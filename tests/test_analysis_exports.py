"""Native and derived export readback, input fencing and publication failure."""

import base64
import csv
import hashlib
import io
import json
from uuid import uuid4

import pytest
from test_dataset_binding import open_saved
from test_dataset_binding import saved as _saved_fixture
from test_desktop_session import create, measure, query, send, wait
from test_operation_exports import result as _operation_fixture

from color_measurement import analysis_exports
from color_measurement.analysis_exports import AnalysisExportError
from color_measurement.demo import demo_frontends
from color_measurement.desktop_session import DesktopSession
from color_measurement.document_mat import from_mat
from color_measurement.exports import _CSV_LOCK, _LOCK_NAMES

# Register the existing component fixture without another source producer.
saved = _saved_fixture
operation_record = _operation_fixture


def csv_rows(text):
    # Complete source/context cells can exceed the stdlib's default 128 KiB.
    # Match the existing bounded-to-input reader discipline and restore its limit.
    with _CSV_LOCK:
        previous = csv.field_size_limit()
        try:
            csv.field_size_limit(max(previous, len(text)))
            return list(csv.DictReader(io.StringIO(text)))
        finally:
            csv.field_size_limit(previous)


def export_params(session, *, scope="selected", selected=None, format="json"):
    data = session.dataset()
    binding = session.snapshot()["dataset_binding"]
    return dict(
        binding_id=binding["id"], generation=binding["generation"],
        dataset_revision=data["dataset_revision"],
        comparison_version=binding["comparison"]["version"],
        scope=scope, selected=list(data["readings"])[:1] if selected is None else selected,
        scale=100, format=format,
    )


def original_document(snapshot, record):
    reference = record["operation_document"] or record["native_input"]["document_sha256"]
    payload = snapshot["source_documents"][reference]["canonical_json"].encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == reference
    assert "canonical_operation_json" not in record
    return payload


@pytest.mark.parametrize("selection_index", [0, 1])
def test_json_selected_keeps_exact_inputs_full_notes_and_outside_roles(
    saved, tmp_path, selection_index,
):
    folder, _, _, pairs = saved
    session = DesktopSession([])
    open_saved(session, folder)
    keys = list(session.dataset()["readings"])
    key = keys[selection_index]
    params = export_params(session, selected=[key])
    target = tmp_path / "selected.json"
    before = {path.name: path.read_bytes() for path in folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES}
    receipt = session.export_analysis(target, **params)
    snapshot = json.loads(target.read_bytes())
    record, = snapshot["records"]
    assert record["reading_id"] == key
    assert {field: record[field] for field in ("operation_id", "observation_id")} == \
        pairs[selection_index]
    raw = original_document(snapshot, record)
    assert hashlib.sha256(raw).hexdigest() == record["operation_sha256"]
    assert raw in before.values()
    annotation = next(item for item in json.loads(before["batch.json"])["annotations"]
                      if item["operation_id"] == pairs[selection_index]["operation_id"])
    assert record["notes"] == annotation["notes"]
    assert len(snapshot["role_inputs"]) == 2
    assert snapshot["role_inputs"]["reference"]["selected"] is (selection_index == 0)
    assert snapshot["role_inputs"]["white"]["selected"] is (selection_index == 1)
    assert snapshot["source"]["manifest_sha256"] == hashlib.sha256(before["batch.json"]).hexdigest()
    assert receipt["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    json.dumps(receipt, allow_nan=False)  # Native reply is JSON, never raw payload bytes.
    assert {path.name: path.read_bytes() for path in folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES} == before


def test_csv_all_retains_signed_native_values_full_json_and_escaped_text(saved, tmp_path):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    target = tmp_path / "all.csv"
    session.export_analysis(target, **export_params(
        session, scope="all", selected=[], format="csv",
    ))
    rows = csv_rows(target.read_text())
    assert len(rows) == 2
    common = json.loads(rows[0]["source_documents_json"])
    assert rows[1]["source_documents_json"] == ""
    assert len(common["source_documents"]) == 2
    assert common["original_file"] is None
    for row in rows:
        record = json.loads(row["record_json"])
        native = record["observation"]["backend_reported"]["xyz"]
        assert float(row["native_Y"]) == native["values"][1]
        assert row["native_xyz_unit"] == native["unit"]
        assert json.loads(row["derived_json"]) == record["derived"]
        assert json.loads(row["export_context_json"])["source"]["manifest_sha256"] == \
            hashlib.sha256((folder / "batch.json").read_bytes()).hexdigest()
        assert original_document(common, record) in {path.read_bytes() for path in folder.iterdir()
            if path.is_file() and path.name not in _LOCK_NAMES}
    assert rows[0]["name"] == "'=Retained label"
    assert len(json.loads(rows[0]["record_json"])["notes"]) > 256


@pytest.mark.parametrize("change", ["binding", "generation", "revision", "comparison", "format",
                                     "selection", "all-selection", "scale"])
def test_export_refuses_stale_or_invalid_request_before_file_creation(saved, tmp_path, change):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    params = export_params(session)
    if change == "binding":
        params["binding_id"] = "foreign"
    elif change == "generation":
        params["generation"] -= 1
    elif change == "revision":
        params["dataset_revision"] = "stale"
    elif change == "comparison":
        params["comparison_version"] += 1
    elif change == "format":
        params["format"] = "unsupported"
    elif change == "selection":
        params["selected"] *= 2
    elif change == "all-selection":
        params["scope"] = "all"
    else:
        params["scale"] = 0
    target = tmp_path / "refused.json"
    with pytest.raises(ValueError):
        session.export_analysis(target, **params)
    assert not target.exists()


def test_source_change_between_preparation_and_publication_refuses_file(
    saved, tmp_path, monkeypatch,
):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    prepare = analysis_exports.prepare_analysis

    def changed_after_prepare(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        path = folder / "batch.json"
        path.write_bytes(path.read_bytes() + b"\n")
        return prepared

    monkeypatch.setattr(analysis_exports, "prepare_analysis", changed_after_prepare)
    target = tmp_path / "changed.json"
    with pytest.raises(ValueError, match="source bytes changed"):
        session.export_analysis(target, **export_params(session))
    assert not target.exists()


def test_failed_publication_retains_payload_and_never_overwrites_existing_file(saved, tmp_path):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    target = tmp_path / "owned-by-other.json"
    target.write_bytes(b"Unrelated existing file")
    with pytest.raises(AnalysisExportError) as caught:
        session.export_analysis(target, **export_params(session))
    assert target.read_bytes() == b"Unrelated existing file"
    retained = json.loads(caught.value.payload)
    assert len(retained["records"]) == 1 and caught.value.path == target
    assert caught.value.publication is None


def test_renderer_cannot_select_an_export_path(saved, tmp_path):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    target = tmp_path / "renderer.json"
    with pytest.raises(ValueError):
        session.request("export_analysis", {"path": str(target)})
    assert not target.exists()


def test_live_export_uses_saved_current_inputs_and_retains_exact_source(tmp_path):
    session = DesktopSession(demo_frontends())
    packet = create(session, tmp_path / "live-source", "Live export", "")
    measure(session)
    measure(session)
    keys = list(session.dataset()["readings"])
    data = session.dataset()
    reference = data["readings"][keys[0]]["observation"]
    pair = {key: reference[key] for key in ("operation_id", "observation_id")}
    binding = session.snapshot()["dataset_binding"]
    assert query(session, "set_analysis_context", dict(
        command_id=str(uuid4()), comparison_version=0,
        reference=pair, white=dict(pair, kind="retained-reading"),
    ))["admission"] == "admitted"
    wait(session, lambda state: state["phase"] == "idle")
    target = tmp_path / "live.json"
    session.export_analysis(target, **export_params(session, selected=keys[1:]))
    result = json.loads(target.read_bytes())
    assert result["binding_id"] == binding["id"]
    assert result["records"][0]["derived"]["xyz_comparison"]["y_percent"] is not None
    assert result["source"]["batch_id"] == packet["batch"]["batch_id"]
    assert result["role_inputs"]["reference"]["selected"] is False
    finish_id = str(uuid4())
    assert send(session, "finish", identity=finish_id)["admission"] == "admitted"
    wait(session, lambda state: session.batch.command_outcome(finish_id)["state"] != "pending")
    assert session.batch.command_outcome(finish_id)["state"] == "completed"


def test_prepared_export_survives_source_change_and_native_retry_never_recomputes(
    saved, tmp_path, monkeypatch,
):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    original_path = tmp_path / "existing.json"
    original_path.write_bytes(b"Unrelated existing input")
    with pytest.raises(AnalysisExportError) as caught:
        session.export_analysis(original_path, **export_params(session))
    error = caught.value
    pending, = session.request("export_recovery", {})
    assert pending["recovery_id"] == error.recovery_id
    assert pending["payload_sha256"] == hashlib.sha256(error.payload).hexdigest()
    json.dumps(session.snapshot(), allow_nan=False)
    # The retry intentionally saves this earlier prepared snapshot. It is not
    # a newly authenticated/current view after the original source changes.
    manifest = folder / "batch.json"
    manifest.write_bytes(manifest.read_bytes() + b"\n")

    def no_recompute(*args, **kwargs):
        pytest.fail("Prepared retry recalculated an analysis snapshot")

    monkeypatch.setattr(analysis_exports, "prepare_analysis", no_recompute)
    destination = tmp_path / "retained.json"
    receipt = session.retry_analysis_export(destination, recovery_id=error.recovery_id)
    assert destination.read_bytes() == error.payload
    assert receipt["retained_snapshot"] is True
    assert session.request("export_recovery", {}) == []
    assert original_path.read_bytes() == b"Unrelated existing input"
    assert session.snapshot()["dataset_binding"]["available"] is False
    with pytest.raises(ValueError):
        session.request("retry_analysis_export", {"path": str(tmp_path / "renderer.json"),
                                                 "recovery_id": error.recovery_id})


def test_retained_export_discard_requires_exact_payload_and_does_not_change_owner(saved, tmp_path):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    target = tmp_path / "owned.json"
    target.write_bytes(b"Unrelated")
    with pytest.raises(AnalysisExportError):
        session.export_analysis(target, **export_params(session))
    pending, = session.export_recovery()
    data = session.dataset()
    with pytest.raises(ValueError, match="changed"):
        session.discard_analysis_export(recovery_id=pending["recovery_id"],
                                        payload_sha256="foreign")
    assert session.export_recovery() == [pending]
    session.discard_analysis_export(recovery_id=pending["recovery_id"],
                                    payload_sha256=pending["payload_sha256"])
    assert session.export_recovery() == [] and session.dataset() == data


@pytest.mark.parametrize("format", ["json", "csv", "mat"])
def test_signed_color_packet_and_summary_survive_actual_saved_analysis_export(
    saved, tmp_path, format,
):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    params = export_params(session, selected=list(session.dataset()["readings"])[1:],
                           format=format)
    expected = query(session, "analysis", {key: params[key] for key in (
        "dataset_revision", "comparison_version", "selected", "scale",
    )})
    target = tmp_path / ("signed." + format)
    session.export_analysis(target, **params)
    if format == "csv":
        row, = csv_rows(target.read_text())
        record = json.loads(row["record_json"])
        context = json.loads(row["export_context_json"])
        assert json.loads(row["derived_json"])["color_comparison"] == record["derived"][
            "color_comparison"]
        for column, metric in (("delta_XYZ_json", "delta_xyz"), ("delta_xy_json", "delta_xy"),
                               ("delta_uv_json", "delta_uv"), ("delta_Lab_json", "delta_lab")):
            assert json.loads(row[column]) == record["derived"]["color_comparison"][metric]
        for metric in ("delta_chroma", "delta_hue"):
            value = record["derived"]["color_comparison"][metric]
            assert row[metric] == "" if value is None else float(row[metric]) == value
    else:
        snapshot = (from_mat(target.read_bytes(), kind="analysis") if format == "mat" else
                    json.loads(target.read_bytes()))
        record, = snapshot["records"]
        context = snapshot
    packet = record["derived"]["color_comparison"]
    assert packet == json.loads(json.dumps(expected["color_comparison_by_source"][
        record["reading_id"]]))
    assert packet["delta_xyz"] is not None and packet["delta_lab"] is not None
    assert record["derived"]["delta_e_2000"] == packet["delta_e2000"]
    assert context["summary"] == json.loads(json.dumps(expected["summary"]))
    assert context["methods"]["color_comparison"] == "processing.compare_color"


@pytest.mark.parametrize("method", ["characterized_reflectance", "relight_reflectance"])
def test_reference_factor_export_retains_outside_original_and_explicit_characterization(
    saved, tmp_path, method,
):
    from test_qualified_science import derive, ratio_context, relight_context

    from color_measurement.dataset_binding import content_digest

    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    before = session.dataset()
    reference_key, sample_key = list(before["readings"])
    reference = before["readings"][reference_key]
    query(session, "set_analysis_context", dict(
        command_id=str(uuid4()), comparison_version=1, reference=None, white=None,
    ))
    source_bytes = {path.name: path.read_bytes() for path in folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES}
    grid = reference["observation"]["backend_reported"]["spectrum"]["wavelengths_nm"]
    characterization = dict(wavelengths_nm=grid, values=[0.8] * len(grid),
                            quantity="reflectance", unit="1")
    context = ratio_context(
        reference["reading_ref"], assume_ideal_white=False, characterization=characterization,
        characterization_id="Declared software-only reference characterization",
    )
    request = dict(method=method, context=(context if method == "characterized_reflectance" else
        relight_context(reflectance=dict(kind="characterized-reference", context=context))))
    reply = derive(session, request, selected=[sample_key])
    result = reply["by_source"][sample_key]["result"]
    assert reply["by_source"][sample_key]["reason"] is None
    factor = result if method == "characterized_reflectance" else result["reference_factor"]
    assert factor["characterization_id"] == context["characterization_id"]
    assert factor["characterization_sha256"] == content_digest(characterization)
    target = tmp_path / (method + ".json")
    session.export_analysis(target, **export_params(session, selected=[sample_key]),
                            derivation=request)
    exported = json.loads(target.read_bytes())
    role = exported["role_inputs"]["reflectance_reference"]
    assert set(exported["role_inputs"]) == {"reflectance_reference"}
    assert exported["selection"] == [sample_key] and role["selected"] is False
    assert role["reading_id"] == reference_key and role["reading_ref"] == reference["reading_ref"]
    assert role["observation"] == json.loads(json.dumps(reference["observation"]))
    assert role["observation_sha256"] == content_digest(reference["observation"])
    original = original_document(exported, role)
    assert original in source_bytes.values()
    assert hashlib.sha256(original).hexdigest() == role["operation_sha256"]
    assert exported["derived_science"] == json.loads(json.dumps({
        key: value for key, value in reply.items() if key not in {"binding_id", "generation"}
    }))
    assert exported["derived_science"]["request"] == json.loads(json.dumps(request))
    assert exported["derived_science"]["context_sha256"] == content_digest(request)
    record, = exported["records"]
    assert record["observation"] == json.loads(json.dumps(
        before["readings"][sample_key]["observation"]))
    assert session.dataset() == before
    assert {path.name: path.read_bytes() for path in folder.iterdir()
            if path.is_file() and path.name not in _LOCK_NAMES} == source_bytes


@pytest.mark.parametrize("input_format,output_format", [
    ("operation_json", "json"), ("operation_mat", "mat"),
    ("observation_json", "json"), ("observation_mat", "csv"), ("observation_csv", "mat"),
])
def test_actual_standalone_analysis_keeps_exact_originals_without_fabricated_batch(
    operation_record, tmp_path, input_format, output_format,
):
    from dataclasses import asdict

    from test_source_records import opened

    session, path = opened(tmp_path, operation_record, input_format)
    original_bytes = path.read_bytes()
    data = session.dataset()
    original = (operation_record if input_format.startswith("operation") else
                operation_record.patches[0].observation)
    target = tmp_path / ("standalone." + output_format)
    receipt = session.export_analysis(target, **export_params(
        session, scope="all", selected=[], format=output_format,
    ))
    if output_format == "csv":
        rows = csv_rows(target.read_text())
        snapshot = json.loads(rows[0]["export_context_json"])
        snapshot.update(json.loads(rows[0]["source_documents_json"]))
        snapshot["records"] = [json.loads(row["record_json"]) for row in rows]
        assert all(row["source_documents_json"] == "" for row in rows[1:])
    else:
        snapshot = (from_mat(target.read_bytes(), kind="analysis") if output_format == "mat" else
                    json.loads(target.read_bytes()))
    assert snapshot["source"]["kind"] == "standalone"
    assert snapshot["source"]["format"] == input_format
    assert not {"batch_id", "context_id", "descriptor", "manifest_sha256"} & \
        snapshot["source"].keys()
    assert receipt["source"] == snapshot["source"]
    assert len(snapshot["source_documents"]) == 1
    assert base64.b64decode(snapshot["original_file"]["data"]) == original_bytes
    assert snapshot["original_file"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    native_document = json.loads(original_document(snapshot, snapshot["records"][0]))
    assert native_document == json.loads(json.dumps(asdict(original)))
    for record in snapshot["records"]:
        assert record["operation_id"] == original.operation_id
        assert record["native_input"]["source_sha256"] == snapshot["original_file"]["sha256"]
        assert record["native_input"]["format"] == input_format
        assert record["notes"] == "" and record["repeat_of"] is None
        assert record["observation"] == json.loads(json.dumps(
            data["readings"][record["reading_id"]]["observation"]))
        if input_format.startswith("observation"):
            assert record["operation_sha256"] is record["operation_status"] is None
            assert record["operation_document"] is None
        else:
            assert record["operation_status"] == original.status
            assert record["operation_sha256"] == hashlib.sha256(original_bytes).hexdigest()
            assert record["operation_document"] == record["native_input"]["document_sha256"]
    assert session.batch is None and session.dataset() == data
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("workflow,persona,mode,layout", [
    ("xy", "spectroscan", "reflective_xy", dict(passes=4, steps=4)),
    ("chart", "dtp20", "saved_chart", dict(passes=4, steps=4, sheets=[4])),
])
def test_actual_multipatch_batch_csv_keeps_one_original_document_and_compact_context(
    tmp_path, workflow, persona, mode, layout,
):
    from test_application import _automatic_companion_state, _command, _demo_frontend, _wait

    from color_measurement.application import BatchApplication
    from color_measurement.dataset_binding import DatasetBinding

    owner = BatchApplication(tmp_path / "multipatch", frontend=_demo_frontend(persona),
                             mode=mode, options={})
    try:
        state = owner.bind()
        draft = state["draft"]
        identity = str(uuid4())
        outcome = _command(owner, "run_workflow", dict(
            context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            workflow=workflow, patch_ids=[f"synthetic-{index}" for index in range(16)],
            layout=layout,
        ), identity=identity)
        assert outcome["admission"] == "admitted", outcome
        _wait(owner, lambda packet: packet["phase"] == "idle", respond=True)
        _automatic_companion_state(owner)
        binding = DatasetBinding(owner=owner)
        data = binding.data()
        assert len(data["readings"]) == 16
        snapshot, header, witness, payloads = analysis_exports.prepare_analysis(
            binding, dataset_revision=data["dataset_revision"], comparison_version=0,
            scope="all", selected=[], scale=100,
        )
        binding.assert_capture(header, witness, payloads)
        assert len(snapshot["source_documents"]) == 1
        document = next(iter(snapshot["source_documents"]))
        assert {record["operation_document"] for record in snapshot["records"]} == {document}
        csv_payload = analysis_exports.analysis_csv(snapshot)
        rows = csv_rows(csv_payload.decode("utf-8"))
        assert len(rows) == 16
        common = json.loads(rows[0]["source_documents_json"])
        assert common["source_documents"] == snapshot["source_documents"]
        assert all(row["source_documents_json"] == "" for row in rows[1:])
        for row in rows:
            record = json.loads(row["record_json"])
            context = json.loads(row["export_context_json"])
            assert "canonical_operation_json" not in record
            assert not {"source_documents", "original_file"} & context.keys()
            assert record["operation_document"] == document
        exact = snapshot["source_documents"][document]["canonical_json"].encode()
        assert exact in payloads.values() and hashlib.sha256(exact).hexdigest() == document
        # The large original document belongs only to the first common cell;
        # independent per-reading display derivations have their own size.
        assert sum(len(row["source_documents_json"]) for row in rows) == len(
            rows[0]["source_documents_json"])
        assert common["source_documents"][document]["canonical_json"] == exact.decode()
    finally:
        if owner.snapshot()["phase"] == "idle":
            close_id = str(uuid4())
            _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]),
                     identity=close_id)
            _wait(owner, lambda packet: owner.command_outcome(close_id)["state"] != "pending")


@pytest.mark.parametrize("kind", ["scalar", "timing"])
def test_standalone_noncolor_operation_analysis_preserves_native_quantities(
    tmp_path, kind,
):
    from dataclasses import asdict

    from test_display_timing_frontend import timing_result
    from test_refresh_frequency import outcome

    from color_measurement.operation_records import result_to_json

    operation = outcome() if kind == "scalar" else timing_result()
    observation = operation.patches[0].observation
    path = tmp_path / "native.json"
    original = result_to_json(operation).encode("utf-8")
    path.write_bytes(original)
    session = DesktopSession([])
    session.open_record(path, format="operation_json", expected_binding_id=None,
                        expected_generation=0)
    target = tmp_path / "analysis.json"
    session.export_analysis(target, **export_params(session, scope="all", selected=[]))
    snapshot = json.loads(target.read_bytes())
    record, = snapshot["records"]
    assert record["record_kind"] == kind and record["operation_status"] == operation.status
    assert "xyz" not in record["observation"]["backend_reported"]
    assert record["observation"] == json.loads(json.dumps(asdict(observation)))
    assert record["derived"]["color_comparison"]["delta_xyz"] is None
    assert record["derived"]["lab"] is None
    assert original_document(snapshot, record) == original
    assert path.read_bytes() == original


def test_standalone_analysis_failed_publication_retries_exact_prepared_source_after_loss(
    operation_record, tmp_path, monkeypatch,
):
    from test_source_records import opened

    session, source = opened(tmp_path, operation_record, "operation_json")
    target = tmp_path / "occupied.json"
    target.write_bytes(b"Unrelated existing file")
    with pytest.raises(AnalysisExportError) as caught:
        session.export_analysis(target, **export_params(session, scope="all", selected=[]))
    failed = caught.value
    snapshot = json.loads(failed.payload)
    assert len(snapshot["source_documents"]) == 1
    assert snapshot["source"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    source.unlink()
    monkeypatch.setattr(analysis_exports, "prepare_analysis", lambda *args, **kwargs:
                        pytest.fail("Retained standalone retry recomputed source"))
    destination = tmp_path / "retained.json"
    receipt = session.retry_analysis_export(destination, recovery_id=failed.recovery_id)
    assert destination.read_bytes() == failed.payload and receipt["retained_snapshot"]
    assert receipt["source"] == snapshot["source"]
    assert target.read_bytes() == b"Unrelated existing file"
