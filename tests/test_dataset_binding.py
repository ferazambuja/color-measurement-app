"""Authenticated saved inputs share the live numerical owner without resuming work."""

import json
from uuid import uuid4

import pytest
from test_desktop_session import create, measure, query, send, wait, xyz_analysis

from color_measurement import batch_archive, dataset_binding
from color_measurement.application import review_saved
from color_measurement.demo import demo_frontends
from color_measurement.desktop_session import DesktopSession
from color_measurement.instrument import Instrument


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *args, **kwargs: pytest.fail("Native transport"),
    )
    source = DesktopSession(demo_frontends())
    folder = tmp_path / "source"
    create(source, folder, "Captured source", "")
    measure(source)
    measure(source)
    rows = list(source.dataset()["readings"].values())
    pairs = [{key: row["observation"][key] for key in ("operation_id", "observation_id")}
             for row in rows]
    notes = "Full source note 🟦\n" * 30
    assert send(source, "edit_annotation_draft", dict(
        pairs[0], version=0, draft_version=0, name="=Retained label", notes=notes,
    ))["admission"] == "admitted"
    assert send(source, "annotate", dict(pairs[0], version=0, draft_version=1))["admission"] == \
        "admitted"
    wait(source, lambda state: state["phase"] == "idle")
    assert send(source, "set_comparison_context", dict(
        version=0, reference=pairs[0], white=dict(pairs[1], kind="retained-reading"),
    ))["admission"] == "admitted"
    wait(source, lambda state: state["phase"] == "idle")
    expected = xyz_analysis(source)
    data = source.dataset()
    finish_id = str(uuid4())
    finished = send(source, "finish", identity=finish_id)
    assert finished["admission"] == "admitted"
    wait(source, lambda state: source.batch.command_outcome(finish_id)["state"] != "pending")
    assert source.batch.command_outcome(finish_id)["state"] == "completed"
    return folder, expected, data, pairs


def open_saved(session, folder):
    binding = session.snapshot()["dataset_binding"]
    return session.open_saved(folder, expected_binding_id=binding["id"],
                              expected_generation=binding["generation"])


@pytest.mark.parametrize("changed", ["other_directory", "typed_header"])
def test_bound_batch_capture_refuses_other_source_and_forged_header(saved, tmp_path, changed):
    import shutil

    folder, _, _, _ = saved
    binding = dataset_binding.DatasetBinding(folder=folder)
    header, identity, payloads = binding.capture()
    binding.assert_capture(header, identity, payloads)
    if changed == "other_directory":
        copied = tmp_path / "other-source"
        shutil.copytree(folder, copied)
        header, identity, payloads = dataset_binding.DatasetBinding(folder=copied).capture()
    else:
        header["manifest"]["batch_name"] = "Not the authenticated source name"
    with pytest.raises(ValueError, match="differs"):
        binding.assert_capture(header, identity, payloads)


def test_live_capture_allows_only_unverified_optional_receipt_progress(tmp_path):
    source = DesktopSession(demo_frontends())
    folder = tmp_path / "live-source"
    create(source, folder, "Live source", "")
    measure(source)
    wait(source, lambda state: not state["companions"]["saving"])
    binding = source._binding
    header, identity, payloads = binding.capture()
    assert header["ti3"] is not None
    (folder / "ti3-result.json").write_text("{}")
    current, current_identity, current_payloads = binding.capture()
    assert current["ti3"] is None and current["export_reason"] != header["export_reason"]
    assert current_identity == identity and current_payloads == payloads
    binding.assert_capture(header, identity, payloads)
    header["manifest"]["batch_name"] = "Forged canonical header"
    with pytest.raises(ValueError, match="differs"):
        binding.assert_capture(header, identity, payloads)


@pytest.mark.parametrize("changed", ["receipt", "operation", "qualification"])
def test_canonical_capture_allows_optional_receipt_progress_but_refuses_source_mutation(
    saved, monkeypatch, changed,
):
    folder, _, _, _ = saved
    review = review_saved(folder)
    path = folder / ("ti3-result.json" if changed == "receipt" else review["operations"][0]["path"])
    original = path.read_bytes()
    if changed == "qualification":
        path.unlink()
        assert review_saved(folder)["sample_count"] == 1
    reader = dataset_binding._read_regular
    modified = False

    def progressed(candidate):
        nonlocal modified
        result = reader(candidate)
        if candidate.name == "batch.json" and not modified:
            modified = True
            path.write_bytes(b"{}" if changed == "receipt" else
                             original if changed == "qualification" else original + b" ")
        return result

    monkeypatch.setattr(dataset_binding, "_read_regular", progressed)
    try:
        if changed in {"receipt", "qualification"}:
            captured, _, files = dataset_binding.capture_saved(folder)
            assert captured["manifest_sha256"] == review["manifest_sha256"]
            assert captured["sample_count"] == review["sample_count"] == 2
            assert "ti3-result.json" not in files
            assert all(item["review_state"] == "accepted" for item in captured["operations"])
            if changed == "qualification":
                prepared = batch_archive.prepare_from_capture(captured, files)
                temporary = batch_archive.unpack(prepared["mat"]["payload"])
                try:
                    assert review_saved(temporary.name)["operations"] == captured["operations"]
                    assert review_saved(temporary.name)["sample_count"] == captured["sample_count"]
                finally:
                    temporary.cleanup()
        else:
            with pytest.raises(ValueError):
                dataset_binding.capture_saved(folder)
    finally:
        path.write_bytes(original)


def test_saved_reading_analysis_matches_live_without_a_frontend_or_writable_owner(saved):
    folder, expected, data, pairs = saved
    original = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    session = DesktopSession([])
    packet = open_saved(session, folder)
    assert session.batch is None
    assert packet["dataset_binding"]["kind"] == "saved"
    assert packet["dataset_binding"]["read_only"]
    saved_data = session.dataset()
    assert saved_data["dataset_revision"] == data["dataset_revision"]
    for key, row in saved_data["readings"].items():
        assert row == data["readings"][key], {
            field: (data["readings"][key][field], value)
            for field, value in row.items() if value != data["readings"][key][field]
        }
    params = dict(scale=100, dataset_revision=saved_data["dataset_revision"],
                  comparison_version=1, selected=list(saved_data["readings"])[1:])
    result = query(session, "analysis", params)
    for field in ("xyz_comparison_by_source", "delta_e_by_source", "comparison_by_source"):
        assert result[field] == expected[field]
    assert query(session, "annotation", {"reading_ref": pairs[0]})["draft"] is None
    assert {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()} == original


def test_saved_context_changes_are_ephemeral_versioned_and_do_not_mutate_inputs(saved):
    folder, _, _, pairs = saved
    before = (folder / "batch.json").read_bytes()
    session = DesktopSession([])
    open_saved(session, folder)
    payload = dict(command_id=str(uuid4()), comparison_version=1, reference=pairs[1], white=None)
    outcome = query(session, "set_analysis_context", payload)
    assert outcome["comparison"]["version"] == 2
    assert outcome["comparison"]["persisted"] is False
    assert query(session, "set_analysis_context", payload) == outcome
    with pytest.raises(ValueError, match="different content"):
        query(session, "set_analysis_context", dict(payload, reference=pairs[0]))
    assert (folder / "batch.json").read_bytes() == before


def test_open_saved_preserves_live_unsaved_draft_and_restores_it(saved, tmp_path, monkeypatch):
    from color_measurement import application

    folder, _, _, _ = saved
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "live", "Retained live work", "")
    state = session.snapshot()["batch"]
    draft = state["draft"]

    def refuse_publication(*args, **kwargs):
        raise OSError("Controlled unsaved live draft")

    with monkeypatch.context() as scoped:
        scoped.setattr(application, "_publish_owned_file", refuse_publication)
        send(session, "edit_draft", dict(draft_id=draft["id"], version=draft["version"],
                                         name="Unsaved work", notes="Keep this", options={},
                                         number_samples=False))
        wait(session, lambda state: state["phase"] == "idle")
    owner = session.batch
    before = owner.snapshot()
    manifest = owner._manifest_bytes
    live_binding = session.snapshot()["dataset_binding"]
    open_saved(session, folder)
    assert session.batch is owner and owner.snapshot() == before
    assert owner._manifest_bytes == manifest
    with pytest.raises(ValueError, match="read-only"):
        send(session, "finish")
    selected = session.snapshot()["dataset_binding"]
    packet = session.activate_live(expected_binding_id=selected["id"],
                                   expected_generation=selected["generation"])
    assert packet["batch"]["draft"]["name"] == "Unsaved work"
    assert packet["dataset_binding"]["kind"] == "live"
    with pytest.raises(ValueError, match="binding changed"):
        query(session, "dataset", binding=live_binding)
    retry_id = str(uuid4())
    assert send(session, "retry_save", identity=retry_id)["admission"] == "admitted"
    wait(session, lambda state: owner.command_outcome(retry_id)["state"] != "pending")
    assert owner.command_outcome(retry_id)["state"] == "completed"
    identity = str(uuid4())
    assert send(session, "finish", identity=identity)["admission"] == "admitted"
    wait(session, lambda state: owner.command_outcome(identity)["state"] != "pending")
    assert owner.command_outcome(identity)["state"] == "completed"


def test_nonnull_failed_review_result_is_excluded_and_keeps_its_diagnostic(saved):
    folder, _, _, _ = saved
    manifest = json.loads((folder / "batch.json").read_bytes())
    path = folder / manifest["operations"][0]["path"]
    path.write_bytes(path.read_bytes() + b"\n")
    session = DesktopSession([])
    packet = open_saved(session, folder)
    assert len(session.dataset()["readings"]) == 1
    assert review_saved(folder)["operations"][0]["result"] is not None
    excluded, = packet["dataset_binding"]["review"]["excluded_operations"]
    assert excluded["state"] == "unknown" and "cannot be authenticated" in excluded["reason"]
    data = session.dataset()
    with pytest.raises(ValueError, match="cannot be authenticated"):
        query(session, "analysis", dict(scale=100, dataset_revision=data["dataset_revision"],
                                         comparison_version=1, selected=list(data["readings"])))


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_uncopyable_excluded_source_keeps_healthy_review_and_refuses_whole_archive(saved, kind):
    from color_measurement.batch_archive import prepare

    folder, _, _, _ = saved
    manifest = json.loads((folder / "batch.json").read_bytes())
    path = folder / manifest["operations"][0]["path"]
    raw = path.read_bytes()
    path.unlink()
    if kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(folder / manifest["operations"][1]["path"])
    reader = DesktopSession([])
    packet = open_saved(reader, folder)
    assert packet["dataset_binding"]["available"] is True
    assert len(reader.dataset()["readings"]) == 1
    excluded, = packet["dataset_binding"]["review"]["excluded_operations"]
    assert excluded["state"] == "unknown" and excluded["reason"]
    witness = reader._binding._review["capture"]["uncopyable_files"][path.name]
    assert witness["reason"] in excluded["reason"]
    assert "Declared source file is unavailable" in excluded["reason"]
    with pytest.raises(ValueError, match="Cannot archive unavailable declared sources"):
        prepare(reader._binding)
    if kind == "directory":
        path.rmdir()
    else:
        path.unlink()
    missing_review, _, _ = dataset_binding.capture_saved(folder)
    missing = next(item for item in missing_review["operations"] if item["path"] == path.name)
    assert missing["review_state"] == "unknown"
    assert f"Declared source file is missing: {path.name}" in missing["review_reason"]
    path.write_bytes(raw)
    assert reader.snapshot()["dataset_binding"]["available"] is False


@pytest.mark.parametrize("target", ["manifest", "operation"])
def test_bound_source_change_refuses_data_and_analysis_without_hiding_native_owner(saved, target):
    folder, _, _, _ = saved
    session = DesktopSession([])
    packet = open_saved(session, folder)
    manifest = json.loads((folder / "batch.json").read_bytes())
    path = folder / ("batch.json" if target == "manifest" else manifest["operations"][0]["path"])
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n")
        metadata = session.snapshot()["dataset_binding"]
        assert metadata["available"] is False and "changed" in metadata["reason"]
        with pytest.raises(ValueError, match="source bytes changed"):
            query(session, "dataset", binding=packet["dataset_binding"])
        restored = session.activate_live(expected_binding_id=metadata["id"],
                                         expected_generation=metadata["generation"])
        assert restored["dataset_binding"]["kind"] == "empty"
    finally:
        path.write_bytes(original)


def test_binding_rejects_stale_dialog_generation_and_raw_renderer_paths(saved):
    folder, _, _, _ = saved
    session = DesktopSession([])
    old = session.snapshot()["dataset_binding"]
    session.bind()
    with pytest.raises(ValueError, match="binding changed"):
        session.open_saved(folder, expected_binding_id=old["id"],
                           expected_generation=old["generation"])
    assert session.snapshot()["dataset_binding"]["kind"] == "empty"
    with pytest.raises(ValueError):
        session.request("open_saved", {"folder": str(folder)})


def test_repeat_lineage_travels_with_live_and_saved_dataset_rows(tmp_path, monkeypatch):
    """Saved review keeps its own Repeat lineage instead of borrowing the live batch's."""
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *args, **kwargs: pytest.fail("Native transport"),
    )
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "lineage", "Study", "")
    measure(session)
    first = next(iter(session.dataset()["readings"].values()))["observation"]
    source_pair = {key: first[key] for key in ("operation_id", "observation_id")}
    state = session.snapshot()["batch"]
    draft = state["draft"]
    assert send(session, "repeat_sample", dict(
        context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
        **source_pair,
    ))["admission"] == "admitted"
    wait(session, lambda current: current["phase"] == "idle", respond=True)
    measure(session)

    def lineage():
        return sorted(((row["observation"]["operation_id"], row["repeat_of"])
                       for row in session.dataset()["readings"].values()),
                      key=lambda item: item[1] is not None)

    live = lineage()
    assert [item[1] for item in live] == [None, None, source_pair]
    folder = session.snapshot()["batch"]["folder"]
    assert send(session, "finish")["admission"] == "admitted"
    wait(session, lambda current: current["phase"] == "idle")
    open_saved(session, folder)
    assert session.snapshot()["dataset_binding"]["kind"] == "saved"
    assert lineage() == live
