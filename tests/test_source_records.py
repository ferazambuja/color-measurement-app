"""Standalone current formats and native export through shared owners, with no acquisition."""

import base64
import hashlib
import json
import os
from dataclasses import asdict, replace

import pytest
from test_desktop_session import create, measure, send
from test_operation_exports import result as operation_fixture
from test_spectral_exports import reflective_result

from color_measurement import document_mat, exports, operation_exports, source_records
from color_measurement.analysis_exports import AnalysisExportError
from color_measurement.dataset_binding import DatasetBinding
from color_measurement.demo import demo_frontends
from color_measurement.desktop_session import DesktopSession
from color_measurement.operation_records import result_to_json
from color_measurement.presentation import DEFAULT_PREVIEW_SCALE


@pytest.fixture
def result():
    return operation_fixture.__wrapped__()


def payload(record, format):
    if format == "operation_json":
        return result_to_json(record).encode()
    if format == "operation_mat":
        return operation_exports.to_mat(record)
    observation = record.patches[0].observation
    if format == "observation_json":
        return exports.to_json(observation).encode()
    if format == "observation_mat":
        return exports.to_mat(observation)
    return exports.to_csv(observation).encode()


def opened(tmp_path, record, format="operation_json"):
    path = tmp_path / "source.bin"
    path.write_bytes(payload(record, format))
    session = DesktopSession([])
    session.open_record(path, format=format, expected_binding_id=None, expected_generation=0)
    return session, path


def export_params(session):
    data = session.dataset()
    return dict(binding_id=data["binding_id"], generation=data["generation"],
                dataset_revision=data["dataset_revision"])


@pytest.mark.parametrize("format", source_records.FORMATS)
def test_standalone_current_formats_preserve_native_identity_and_exact_original_bytes(
    tmp_path, result, format,
):
    session, path = opened(tmp_path, result, format)
    source = session.snapshot()["dataset_binding"]["source"]
    original = result if format.startswith("operation") else result.patches[0].observation
    assert source["operation_id"] == original.operation_id
    assert source["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not {"batch_id", "context_id", "descriptor"} & source.keys()
    data = session.dataset()
    expected = len(result.patches) if format.startswith("operation") else 1
    assert len(data["readings"]) == expected
    row = next(iter(data["readings"].values()))
    assert row["observation"] == asdict(result.patches[0].observation)
    assert row["saved"] and not data["drafts"] and session.batch is None
    capture, _, originals = session._binding.capture()
    assert capture["record"] == asdict(original)
    assert base64.b64decode(capture["original_file"]["data"]) == path.read_bytes()
    assert originals == {path.name: path.read_bytes()}
    assert "manifest" not in capture
    params = export_params(session)
    native_format = "json" if format.startswith("operation") else "csv"
    saved = session.export_record(tmp_path / "exported.bin", format=native_format, **params)
    exported = operation_exports.load(saved["path"]) if native_format == "json" \
        else exports.from_csv((tmp_path / "exported.bin").read_text())
    assert exported == original


def test_standalone_failed_partial_and_zero_row_outcomes_do_not_become_successful(tmp_path, result):
    partial = replace(result, status="failed", error="Original failure", patches=result.patches[:1],
                      instrument_state="unknown", software_stopped=False)
    session, _ = opened(tmp_path, partial)
    source = session.snapshot()["dataset_binding"]["source"]
    assert source["operation_status"] == "failed"
    assert source["operation_reason"] == "Original failure"
    assert source["software_stopped"] is False and source["instrument_state"] == "unknown"
    assert len(session.dataset()["readings"]) == 1 and session.batch is None
    path = tmp_path / "empty.json"
    empty = replace(partial, patches=())
    path.write_text(result_to_json(empty))
    previous = session.snapshot()["dataset_binding"]
    session.open_record(path, format="operation_json", expected_binding_id=previous["id"],
                        expected_generation=previous["generation"])
    assert not session.dataset()["readings"]
    saved = session.export_record(
        tmp_path / "empty-out.json", format="json", **export_params(session),
    )
    assert operation_exports.load(saved["path"]) == empty
    assert saved["ti3"]["unavailable"]


@pytest.mark.parametrize("change", ["missing", "bytes", "replacement"])
def test_standalone_source_loss_change_or_same_byte_inode_replacement_refuses_consumption(
    tmp_path, result, change,
):
    session, path = opened(tmp_path, result)
    params = export_params(session)
    before = path.read_bytes()
    if change == "bytes":
        path.write_bytes(before + b" ")
    elif change == "replacement":
        replacement = tmp_path / "replacement"
        replacement.write_bytes(before)
        os.replace(replacement, path)
    else:
        path.unlink()
    assert not session.snapshot()["dataset_binding"]["available"]
    with pytest.raises((OSError, ValueError)):
        session.dataset()
    target = tmp_path / "no-export.json"
    with pytest.raises((OSError, ValueError)):
        session.export_record(target, format="json", **params)
    assert not target.exists()
    assert session.activate_live(expected_binding_id=params["binding_id"],
                                 expected_generation=params["generation"])


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_standalone_admission_refuses_nonordinary_source_paths(tmp_path, result, kind):
    actual = tmp_path / "original"
    actual.write_bytes(payload(result, "operation_json"))
    path = tmp_path / "selected"
    if kind == "symlink":
        path.symlink_to(actual)
    elif kind == "hardlink":
        os.link(actual, path)
    else:
        path.mkdir()
    with pytest.raises(OSError):
        DatasetBinding(source_file=path, format="operation_json")


def test_standalone_size_and_wrong_explicit_format_refuse_before_fake_record_creation(
    tmp_path, result, monkeypatch,
):
    path = tmp_path / "source"
    path.write_bytes(payload(result, "observation_mat"))
    with pytest.raises(ValueError, match="fields"):
        DatasetBinding(source_file=path, format="operation_mat")
    with pytest.raises(ValueError, match="kind"):
        exports.from_mat(document_mat.to_mat(dict(value=0), kind="analysis"))
    with pytest.raises(ValueError, match="explicit"):
        DatasetBinding(source_file=path, format="vendor_csv")
    monkeypatch.setattr(source_records, "MAX_SOURCE_BYTES", 8)
    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        DatasetBinding(source_file=path, format="observation_mat")


def test_standalone_csv_refuses_conflicting_convenience_column_and_preserves_prior_binding(
    tmp_path, result,
):
    session, _ = opened(tmp_path, result)
    before, data = session.snapshot(), session.dataset()
    path = tmp_path / "conflicting.csv"
    path.write_bytes(exports.to_csv(result.patches[0].observation).replace(
        ",-1,0,105,", ",10,0,105,", 1,
    ).encode("utf-8"))
    with pytest.raises(ValueError, match="disagree"):
        session.open_record(path, format="observation_csv",
                            expected_binding_id=before["dataset_binding"]["id"],
                            expected_generation=before["dataset_binding"]["generation"])
    assert session.snapshot() == before and session.dataset() == data


def test_native_failed_export_retries_retained_original_after_source_loss(tmp_path, result):
    session, source = opened(tmp_path, result, "observation_json")
    target = tmp_path / "occupied.csv"
    target.write_text("Keep unrelated file")
    with pytest.raises(AnalysisExportError) as failure:
        session.export_record(target, format="csv", **export_params(session))
    identity = failure.value.recovery_id
    assert failure.value.payload == exports.to_csv(result.patches[0].observation).encode()
    assert target.read_text() == "Keep unrelated file"
    source.unlink()
    with pytest.raises(ValueError, match="retry_record_export"):
        session.retry_analysis_export(tmp_path / "wrong-retry", recovery_id=identity)
    saved = session.retry_record_export(tmp_path / "retained.csv", recovery_id=identity)
    assert exports.from_csv((tmp_path / "retained.csv").read_text()) == (
        result.patches[0].observation
    )
    assert saved["ti3"] and not session.export_recovery()


@pytest.mark.parametrize("format", ["operation_json", "observation_json"])
def test_native_mat_retry_keeps_prepared_bytes_and_context_without_reserializing(
    tmp_path, result, monkeypatch, format,
):
    session, source = opened(tmp_path, result, format)
    occupied = tmp_path / "occupied.mat"
    occupied.write_bytes(b"Preserve unrelated output")
    with pytest.raises(AnalysisExportError) as caught:
        session.export_record(occupied, format="mat", **export_params(session))
    original = caught.value
    source.unlink()

    def refuse_serialization(record):
        pytest.fail("Retry must publish the retained MAT bytes without serializing again")

    monkeypatch.setattr(exports, "to_mat", refuse_serialization)
    monkeypatch.setattr(operation_exports, "to_mat", refuse_serialization)
    with pytest.raises(AnalysisExportError) as retried:
        session.retry_record_export(occupied, recovery_id=original.recovery_id)
    assert retried.value.payload == original.payload
    assert retried.value.context == original.context
    assert occupied.read_bytes() == b"Preserve unrelated output"
    target = tmp_path / "retained.mat"
    saved = session.retry_record_export(target, recovery_id=original.recovery_id)
    assert target.read_bytes() == original.payload
    assert saved["context"] == original.context and saved["ti3"]
    reader = operation_exports.from_mat if format == "operation_json" else exports.from_mat
    expected = result if format == "operation_json" else result.patches[0].observation
    assert reader(target.read_bytes()) == expected
    assert not session.export_recovery()


def test_standalone_observation_spect_preserves_original_without_manufactured_operation(tmp_path):
    record = reflective_result()
    session, _ = opened(tmp_path, record, "observation_json")
    params = export_params(session)
    capabilities = session.request("record_formats", dict(params, reading_ref=None))
    assert next(row for row in capabilities["formats"] if row["format"] == "sp")["available"]
    assert next(row for row in capabilities["ti3"] if row["component"] == "spectrum")["available"]
    saved = session.export_record(tmp_path / "spectrum.sp", format="sp",
                                  canonical_path=tmp_path / "original.json", **params)
    assert exports.from_json((tmp_path / "original.json").read_text()) == (
        record.patches[0].observation
    )
    spectrum = (tmp_path / "spectrum.sp").read_text()
    assert "CM_OPERATION_STATUS" not in spectrum and "NUMBER_OF_SETS 1" in spectrum
    assert "single original observation" in spectrum and saved["ti3"]


def test_standalone_spect_companion_failure_retains_actual_payload_kind_and_retries_pair(tmp_path):
    record = reflective_result()
    session, source = opened(tmp_path, record, "observation_json")
    with pytest.raises(AnalysisExportError) as caught:
        session.export_record(tmp_path / "missing/spectrum.sp", format="sp",
                              canonical_path=tmp_path / "missing/original.json",
                              **export_params(session))
    error = caught.value
    assert error.format == "json" and error.path.name == "original.json"
    assert error.payload == exports.to_json(record.patches[0].observation).encode()
    recovery, = session.export_recovery()
    assert recovery["format"] == "json" and recovery["native_format"] == "sp"
    assert recovery["retry_method"] == "retry_record_export"
    source.unlink()
    session.retry_record_export(tmp_path / "retained.sp", recovery_id=error.recovery_id,
                                canonical_path=tmp_path / "retained.json")
    assert "CM_CANONICAL_FILE \"retained.json\"" in (tmp_path / "retained.sp").read_text()
    assert not session.export_recovery()


def test_renderer_cannot_select_source_or_native_export_paths():
    session = DesktopSession([])
    for method in ("open_record", "export_record", "retry_record_export"):
        with pytest.raises(ValueError):
            session.request(method, dict(path="forbidden-renderer-path"))


def test_standalone_analysis_reuses_native_quantities_and_fences_without_invented_white(
    tmp_path, result,
):
    session, _ = opened(tmp_path, result)
    params = export_params(session)
    analysis_params = dict(params, comparison_version=0, scale=DEFAULT_PREVIEW_SCALE)
    projection = session.request("projection", analysis_params)
    data = session.dataset()
    analysis = session.request("analysis", dict(analysis_params, selected=list(data["readings"])))
    assert projection["binding_id"] == params["binding_id"]
    assert all(value is None for value in projection["lab_by_source"].values())
    assert analysis["dataset_revision"] == params["dataset_revision"]
    assert data == session.dataset()
    with pytest.raises(ValueError, match="revision"):
        session.request("analysis", dict(analysis_params, dataset_revision="stale",
                                          selected=list(data["readings"])))
    with pytest.raises(ValueError, match="batch archive"):
        session.export_batch_mat(tmp_path / "not-a-batch.mat", binding_id=params["binding_id"],
                                  generation=params["generation"])


def test_standalone_open_preserves_actual_live_owner_and_saved_bytes(tmp_path, result):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "live", "Preserved live batch", "Original notes")
    measure(session)
    owner = session.batch
    batch_header, batch_identity, batch_payloads = session._binding.capture()
    assert batch_header["kind"] == "batch"
    session._binding.assert_capture(batch_header, batch_identity, batch_payloads)
    with pytest.raises(ValueError, match="source kind"):
        session._binding.assert_capture(
            {key: value for key, value in batch_header.items() if key != "kind"},
            batch_identity, batch_payloads,
        )
    original_manifest = (tmp_path / "live/batch.json").read_bytes()
    original_data = session.dataset()
    path = tmp_path / "standalone.json"
    path.write_text(result_to_json(result))
    previous = session.snapshot()["dataset_binding"]
    session.open_record(path, format="operation_json", expected_binding_id=previous["id"],
                        expected_generation=previous["generation"])
    assert session.batch is owner
    assert (tmp_path / "live/batch.json").read_bytes() == original_manifest
    current = session.snapshot()["dataset_binding"]
    with pytest.raises(ValueError, match="read-only"):
        send(session, "finish")
    session.activate_live(expected_binding_id=current["id"],
                          expected_generation=current["generation"])
    assert session.batch is owner
    assert session.dataset()["readings"] == original_data["readings"]


def test_observation_mat_save_failure_retains_exact_binary_payload_and_native_values(
    tmp_path, result,
):
    observation = result.patches[0].observation
    target = tmp_path / "existing.mat"
    target.write_bytes(b"Preserve unrelated content")
    with pytest.raises(exports.SaveError) as caught:
        exports.save(observation, target, format="mat")
    assert caught.value.format == "mat" and exports.from_mat(caught.value.payload) == observation
    assert target.read_bytes() == b"Preserve unrelated content"


def test_spect_reused_companion_refuses_symlinks_without_publishing_or_modifying_source(tmp_path):
    from color_measurement import spectral_exports

    observation = reflective_result().patches[0].observation
    original = tmp_path / "original.json"
    original.write_text(exports.to_json(observation))
    companion = tmp_path / "companion.json"
    companion.symlink_to(original)
    with pytest.raises(exports.SaveError):
        spectral_exports.save_sp(observation, tmp_path / "refused.sp", canonical_path=companion)
    assert not (tmp_path / "refused.sp").exists()
    assert exports.from_json(original.read_text()) == observation and companion.is_symlink()


def test_standalone_capture_authenticates_header_exact_bytes_and_file_identity(tmp_path, result):
    session, _ = opened(tmp_path, result)
    header, identity, payloads = session._binding.capture()
    session._binding.assert_capture(header, identity, payloads)
    changed = dict(header, record={})
    with pytest.raises(ValueError, match="differs"):
        session._binding.assert_capture(changed, identity, payloads)
    with pytest.raises(ValueError, match="differs"):
        session._binding.assert_capture(header, (identity[0], identity[1] + 1), payloads)
    with pytest.raises(ValueError, match="differs"):
        session._binding.assert_capture(header, identity, {"source.bin": b"changed"})


def test_standalone_operation_mat_keeps_exact_embedded_json_separate_from_file_hash(
    tmp_path, result, monkeypatch,
):
    original = json.dumps(json.loads(result_to_json(result)), indent=1) + "\n\n"
    # The actual writer retains a controlled whitespace variant of the same
    # validated record. Its ordinary native inspection view remains unchanged.
    monkeypatch.setattr(operation_exports, "result_to_json", lambda value: original)
    path = tmp_path / "source.mat"
    path.write_bytes(operation_exports.to_mat(result))
    record = source_records.StandaloneRecord(path, format="operation_mat")
    header, _, _ = record.capture()
    native = header["native_input"]
    assert native["canonical_json"] == original and record.record == result
    assert native["canonical_json_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert native["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert native["canonical_json_sha256"] != native["source_sha256"]


def test_standalone_import_catalogue_reports_current_formats_and_actual_mat_availability(
    monkeypatch,
):
    def missing():
        raise ImportError("Declared unavailable MAT dependency")

    monkeypatch.setattr(operation_exports, "_mat_dependencies", missing)
    catalogue = DesktopSession([]).request("formats", {})["standalone_imports"]
    assert [row["format"] for row in catalogue] == list(source_records.FORMATS)
    for row in catalogue:
        assert row["available"] is (not row["format"].endswith("_mat"))
        assert row["reason"] == ("MAT files are unavailable in this app build."
                                 if row["format"].endswith("_mat") else None)
