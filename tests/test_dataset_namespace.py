"""Inherited native identities remain distinct in live and authenticated saved analysis."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from test_application import _automatic_companion_state, _command, _configure_chart, _wait
from test_batch_archive import continued_chart as _continued_chart

from color_measurement import analysis_exports, batch_archive, chart_science, chart_state
from color_measurement.application import BatchApplication, review_saved
from color_measurement.chart_targets import import_ti2_json
from color_measurement.dataset_binding import DatasetBinding, content_digest
from color_measurement.demo import demo_frontends
from color_measurement.exports import _LOCK_NAMES
from color_measurement.models import observation_from_dict
from color_measurement.presentation import RESOURCE
from color_measurement.reading_dataset import project_dataset, reading_identity, reading_key

continued_chart = _continued_chart


def inherited_row(binding):
    return next((key, row) for key, row in binding.data()["readings"].items() if row["inherited"])


def test_actual_parent_child_live_saved_mat_share_source_qualified_native_data(continued_chart):
    _, owner = continued_chart
    live = DatasetBinding(owner=owner)
    saved = DatasetBinding(folder=owner._folder)
    assert len(live.data()["readings"]) == 2
    assert {key: value for key, value in live.data().items() if key != "roster_revision"} == {
        key: value for key, value in saved.data().items() if key != "roster_revision"
    }
    assert saved.metadata()["review"]["accepted_readings"] == len(saved.data()["readings"]) == 2
    assert saved._review["sample_count"] == 1  # The canonical review counts this segment only.
    key, row = inherited_row(live)
    reference = row["reading_ref"]
    assert key == reading_key(reference)
    original, = review_saved(owner._folder)["inherited_operations"]
    assert reference["segment_id"] == original["segment_id"]
    assert reference["source_manifest_sha256"] == original["source_manifest_sha256"]
    assert json.loads(json.dumps(row["observation"])) == original["result"]["patches"][0][
        "observation"
    ]
    assert row["observation"]["operation_id"] == reference["operation_id"]
    detail = saved.annotation_reference(reference)
    assert detail["read_only"] and detail["draft"] is None
    assert detail["applied"]["notes"] == original["annotations"][0]["notes"]
    with pytest.raises(ValueError, match="Unknown reading"):
        owner.reading_details(reference["operation_id"], reference["observation_id"])
    payload, _ = batch_archive.prepare(saved)
    temporary = batch_archive.unpack(payload)
    restored = DatasetBinding(folder=temporary.name)
    try:
        assert restored.data() == saved.data()
        assert restored.metadata()["review"] == saved.metadata()["review"]
        for chart_pass in saved._review["manifest"]["chart"]["passes"]:
            identity = chart_pass["id"]
            assert saved.chart_view(identity) == live.chart_view(identity)
            assert restored.chart_readings(identity) == saved.chart_readings(identity) == \
                live.chart_readings(identity)
            assert saved.chart_history(identity, 1) == live.chart_history(identity, 1)
            attempt = chart_pass["attempts"][0]["id"]
            assert saved.chart_details(identity, attempt) == live.chart_details(identity, attempt)
        assert saved.chart_decision_details() == live.chart_decision_details()
    finally:
        restored.close()
        temporary.cleanup()


@pytest.mark.parametrize("source", ["live", "saved"])
def test_inherited_analysis_context_is_ephemeral_and_exports_original_inputs(
    continued_chart, source,
):
    _, owner = continued_chart
    binding = (DatasetBinding(owner=owner) if source == "live" else
               DatasetBinding(folder=owner._folder))
    key, row = inherited_row(binding)
    reference = row["reading_ref"]
    _automatic_companion_state(owner)
    before = {path.name: path.read_bytes() for path in owner._folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES}
    command = str(uuid4())
    outcome = binding.set_context(command, 0, reference, dict(reference, kind="retained-reading"))
    assert outcome["comparison"]["version"] == 1 and not outcome["comparison"]["persisted"]
    assert binding.set_context(command, 0, reference, dict(reference, kind="retained-reading")) == \
        outcome
    with pytest.raises(ValueError, match="different content"):
        binding.set_context(command, 0, reference, None)
    data = binding.data()
    snapshot, _, _, payloads = analysis_exports.prepare_analysis(
        binding, dataset_revision=data["dataset_revision"], comparison_version=1,
        scope="all", selected=[], scale=100,
    )
    assert len(snapshot["records"]) == 2
    assert snapshot["role_inputs"]["reference"]["reading_id"] == key
    parent = next(record for record in snapshot["records"] if record["reading_id"] == key)
    assert parent["reading_ref"] == reference
    document = snapshot["source_documents"][parent["operation_document"]]
    assert document["kind"] == "operation"
    assert document["canonical_json"].encode() in payloads.values()
    assert parent["observation"] == row["observation"]
    assert {path.name: path.read_bytes() for path in owner._folder.iterdir()
            if path.is_file() and path.name not in _LOCK_NAMES} == before


@pytest.mark.parametrize("choice", ["local", "clear", "standard-white", "owner-direct"])
def test_later_persisted_local_context_cannot_reuse_ephemeral_context_version(
    continued_chart, choice,
):
    _, owner = continued_chart
    binding = DatasetBinding(owner=owner)
    _, row = inherited_row(binding)
    binding.set_context(str(uuid4()), 0, row["reading_ref"], None)
    current = next(row for row in binding.data()["readings"].values() if not row["inherited"])
    pair = current["reading_ref"]
    reference, white = (pair if choice in {"local", "owner-direct"} else None), None
    if choice == "standard-white":
        native = current["observation"]["backend_reported"]["xyz"]
        white = dict(kind="theoretical-lab-reference", name="D50", reference_y=100,
                     **{key: native[key] for key in ("unit", "scale", "observer")})
    command = str(uuid4())
    if choice == "owner-direct":
        assert _command(owner, "set_comparison_context", dict(
            version=0, reference=reference, white=white,
        ))["admission"] == "admitted"
    else:
        assert binding.set_context(command, 1, reference, white)["admission"] in {
            "admitted", "no_change",
        }
    _wait(owner, lambda state: state["phase"] == "idle")
    assert binding.metadata()["comparison"]["version"] == 2
    assert binding._ephemeral_context is None
    with pytest.raises(ValueError, match="version changed"):
        binding.context(1)
    context = binding.context(2)
    assert context["reference"] == reference and context["white"] == white
    assert binding.metadata()["comparison"]["saved"]
    persisted = DatasetBinding(folder=owner._folder).context(owner.snapshot()["comparison"][
        "version"
    ])
    assert persisted["reference"] == reference and persisted["white"] == white
    if choice != "owner-direct":
        assert binding.set_context(command, 1, reference, white)["state"] == "completed"
        assert binding.metadata()["comparison"]["version"] == 2


def test_refused_local_context_does_not_discard_the_inherited_analysis_choice(continued_chart):
    _, owner = continued_chart
    binding = DatasetBinding(owner=owner)
    reference = inherited_row(binding)[1]["reading_ref"]
    binding.set_context(str(uuid4()), 0, reference, None)
    before = binding.context(1)
    invalid = dict(operation_id=str(uuid4()), observation_id=str(uuid4()))
    command = str(uuid4())
    assert binding.set_context(command, 1, invalid, None)["admission"] == "refused"
    assert binding.context(1) == before
    assert binding.set_context(command, 1, invalid, None)["admission"] == "refused"
    assert binding.context(1) == before


def test_qualified_native_pairs_do_not_collapse_across_original_sources(continued_chart):
    _, owner = continued_chart
    binding = DatasetBinding(folder=owner._folder)
    reference = inherited_row(binding)[1]["reading_ref"]
    other = dict(reference, segment_id=str(uuid4()), source_manifest_sha256="0" * 64)
    assert reading_key(reference) != reading_key(other)
    for bad in (dict(reference, source_manifest_sha256="0" * 64),
                dict(reference, segment_id=str(uuid4())),
                {key: value for key, value in reference.items() if key != "segment_id"}):
        with pytest.raises(ValueError):
            binding.set_context(str(uuid4()), 0, bad, None)
    before = deepcopy(binding.data())
    assert binding.data() == before


def test_projection_keeps_identical_native_ids_in_two_declared_source_namespaces(continued_chart):
    _, owner = continued_chart
    binding = DatasetBinding(folder=owner._folder)
    reference = inherited_row(binding)[1]["reading_ref"]
    observation = inherited_row(binding)[1]["observation"]
    other = dict(reference, segment_id=str(uuid4()), source_manifest_sha256="0" * 64)
    observations, annotations, hashes = {}, [], {}
    for index, declared in enumerate((reference, other)):
        identity = reading_identity(declared)
        observations[identity] = deepcopy(observation)
        hashes[identity] = content_digest(observation)
        annotations.append(dict(declared, reading_ref=declared, name=f"Declared source {index}",
                                notes_preview="", notes_length=0, saved=True, version=0))
    state = dict(batch_id=str(uuid4()), context_id=str(uuid4()), annotations=annotations)
    data = project_dataset(
        state["annotations"], observations, hashes, {}, "declared-software-inputs",
        source_identity=dict(kind="batch", batch_id=state["batch_id"],
                             context_id=state["context_id"]),
    )
    assert list(data["readings"]) == [reading_key(reference), reading_key(other)]
    assert [row["name"] for row in data["readings"].values()] == [
        "Declared source 0", "Declared source 1",
    ]
    assert all(row["observation"] == observation for row in data["readings"].values())


def test_live_local_context_replay_remains_owned_by_original_command_registry(continued_chart):
    _, owner = continued_chart
    binding = DatasetBinding(owner=owner)
    row = next(row for row in binding.data()["readings"].values() if not row["inherited"])
    command = str(uuid4())
    initial = binding.set_context(command, 0, row["reading_ref"], None)
    assert initial["admission"] == "admitted"
    _wait(owner, lambda state: state["phase"] == "idle")
    assert binding.set_context(command, 0, row["reading_ref"], None)["state"] == "completed"
    assert owner.snapshot()["comparison"]["version"] == binding.context(1)["version"] == 1


@pytest.mark.parametrize("source", ["live", "saved"])
def test_chart_queries_refuse_boolean_identity_and_history_bounds(continued_chart, source):
    _, owner = continued_chart
    binding = (DatasetBinding(owner=owner) if source == "live" else
               DatasetBinding(folder=owner._folder))
    identity = binding.chart_view()["active_pass"]
    for call in (lambda: binding.chart_view(False), lambda: binding.chart_readings(False),
                 lambda: binding.chart_history(identity, True),
                 lambda: binding.chart_history(identity, 1, offset=True),
                 lambda: binding.chart_history(identity, 1, limit=51),
                 lambda: binding.operation_details(False)):
        with pytest.raises(ValueError):
            call()


@pytest.mark.parametrize("color_space", ["srgb", "display-p3"])
def test_chart_color_query_composes_original_live_saved_and_mat_source_qualifications(
    continued_chart, color_space,
):
    _, owner = continued_chart
    live, saved = DatasetBinding(owner=owner), DatasetBinding(folder=owner._folder)
    payload, _ = batch_archive.prepare(saved)
    temporary = batch_archive.unpack(payload)
    restored = DatasetBinding(folder=temporary.name)
    before = {path.name: path.read_bytes() for path in owner._folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES}
    try:
        for selected in saved._review["manifest"]["chart"]["passes"]:
            identity = selected["id"]
            packet = live.chart_colors(identity, color_space=color_space)
            frozen = saved.chart_colors(identity, color_space=color_space)
            assert frozen == restored.chart_colors(identity, color_space=color_space)
            assert {key: value for key, value in packet.items() if key != "source_revision"} == {
                key: value for key, value in frozen.items() if key != "source_revision"
            }
            assert packet["observer_sha256"] == hashlib.sha256(RESOURCE.read_bytes()).hexdigest()
            assert packet["source_revision"] == (
                f"{owner._data['batch_id']}/{owner._data['revision']}"
            )
            assert frozen["source_revision"] == saved.metadata()["roster_revision"]
            assert packet["expected"]["available"] is False
            assert packet["expected"]["reason"] == "No authenticated TI2 expected values"
            for row, wrapper in zip(packet["measured"], live.chart_readings(identity)):
                original = chart_science.chart_reading_colors(
                    dict(wrapper, observation=observation_from_dict(wrapper["observation"])),
                    observer_csv=RESOURCE.read_bytes(), color_space=color_space,
                )
                assert {key: value for key, value in row.items() if key != "source"} == {
                    key: value for key, value in original.items() if key != "source"
                }
                assert row["source"] == dict(
                    reading_ref=wrapper["reading_ref"],
                    observation_sha256=content_digest(wrapper["observation"]),
                    accepted_context_sha256=content_digest(wrapper["accepted_context"]),
                )
                assert not row["available"] and "reflective" in row["reason"]
        assert {path.name: path.read_bytes() for path in owner._folder.iterdir()
                if path.is_file() and path.name not in _LOCK_NAMES} == before
    finally:
        restored.close()
        temporary.cleanup()


@pytest.mark.parametrize("strips_as", ["rows", "columns"])
def test_chart_color_query_uses_declared_ti2_context_and_common_reflective_white(
    tmp_path, strips_as,
):
    from test_application_chart import _measure
    from test_chart_targets import LAB_CONTEXT, LAB_FIXTURE

    front = next(front for front in demo_frontends() if front._meter.persona == "i1pro3")
    owner = BatchApplication(tmp_path / "reflective", frontend=front, mode="reflective_spot_m1",
                             options={})
    owner.bind()
    _wait(owner, lambda state: state["phase"] == "idle" and not state["companions"]["saving"])
    definition = chart_state.definition("ti2", target_json=import_ti2_json(str(LAB_FIXTURE)),
                                        strips_as=strips_as, mapping_confirmed=True)
    assert _configure_chart(owner, definition)["admission"] == "admitted"
    _measure(owner)
    binding = DatasetBinding(owner=owner)
    for color_space in ("srgb", "display-p3"):
        packet = binding.chart_colors(None, color_space, expected_context=LAB_CONTEXT)
        assert packet["expected"] == chart_science.chart_expected_colors(
            definition, json.dumps(LAB_CONTEXT), color_space=color_space,
        )
        assert packet["expected"]["available"] and packet["measured"][0]["available"]
        assert packet["measured"][0]["context"]["white"]["reference_y"] == 100
        assert packet["measured"][0]["color"]["encoding"]["color_space"] == color_space
    assert not binding.chart_colors(None, "srgb")["expected"]["available"]
    assert _command(owner, "finish_chart", dict(
        chart_revision=owner.snapshot()["chart"]["revision"], allow_incomplete=True,
    ))["admission"] == "admitted"


def test_chart_color_query_refuses_bad_arguments_and_changed_source(continued_chart):
    _, owner = continued_chart
    binding = DatasetBinding(folder=owner._folder)
    for call in (lambda: binding.chart_colors(False, "srgb"),
                 lambda: binding.chart_colors(None, "unknown"),
                 lambda: binding.chart_colors(None, "srgb", expected_context={}),
                 lambda: binding.chart_colors(None, "srgb", expected_context="null")):
        with pytest.raises(ValueError):
            call()
    path = owner._folder / "batch.json"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b" ")
        with pytest.raises(ValueError, match="source bytes changed"):
            binding.chart_colors(None, "srgb")
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("field", ["chart", "source", "companion"])
def test_chart_color_query_refuses_a_revision_change_during_calculation(
    continued_chart, monkeypatch, field,
):
    _, owner = continued_chart
    binding = DatasetBinding(owner=owner)
    original = chart_science.chart_reading_colors

    def changed(wrapper, **kwargs):
        result = original(wrapper, **kwargs)
        with owner._mutex:
            if field == "chart":
                owner._data["chart"]["revision"] += 1
            elif field == "source":
                owner._data["revision"] += 1
            else:
                owner._revision += 1
        return result

    monkeypatch.setattr(chart_science, "chart_reading_colors", changed)
    before = owner._data["chart"]["revision"]
    source_before = owner._revision
    canonical_before = owner._data["revision"]
    try:
        if field == "companion":
            packet = binding.chart_colors(None, "srgb")
            assert packet["source_revision"] == f"{owner._data['batch_id']}/{canonical_before}"
        else:
            with pytest.raises(ValueError, match="revision changed"):
                binding.chart_colors(None, "srgb")
    finally:
        owner._data["chart"]["revision"] = before
        owner._revision = source_before
        owner._data["revision"] = canonical_before


@pytest.mark.parametrize("source", ["live", "saved"])
def test_session_chart_queries_and_annotation_use_selected_source_fences(continued_chart, source):
    from color_measurement.desktop_session import DesktopSession

    _, owner = continued_chart
    session = DesktopSession([])
    session.batch = owner
    session._live_binding = DatasetBinding(owner=owner)
    session._binding = session._live_binding
    if source == "saved":
        before = session.snapshot()["dataset_binding"]
        session.open_saved(owner._folder, expected_binding_id=before["id"],
                           expected_generation=before["generation"])
    binding = session.snapshot()["dataset_binding"]
    fence = dict(binding_id=binding["id"], generation=binding["generation"])
    chart = session._binding.chart_view()
    chosen = chart["pass_id"] if "pass_id" in chart else session._binding._chart()["active_pass"]
    attempt = session._binding._chart()["passes"][0]["attempts"][0]["id"]
    first_pass = session._binding._chart()["passes"][0]["id"]
    queries = {
        "chart_view": dict(pass_id=None),
        "chart_history": dict(pass_id=first_pass, cell=1, offset=0, limit=1),
        "chart_details": dict(pass_id=first_pass, attempt_id=attempt),
        "chart_decision_details": {},
        "chart_readings": dict(pass_id=chosen),
        "operation_details": dict(command_id=owner.snapshot()["operations"][0]["command_id"]),
    }
    for method, operands in queries.items():
        reply = session.request(method, dict(fence, **operands))
        assert reply == dict(fence, value=getattr(session._binding, method)(**operands))
        with pytest.raises(ValueError, match="binding changed"):
            session.request(method, dict(fence, generation=fence["generation"]+1, **operands))
    _, row = inherited_row(session._binding)
    detail = session.request("annotation", dict(fence, reading_ref=row["reading_ref"]))
    assert detail["read_only"]
    expected = session._binding.annotation_reference(row["reading_ref"])
    assert detail["applied"]["notes"] == expected["applied"]["notes"]
    with pytest.raises(ValueError, match="Expected fields"):
        session.request("annotation", dict(fence, operation_id=row["observation"]["operation_id"],
                                           observation_id=row["observation"]["observation_id"]))


def test_native_chart_source_token_keeps_paths_and_replay_out_of_renderer(continued_chart):
    from test_desktop_session import create, wait

    from color_measurement.demo import demo_frontends
    from color_measurement.desktop_session import DesktopSession

    _, source = continued_chart
    session = DesktopSession(demo_frontends())
    create(session, source._folder.parent / "continued-via-session", "Continuation", "")
    state = session.snapshot()["batch"]
    fence = {key: state[key] for key in ("batch_id", "generation", "context_id")}
    packet = session.stage_chart_continuation(source._folder, **fence)
    assert "folder" not in packet and packet["passes"]
    params = dict(fence, source_token=packet["source_token"], command_id=str(uuid4()),
                  action="new-pass", source_pass_id=packet["passes"][0]["id"],
                  acknowledged_material=[])
    result = session.request("continue_chart", params)
    assert result["admission"] == "admitted"
    wait(session, lambda current: current["phase"] == "idle")
    assert session.request("continue_chart", params)["effect"] == "continue_chart"
    with pytest.raises(ValueError, match="Expected fields"):
        session.request("continue_chart", dict(params, folder=str(source._folder)))
    assert any(row["inherited"] for row in session.dataset()["readings"].values())
