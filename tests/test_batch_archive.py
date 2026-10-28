"""Whole current batch MAT retains source bytes and reopens with no writable owner."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from test_analysis_exports import csv_rows
from test_dataset_binding import open_saved
from test_dataset_binding import saved as _saved_fixture
from test_desktop_session import completed_command, create, measure

from color_measurement import batch_archive, operation_exports
from color_measurement.application import review_captured, review_saved
from color_measurement.dataset_binding import DatasetBinding
from color_measurement.demo import demo_frontends
from color_measurement.desktop_session import DesktopSession
from color_measurement.document_mat import from_mat, to_mat
from color_measurement.exports import _LOCK_NAMES, to_json

saved = _saved_fixture


@pytest.fixture
def continued_chart(tmp_path):
    from test_application import _wait
    from test_application_chart import _command, _configure, _demo_batch, _measure

    parent = _demo_batch(tmp_path / "chart-parent")
    child = _demo_batch(tmp_path / "chart-child")
    parent.bind()
    child.bind()
    for owner in (parent, child):
        _wait(owner, lambda state: state["phase"] == "idle" and not state["companions"]["saving"])
    _configure(parent, 1)
    _measure(parent)
    original = review_saved(parent._folder)
    outcome = _command(child, "continue_chart", dict(
        folder=str(parent._folder), source_sha256=original["manifest_sha256"],
        action="new-pass", source_pass_id=original["manifest"]["chart"]["active_pass"],
        acknowledged_material=[],
    ))
    assert outcome["admission"] == "admitted", outcome
    _measure(child)
    for owner in (parent, child):
        _wait(owner, lambda state: state["phase"] == "idle" and not state["companions"]["saving"])
    yield parent, child
    for owner in (parent, child):
        if owner.snapshot()["status"] == "open":
            assert _command(owner, "finish_chart", dict(
                chart_revision=owner.snapshot()["chart"]["revision"], allow_incomplete=True,
            ))["admission"] in {"admitted", "no_change"}


def test_continued_chart_mat_retains_complete_authenticated_parent_closure(
    continued_chart, tmp_path,
):
    from test_application import _command, _wait

    parent, child = continued_chart
    before = review_saved(child._folder)
    assert before["chart"]["available"] and len(before["inherited_operations"]) == 1
    original_files = {path.name: path.read_bytes() for path in child._folder.iterdir()
                      if path.is_file() and path.name not in _LOCK_NAMES}
    payload, source = batch_archive.prepare(DatasetBinding(owner=child))
    archive = from_mat(payload, kind="batch")
    assert source["manifest_sha256"] == before["manifest_sha256"]
    for item in before["chart"]["source_files"]:
        assert archive["files"][item["path"]]["sha256"] == item["sha256"]
    # Finish the producer normally before moving its folder. Windows refuses
    # moving a directory containing a live writer's kernel-lock handle.
    assert _command(parent, "finish_chart", dict(
        chart_revision=parent.snapshot()["chart"]["revision"], allow_incomplete=True,
    ))["admission"] == "admitted"
    _wait(parent, lambda state: state["status"] == "finished"
          and not state["companions"]["saving"])
    # The portable archive must not depend on the producer folders remaining.
    offline_parent = tmp_path / "parent-offline"
    parent._folder.rename(offline_parent)
    target = tmp_path / "continued.mat"
    target.write_bytes(payload)
    reopened = DatasetBinding(folder=target)
    try:
        actual = reopened._review
        assert actual["chart"] == before["chart"]
        assert actual["manifest"] == before["manifest"]
        assert actual["inherited_operations"] == before["inherited_operations"]
        assert actual["operations"] == before["operations"]
        for item in before["chart"]["source_files"]:
            assert (reopened._folder / item["path"]).read_bytes() == original_files[item["path"]]
    finally:
        reopened.close()
        offline_parent.rename(parent._folder)


def test_automatic_capture_serializers_reuse_mat_closure_and_selected_original_refs(
    continued_chart,
):
    parent, child = continued_chart
    binding = DatasetBinding(owner=child)
    review, _, files = binding.capture()
    prepared = batch_archive.prepare_from_capture(review, files)
    assert prepared["source_manifest_sha256"] == review["manifest_sha256"]
    assert all(prepared[key]["reason"] is None for key in ("mat", "csv", "chart_selected_csv"))
    assert from_mat(prepared["mat"]["payload"], kind="batch")["files"].keys() == files.keys()
    rows = csv_rows(prepared["csv"]["payload"].decode("utf-8"))
    assert len(rows) == 2 and {row["segment_id"] for row in rows} == {
        parent.snapshot()["batch_id"], child.snapshot()["batch_id"],
    }
    selected = csv_rows(prepared["chart_selected_csv"]["payload"].decode("utf-8"))
    wrapper, = child.chart_readings()
    assert len(selected) == 2
    assert [row["pass_id"] for row in selected] == [
        item["id"] for item in review["manifest"]["chart"]["passes"]
    ]
    assert json.loads(selected[-1]["reading_ref_json"]) == wrapper["reading_ref"]
    assert int(selected[-1]["cell_index"]) == wrapper["cell_index"]
    inherited_pass = review["manifest"]["chart"]["passes"][0]["id"]
    inherited, = csv_rows(batch_archive.native_csv(
        review, selected_passes=[inherited_pass],
    ).decode("utf-8"))
    original, = child.chart_readings(inherited_pass)
    assert json.loads(inherited["reading_ref_json"]) == original["reading_ref"]
    assert inherited["source_manifest_sha256"] == review["inherited_operations"][0][
        "source_manifest_sha256"
    ]
    assert selected[0] == inherited
    assert json.loads(selected[0]["observation_json"]) == json.loads(
        to_json(original["observation"]),
    )
    assert all(row["pass_id"] == "" for row in rows)


def test_automatic_mat_dependency_refusal_leaves_native_csv_available(saved, monkeypatch):
    folder, _, _, _ = saved
    binding = DatasetBinding(folder=folder)
    review, _, files = binding.capture()

    def unavailable(*args, **kwargs):
        raise ImportError("Synthetic missing MAT runtime")

    monkeypatch.setattr(batch_archive, "to_mat", unavailable)
    prepared = batch_archive.prepare_from_capture(review, files)
    # Shown in the workspace: the operator reason, not the raised install text.
    assert prepared["mat"] == dict(
        payload=None, reason="MAT files are unavailable in this app build.", retryable=False,
    )
    assert prepared["csv"]["reason"] is None
    rows = csv_rows(prepared["csv"]["payload"].decode("utf-8"))
    assert len(rows) == 2 and rows[0]["name"] == "'=Retained label"
    original = review["manifest"]["annotations"][0]
    assert json.loads(rows[0]["annotation_json"]) == original
    assert rows[0]["notes"] == original["notes"]
    assert prepared["chart_selected_csv"] == dict(
        payload=None, reason="This batch has no guided chart", retryable=False,
    )


def test_empty_batch_capture_produces_mat_and_header_only_csv(tmp_path):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "empty-batch", "Empty native source", "")
    binding = DatasetBinding(owner=session.batch)
    review, _, files = binding.capture()
    prepared = batch_archive.prepare_from_capture(review, files)
    assert from_mat(prepared["mat"]["payload"], kind="batch")["files"].keys() == {"batch.json"}
    assert csv_rows(prepared["csv"]["payload"].decode("utf-8")) == []
    assert b"observation_json" in prepared["csv"]["payload"]
    completed_command(session, "finish")


@pytest.mark.parametrize("original", [b"", b"\x00SETUP\xff" + b"x" * 80 + b"LATE FAILURE\xfe"])
@pytest.mark.parametrize("inherited", [False, True])
def test_schema2_diagnostic_originals_survive_current_and_inherited_mat_closure(
    tmp_path, monkeypatch, original, inherited,
):
    from test_application import _command, _demo_batch, _native_diagnostic_seam, _wait
    from test_application_chart import _configure, _measure

    owner = _demo_batch(tmp_path / "diagnostic-parent")
    owner.bind()
    _native_diagnostic_seam(owner, monkeypatch, original, written_bytes=3 if original else None)
    assert _command(owner, "configure_native_diagnostics", dict(
        debug_level=4, limit_bytes=16,
    ))["admission"] == "admitted"
    if inherited:
        _configure(owner, 1)
        _measure(owner)
    else:
        state = owner.snapshot()
        assert _command(owner, "measure", dict(
            context_id=state["context_id"], draft_id=state["draft"]["id"],
            draft_version=state["draft"]["version"],
        ))["admission"] == "admitted"
        _wait(owner, lambda state: state["phase"] == "idle", respond=True)
    parent = review_saved(owner._folder)
    diagnostic = parent["operations"][0]["native_diagnostics"]
    selected = original if len(original) <= 16 else original[:8] + original[-8:]
    assert diagnostic["schema_version"] == 2 and diagnostic["retention"] == "head_tail"
    assert parent["operations"][0]["native_diagnostic_review"]["state"] == "authenticated"
    assert (owner._folder / diagnostic["path"]).read_bytes() == selected
    current = owner
    if inherited:
        current = _demo_batch(tmp_path / "diagnostic-child")
        current.bind()
        assert _command(current, "continue_chart", dict(
            folder=str(owner._folder), source_sha256=parent["manifest_sha256"],
            action="new-pass", source_pass_id=parent["manifest"]["chart"]["active_pass"],
            acknowledged_material=[],
        ))["admission"] == "admitted"
        path = f"source-{diagnostic['sha256']}.json"
    else:
        path = diagnostic["path"]
    binding = DatasetBinding(owner=current)
    review, _, files = binding.capture()
    assert files[path] == selected
    payload, _ = batch_archive.encode(review, files)
    temporary = batch_archive.unpack(payload)
    try:
        from pathlib import Path

        restored = review_saved(temporary.name)
        assert (Path(temporary.name) / path).read_bytes() == selected
        assert restored["operations"] == review["operations"]
        assert restored["inherited_operations"] == review["inherited_operations"]
        assert restored["chart"] == review["chart"]
    finally:
        temporary.cleanup()
        for active in (current, owner) if inherited else (owner,):
            action = "finish_chart" if inherited else "finish"
            operands = dict(chart_revision=active.snapshot()["chart"]["revision"],
                            allow_incomplete=True) if inherited else {}
            assert _command(active, action, operands)["admission"] in {"admitted", "no_change"}


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_chart_mat_refuses_unauthenticated_parent_without_hiding_healthy_local_data(
    continued_chart, damage,
):
    _, child = continued_chart
    source = review_saved(child._folder)["chart"]["source_files"][0]
    path = child._folder / source["path"]
    raw = path.read_bytes()
    try:
        if damage == "missing":
            path.unlink()
        else:
            path.write_bytes(raw + b"changed")
        binding = DatasetBinding(folder=child._folder)
        assert len(binding.data()["readings"]) == 1
        with pytest.raises(ValueError, match="Cannot archive unauthenticated chart sources"):
            batch_archive.prepare(binding)
    finally:
        path.write_bytes(raw)


def test_mat_unpack_refuses_missing_parent_even_when_missing_inventory_is_declared(continued_chart):
    _, child = continued_chart
    payload, _ = batch_archive.prepare(DatasetBinding(owner=child))
    archive = from_mat(payload, kind="batch")
    source = review_saved(child._folder)["chart"]["source_files"][0]["path"]
    del archive["files"][source]
    archive["missing_files"].append(source)
    with pytest.raises(ValueError, match="Archive chart source graph cannot be authenticated"):
        batch_archive.unpack(to_mat(archive, kind="batch"))


@pytest.mark.parametrize("evidence", ["available", "missing", "damaged"])
def test_terminal_unknown_original_recovery_survives_whole_mat_review(
    tmp_path, monkeypatch, evidence,
):
    source = DesktopSession(demo_frontends())
    folder = tmp_path / "terminal-source"
    create(source, folder, "Terminal evidence", "")
    measure(source)
    frontend = source.batch._frontend
    prepare = frontend.prepare

    class InvalidResult:
        def __init__(self, operation):
            self.operation = operation

        def __getattr__(self, name):
            return getattr(self.operation, name)

        def result_json(self):
            result = json.loads(self.operation.result_json())
            result["device"]["serial"] = "Deliberate wrong terminal serial"
            return json.dumps(result)

    monkeypatch.setattr(frontend, "prepare", lambda request: InvalidResult(prepare(request)))
    measure(source)
    completed_command(source, "retry_save")
    completed_command(source, "finish")
    complete = review_saved(folder)
    terminal = complete["operations"][-1]
    assert terminal["review_state"] == "terminal_unknown"
    recovery = terminal["recovery"]
    path = folder / recovery["path"]
    original = path.read_bytes()
    if evidence == "missing":
        path.unlink()
    elif evidence == "damaged":
        path.write_bytes(b"Original damaged recovery bytes")
    before = {path.name: path.read_bytes() for path in folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES}
    expected = review_saved(folder)["operations"][-1]
    reader = DesktopSession([])
    open_saved(reader, folder)
    binding = reader.snapshot()["dataset_binding"]
    target = tmp_path / "terminal.mat"
    reader.export_batch_mat(target, binding_id=binding["id"], generation=binding["generation"])
    document = from_mat(target.read_bytes(), kind="batch")
    if evidence == "missing":
        assert recovery["path"] in document["missing_files"]
    else:
        assert document["files"][recovery["path"]]["sha256"] == hashlib.sha256(
            before[recovery["path"]]).hexdigest()
    reopened = DesktopSession([])
    try:
        open_saved(reopened, target)
        actual = reopened._binding._review["operations"][-1]
        assert actual["review_state"] == expected["review_state"]
        if evidence == "available":
            assert actual["recovery_result"] == terminal["recovery_result"]
            assert (reopened._binding._folder / recovery["path"]).read_bytes() == original
            assert actual["recovery_result"]["software_stopped"] is True
        elif evidence == "damaged":
            assert actual["review_reason"] == expected["review_reason"]
        else:
            assert recovery["path"] in actual["review_reason"]
        assert reopened.batch is None and len(reopened.dataset()["readings"]) == 1
        after = {path.name: path.read_bytes() for path in folder.iterdir()
                    if path.is_file() and path.name not in _LOCK_NAMES}
        assert after == before
        path.write_bytes(original if evidence == "missing" else b"Changed after source capture")
        assert reader.snapshot()["dataset_binding"]["available"] is False
        with pytest.raises(ValueError, match="source.*(changed|appeared)"):
            reader.dataset()
    finally:
        reopened._binding.close()


@pytest.mark.parametrize("field", ["schema_version", "batch_id", "context_id"])
def test_archive_schema_and_declared_source_identities_are_strict(saved, field):
    folder, _, _, _ = saved
    reader = DesktopSession([])
    open_saved(reader, folder)
    payload, _ = batch_archive.prepare(reader._binding)
    document = from_mat(payload, kind="batch")
    if field == "schema_version":
        document[field] = True
    else:
        document["source"][field] = str(uuid4())
    with pytest.raises(ValueError, match="archive|identity"):
        temporary = batch_archive.unpack(to_mat(document, kind="batch"))
        temporary.cleanup()


def test_archive_copies_only_the_declared_canonical_inventory(saved):
    folder, _, _, _ = saved
    reader = DesktopSession([])
    open_saved(reader, folder)
    payload, _ = batch_archive.prepare(reader._binding)
    document = from_mat(payload, kind="batch")
    changed = deepcopy(document)
    changed["files"]["unrelated.json"] = changed["files"]["batch.json"]
    with pytest.raises(ValueError, match="inventory"):
        temporary = batch_archive.unpack(to_mat(changed, kind="batch"))
        temporary.cleanup()


def test_whole_batch_mat_reopens_exact_source_and_retires_its_owned_materialization(
    saved, tmp_path,
):
    folder, _, original, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    before = {path.name: path.read_bytes() for path in folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES}
    binding = session.snapshot()["dataset_binding"]
    target = tmp_path / "complete.mat"
    receipt = session.export_batch_mat(target, binding_id=binding["id"],
                                       generation=binding["generation"])
    assert receipt["export_kind"] == "batch" and receipt["format"] == "mat"
    archive = from_mat(target.read_bytes(), kind="batch")
    assert archive["source"]["manifest_sha256"] == hashlib.sha256(before["batch.json"]).hexdigest()
    reopened = DesktopSession([])
    open_saved(reopened, target)
    data = reopened.dataset()
    assert reopened.batch is None and data["readings"] == original["readings"]
    info = reopened.snapshot()["dataset_binding"]
    assert info["read_only"] is True and info["source"]["format"] == "mat"
    assert info["source"]["path"] == str(target)
    materialization = reopened._binding._folder
    assert materialization.is_dir()
    target.write_bytes(target.read_bytes() + b"Changed source")
    assert reopened.snapshot()["dataset_binding"]["available"] is False
    with pytest.raises(ValueError, match="archive bytes changed"):
        reopened.dataset()
    reopened.activate_live(expected_binding_id=info["id"], expected_generation=info["generation"])
    assert not materialization.exists()
    assert {path.name: path.read_bytes() for path in folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES} == before


@pytest.mark.parametrize("destination", [
    "/Users/source/Measurements/batch",
    r"C:\Measurements\batch",
    r"\\server\measurements\batch",
])
def test_whole_mat_preserves_foreign_destination_history(saved, tmp_path, destination):
    folder, _, original, _ = saved
    source = DatasetBinding(folder=folder)
    try:
        review, _, files = source.capture()
    finally:
        source.close()
    disk_originals = dict(files)
    manifest = deepcopy(review["manifest"])
    manifest["destinations"] = [destination]
    manifest["destination_issues"] = {destination: "Original destination is unavailable"}
    files["batch.json"] = json.dumps(manifest, ensure_ascii=True, allow_nan=False).encode()
    captured = review_captured(folder, files)
    assert captured["read_only"] and captured["manifest"] == manifest
    payload, identity = batch_archive.encode(captured, files)
    assert identity["manifest_sha256"] == hashlib.sha256(files["batch.json"]).hexdigest()
    archive = tmp_path / "foreign-origin.mat"
    archive.write_bytes(payload)
    reader = DesktopSession([])
    try:
        open_saved(reader, archive)
        assert reader.batch is None
        assert reader._binding._review["manifest"] == manifest
        assert reader.dataset()["readings"] == original["readings"]
        assert reader.snapshot()["dataset_binding"]["read_only"] is True
        for name, raw in files.items():
            assert (reader._binding._folder / name).read_bytes() == raw
    finally:
        reader._binding.close()
    assert {name: (folder / name).read_bytes() for name in disk_originals} == disk_originals


def test_archive_paths_and_file_digests_cannot_escape_or_relabel_source(saved, monkeypatch):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    payload, _ = batch_archive.prepare(session._binding)
    document = from_mat(payload, kind="batch")
    monkeypatch.setattr(batch_archive, "TemporaryDirectory", lambda **kwargs: pytest.fail(
        "Unsafe or unauthenticated archive reached temporary materialization",
    ))
    document["files"]["../outside.json"] = document["files"]["batch.json"]
    with pytest.raises(ValueError, match="unsafe"):
        batch_archive.unpack(to_mat(document, kind="batch"))
    del document["files"]["../outside.json"]
    document["files"]["batch.json"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        batch_archive.unpack(to_mat(document, kind="batch"))


def test_mat_capability_is_truthful_and_unavailable_dependency_creates_no_archive(
    saved, tmp_path, monkeypatch,
):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    assert session.request("formats", {})["batch"]["mat"]["available"] is True

    def absent():
        raise ImportError("Synthetic unavailable MAT dependency")

    monkeypatch.setattr(operation_exports, "_mat_dependencies", absent)
    assert session.request("formats", {})["batch"]["mat"] == dict(
        available=False, reason="MAT files are unavailable in this app build.",
    )
    from color_measurement import document_mat

    monkeypatch.setattr(document_mat, "_mat_dependencies", absent)
    target = tmp_path / "unavailable.mat"
    binding = session.snapshot()["dataset_binding"]
    with pytest.raises(ImportError, match="dependency"):
        session.export_batch_mat(target, binding_id=binding["id"], generation=binding["generation"])
    assert not target.exists()
