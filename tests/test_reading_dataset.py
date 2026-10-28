"""Mixed validated software records preserve native types and independent color absence."""

import csv
import io
import json
import math
import time
from copy import deepcopy
from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from test_analysis_exports import export_params
from test_application import _automatic_companion_state
from test_batch_archive import continued_chart as _continued_chart
from test_dataset_binding import open_saved
from test_desktop_session import create, measure, query, send, wait
from test_display_timing_frontend import timing_result
from test_refresh_frequency import outcome

from color_measurement.analysis_exports import analysis_csv, prepare_analysis
from color_measurement.dataset_binding import DatasetBinding, content_digest
from color_measurement.demo import demo_frontends
from color_measurement.desktop_session import DesktopSession
from color_measurement.document_mat import from_mat
from color_measurement.exports import _LOCK_NAMES
from color_measurement.instrument import Instrument
from color_measurement.models import XYZ
from color_measurement.operations import SimulatedDevice
from color_measurement.reading_dataset import (
    analyze_dataset,
    dataset_white_descriptor,
    project_dataset,
)
from color_measurement.science import lab_to_xyz

continued_chart = _continued_chart


def mixed_dataset():
    color = list(SimulatedDevice().measure())[-1].observation
    scalar = outcome().patches[0].observation
    timing = timing_result().patches[0].observation
    observations = {}
    annotations = []
    for original in (color, scalar, timing):
        value = asdict(replace(original, operation_id=str(uuid4())))
        pair = value["operation_id"], value["observation_id"]
        observations[pair] = value
        annotations.append(dict(
            operation_id=pair[0], observation_id=pair[1], name=value["sample_id"],
            notes_preview="", notes_length=0, saved=True, version=0,
        ))
    state = dict(batch_id=str(uuid4()), context_id=str(uuid4()), annotations=annotations)
    before = content_digest(list(observations.values()))
    data = project_dataset(
        state["annotations"], observations,
        {pair: content_digest(row) for pair, row in observations.items()}, {}, "mixed",
        source_identity=dict(kind="batch", batch_id=state["batch_id"],
                             context_id=state["context_id"]),
    )
    assert content_digest(list(observations.values())) == before
    keys = list(data["readings"])
    context = dict(version=0, reference={field: data["readings"][keys[0]]["observation"][field]
                                       for field in ("operation_id", "observation_id")},
                   white=None, white_descriptor=None)
    params = dict(scale=100, dataset_revision=data["dataset_revision"], comparison_version=0)
    return data, context, params


def test_mixed_projection_preserves_hz_stimulus_and_two_clock_domains_without_color_fields():
    data, context, params = mixed_dataset()
    rows = list(data["readings"].values())
    assert [row["record_kind"] for row in rows] == ["color", "scalar", "timing"]
    color_key, scalar_key, timing_key = data["readings"]
    assert data["eligible_white_sources"] == [color_key]
    assert rows[1]["observation"]["backend_reported"]["value"] == 59.94
    assert rows[1]["observation"]["backend_reported"]["unit"] == "Hz"
    assert rows[1]["observation"]["stimulus"]["source"] == "external"
    timing = rows[2]["observation"]
    assert timing["backend_reported"]["display_update_delay_ms"] == 0
    assert timing["backend_reported"]["instrument_reaction_ms"] == -230
    assert timing["timing_context"]["native_clock_id"] == "worker_monotonic"
    assert timing["timing_context"]["presentations"][0]["clock_id"] == "client-clock"
    assert timing["timing_context"]["presentations"][0]["presented_at_seconds"] == 50
    assert timing["timing_context"]["method_window_started_seconds"] == 10
    projection = analyze_dataset("projection", params, data, context)
    for key in (scalar_key, timing_key):
        row = data["readings"][key]
        assert row["xyy"] is None and row["uvy"] is None
        assert "xyz" not in row["observation"]["backend_reported"]
        assert "spectrum" not in row["observation"]["backend_reported"]
        assert key not in projection["lab_by_source"]
        assert "Color metrics" in projection["color_reasons"][key]
        for preview in projection["display_previews"][key].values():
            assert preview["display_rgb"] is None and preview["gamut_status"] is None
            assert "observation" in preview["reason"]
    json.dumps(projection, allow_nan=False)


def test_mixed_analysis_and_csv_preserve_native_quantities_with_empty_xyz_cells():
    data, context, params = mixed_dataset()
    keys = list(data["readings"])
    analysis = analyze_dataset("analysis", dict(params, selected=keys), data, context)
    projection = analyze_dataset("projection", params, data, context)
    records = []
    for key, row in data["readings"].items():
        observation = row["observation"]
        records.append(dict(
            reading_id=key, operation_id=observation["operation_id"],
            observation_id=observation["observation_id"], name=row["name"], notes="",
            observation=observation, record_kind=row["record_kind"],
            derived=dict(xyy=row["xyy"], uvy=row["uvy"], lab=None, delta_e_2000=None,
                         xyz_comparison=analysis["xyz_comparison_by_source"][key],
                         color_comparison=analysis["color_comparison_by_source"][key],
                         spectral_comparison=analysis["comparison_by_source"][key],
                         display_previews=projection["display_previews"][key]),
        ))
    payload = analysis_csv(dict(
        records=records, selection=keys, source="Software-only unit input",
        source_documents={}, original_file=None, role_inputs={}, derived_science=None,
    ))
    exported = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    assert [row["record_kind"] for row in exported] == ["color", "scalar", "timing"]
    for index in (1, 2):
        row = exported[index]
        assert [row[name] for name in ("native_X", "native_Y", "native_Z")] == ["", "", ""]
        assert json.loads(row["native_quantity_json"]) == records[index]["observation"][
            "backend_reported"]
        assert json.loads(row["stimulus_json"]) == records[index]["observation"]["stimulus"]
        assert json.loads(row["record_json"])["observation"] == json.loads(json.dumps(
            records[index]["observation"]))
        assert analysis["xyz_comparison_by_source"][keys[index]]["y_percent"] is None
        assert analysis["comparison_by_source"][keys[index]]["rms"] is None
        color = analysis["color_comparison_by_source"][keys[index]]
        assert color["delta_xyz"] is color["delta_lab"] is color["delta_hue"] is None
        assert color["xyz_reason"] and color["lab_reason"] and color["hue_reason"]
        assert exported[index]["delta_chroma"] == exported[index]["delta_hue"] == ""
    assert json.loads(exported[2]["timing_context_json"]) == json.loads(json.dumps(
        records[2]["observation"]["timing_context"]))


@pytest.mark.parametrize("format", ["json", "csv", "mat"])
def test_actual_mixed_live_saved_bridge_export_uses_shared_durable_workflows(
    tmp_path, monkeypatch, format,
):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *a, **k: pytest.fail("Native transport"),
    )
    choices = []
    for front in demo_frontends():
        descriptor = json.loads(front.describe_json())
        for mode in descriptor["modes"]:
            names = {entry["workflow"] for entry in mode["workflows"] if entry["available"]}
            if mode["canonical"] and {"spot", "refresh_frequency", "display_timing"} <= names:
                choices.append((front, descriptor, mode["mode"]))
    assert choices, "The software catalog has no common color/frequency/timing persona"
    front, descriptor, mode = choices[0]
    session = DesktopSession([front])
    folder = tmp_path / "actual-mixed-source"
    create(session, folder, "Mixed software input", "No instrument or physical draw used",
           device_id=descriptor["device_id"], mode=mode)
    measure(session)
    for workflow, layout in (
        ("refresh_frequency", dict(stimulus_id="synthetic-external",
                                   stimulus_description="Declared synthetic stimulus")),
        ("display_timing", dict(stimulus_id="synthetic-controlled",
                                stimulus_description="Synthetic black-white input",
                                display_id="software-surface")),
    ):
        state = session.snapshot()["batch"]
        draft = state["draft"]
        response = send(session, "run_workflow", dict(
            context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            workflow=workflow, patch_ids=[workflow], layout=layout,
        ))
        assert response["admission"] == "admitted", response
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = session.snapshot()["batch"]
            if state["instructions"]:
                prompt = state["instructions"]
                assert send(session, "respond", dict(
                    operation_id=prompt["operation_id"], request_id=prompt["request_id"],
                    response=prompt["allowed_responses"][0],
                ))["admission"] == "admitted"
            elif state["presentation"]:
                prompt = state["presentation"]
                # Declared synthetic software input, not a GUI draw or optical observation.
                stamp = dict(phase=prompt["phase"], stimulus_id=prompt["stimulus_id"],
                             display_id=prompt["display_id"], clock_id="synthetic-client",
                             presented_at_seconds=time.monotonic(), source="client_draw_completed")
                assert send(session, "presented", dict(
                    operation_id=prompt["operation_id"], request_id=prompt["request_id"],
                    stamp=stamp,
                ))["admission"] == "admitted"
            if state["phase"] == "idle":
                break
            time.sleep(.005)
        assert state["phase"] == "idle" and state["save_reason"] is None, state
    before = session.dataset()
    assert [row["record_kind"] for row in before["readings"].values()] == [
        "color", "scalar", "timing",
    ]
    params = dict(scale=100, dataset_revision=before["dataset_revision"],
                  comparison_version=0, selected=list(before["readings"]))
    query(session, "analysis", params)
    live_export = tmp_path / ("live." + format)
    session.export_analysis(live_export, **export_params(session, scope="all", selected=[],
                                                        format=format))
    finish_id = str(uuid4())
    assert send(session, "finish", identity=finish_id)["admission"] == "admitted"
    wait(session, lambda state: session.batch.command_outcome(finish_id)["state"] != "pending")
    assert session.batch.command_outcome(finish_id)["state"] == "completed"
    saved = DesktopSession([])
    open_saved(saved, folder)
    after = saved.dataset()
    assert saved.batch is None and before["readings"] == after["readings"]
    saved_export = tmp_path / ("saved." + format)
    saved.export_analysis(saved_export, **export_params(saved, scope="all", selected=[],
                                                       format=format))
    for path in (live_export, saved_export):
        if format == "mat":
            records = from_mat(path.read_bytes(), kind="analysis")["records"]
        elif format == "json":
            records = json.loads(path.read_text())["records"]
        else:
            records = [json.loads(row["record_json"]) for row in csv.DictReader(
                io.StringIO(path.read_text()))]
        assert [record["record_kind"] for record in records] == ["color", "scalar", "timing"]
        for record, source in zip(records, before["readings"].values(), strict=True):
            assert record["observation"] == json.loads(json.dumps(source["observation"]))
            if record["record_kind"] != "color":
                assert "xyz" not in record["observation"]["backend_reported"]
                assert record["derived"]["lab"] is None
        json.dumps(records, allow_nan=False)
    if format == "json":
        from color_measurement.batch_archive import prepare_from_capture

        review, _, files = saved._binding.capture()
        prepared = prepare_from_capture(review, files)
        assert prepared["mat"]["reason"] is prepared["csv"]["reason"] is None
        native_rows = list(csv.DictReader(io.StringIO(prepared["csv"]["payload"].decode("utf-8"))))
        assert [row["record_kind"] for row in native_rows] == ["color", "scalar", "timing"]
        for row, original in zip(native_rows, before["readings"].values(), strict=True):
            assert json.loads(row["observation_json"]) == json.loads(json.dumps(
                original["observation"]))
            if row["record_kind"] != "color":
                assert [row[field] for field in ("native_X", "native_Y", "native_Z")] == [
                    "", "", "",
                ]


def color_pair_dataset(reference, sample, *, white=None, sample_context=None, white_context=None):
    original = list(SimulatedDevice().measure())[-1].observation
    observations, annotations = {}, []
    declarations = [(reference, {}), (sample, sample_context or {})]
    if white is not None:
        declarations.append((white, white_context or {}))
    for index, (values, changes) in enumerate(declarations):
        native = XYZ(tuple(values), **dict(
            dict(unit="cd/m^2", scale="absolute; Y is luminance", observer="CIE1931_2",
                 illuminant="D65"), **changes,
        ))
        record = asdict(replace(
            original, operation_id=str(uuid4()), observation_id=str(uuid4()),
            sample_id=f"Synthetic pair input {index}",
            backend_reported=replace(original.backend_reported, xyz=native, spectrum=None),
        ))
        pair = record["operation_id"], record["observation_id"]
        observations[pair] = record
        annotations.append(dict(
            operation_id=pair[0], observation_id=pair[1], name=record["sample_id"],
            notes_preview="", notes_length=0, saved=True, version=0,
        ))
    data = project_dataset(
        annotations, observations,
        {pair: content_digest(record) for pair, record in observations.items()},
        {}, "Synthetic pair inputs",
        source_identity=dict(kind="batch", batch_id=str(uuid4()), context_id=str(uuid4())),
    )
    keys = list(data["readings"])
    declaration = (dict(data["readings"][keys[2]]["reading_ref"], kind="retained-reading")
                   if white is not None else None)
    context = dict(version=0, reference=data["readings"][keys[0]]["reading_ref"],
                   white=declaration,
                   white_descriptor=dataset_white_descriptor(data["readings"], declaration))
    params = dict(scale=100, dataset_revision=data["dataset_revision"], comparison_version=0,
                  selected=keys[:2])
    return data, context, params, keys


def test_reflective_preview_survives_actual_ordinary_live_and_saved_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(Instrument, "_open_session", lambda *args: pytest.fail("Native I/O"))
    session = DesktopSession(demo_frontends())
    folder = tmp_path / "relative-ordinary"
    create(session, folder, "Relative reflective source", "Software only",
           device_id="demo:spectroscan", mode="reflective_spot")
    measure(session)
    _automatic_companion_state(session.batch)
    live = session.dataset()
    before = {path.name: path.read_bytes() for path in folder.iterdir()
              if path.is_file() and path.name not in _LOCK_NAMES}
    saved = DesktopSession([])
    open_saved(saved, folder)
    reopened = saved.dataset()
    assert reopened["dataset_revision"] == live["dataset_revision"]
    for key, row in live["readings"].items():
        assert reopened["readings"][key]["display_previews"] == row["display_previews"]
        native = row["observation"]["backend_reported"]["xyz"]
        assert native["unit"] == "1" and native["illuminant"] == "D50"
        assert row["xyy"][2] == native["values"][1]
        for preview in row["display_previews"].values():
            assert preview["display_rgb"] is not None and preview["gamut_status"] is None
            assert preview["common_reference_y"] == 100
    assert {path.name: path.read_bytes() for path in folder.iterdir()
            if path.is_file() and path.name not in _LOCK_NAMES} == before


@pytest.mark.parametrize("source", ["live", "saved"])
def test_reflective_chart_older_attempts_share_dataset_preview_without_source_changes(
    tmp_path, monkeypatch, source,
):
    from test_application_chart import _configure, _edit, _measure

    from color_measurement.application import BatchApplication

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: pytest.fail("Native I/O"))
    front = next(front for front in demo_frontends() if front._meter.persona == "spectroscan")
    owner = BatchApplication(tmp_path / "relative-chart", frontend=front,
                             mode="reflective_spot", options={})
    owner.bind()
    _automatic_companion_state(owner)
    _configure(owner, 1)
    _measure(owner)
    _automatic_companion_state(owner)
    assert _edit(owner, "select_cell", cell=1)["admission"] in {"admitted", "no_change"}
    _measure(owner)
    _automatic_companion_state(owner)
    before = {path.name: path.read_bytes() for path in owner._folder.iterdir()
              if path.is_file() and path.name not in _LOCK_NAMES}
    binding = (
        DatasetBinding(owner=owner) if source == "live" else DatasetBinding(folder=owner._folder)
    )
    data = binding.data()
    assert len(data["readings"]) == 2
    assert len(owner._data["chart"]["passes"][0]["attempts"]) == 2
    for row in data["readings"].values():
        assert row["xyy"][2] == row["observation"]["backend_reported"]["xyz"]["values"][1]
        for preview in row["display_previews"].values():
            assert preview["display_rgb"] is not None and preview["common_reference_y"] == 100
            assert preview["reference_unit"] == "1" and preview["gamut_status"] is None
    assert {path.name: path.read_bytes() for path in owner._folder.iterdir()
            if path.is_file() and path.name not in _LOCK_NAMES} == before


def test_standalone_projection_binds_file_identity_without_manufacturing_batch_or_native_ids():
    data, _, _, keys = color_pair_dataset((1, 2, 3), (2, 3, 4))
    observations = {}
    annotations = []
    for row in data["readings"].values():
        reference = row["reading_ref"]
        pair = reference["operation_id"], reference["observation_id"]
        observations[pair] = row["observation"]
        annotations.append(dict(
            reference, name=row["name"], notes_preview="", notes_length=0, saved=True, version=0,
        ))
    before = deepcopy(observations)
    hashes = {pair: content_digest(value) for pair, value in observations.items()}
    identity = dict(kind="standalone", format="operation_json", source_sha256="a" * 64)

    def project(source):
        return project_dataset(annotations, observations, hashes, {}, "source",
                               source_identity=source)

    projected = project(identity)
    assert list(projected["readings"]) == keys
    assert [row["observation"] for row in projected["readings"].values()] == list(before.values())
    assert projected["dataset_revision"] == project(dict(reversed(list(identity.items()))))[
        "dataset_revision"]
    assert projected["dataset_revision"] != project(dict(identity, source_sha256="b" * 64))[
        "dataset_revision"]
    assert projected["dataset_revision"] != data["dataset_revision"]
    with pytest.raises(ValueError):
        project(dict(identity, batch_id=str(uuid4())))
    with pytest.raises(ValueError, match="SHA-256"):
        project(dict(identity, source_sha256="unknown"))
    assert observations == before


def test_signed_comparison_without_white_preserves_native_coordinates_and_summary_direction():
    data, context, params, keys = color_pair_dataset((0, 1, 0), (1, 1, 0))
    original = deepcopy(data)
    result = analyze_dataset("analysis", params, data, context)
    packet = result["color_comparison_by_source"][keys[1]]
    assert packet["delta_xyz"] == (1, 0, 0)
    assert packet["delta_xy"] == (0.5, -0.5)
    assert packet["delta_uv"] == pytest.approx((0.25, -3 / 80))
    assert packet["delta_lab"] is packet["delta_chroma"] is packet["delta_hue"] is None
    assert packet["lab_reason"] == "Choose an explicit Lab reference white"
    assert result["delta_e_by_source"] == {}
    assert result["summary"]["delta_xyz_range"] == [
        dict(min=value, max=value, count=1, unit="cd/m^2") for value in (1, 0, 0)
    ]
    assert result["summary"]["delta_xy_range"][1] == dict(
        min=-0.5, max=-0.5, count=1, unit="1",
    )
    assert result["summary"]["delta_lab_range"] is None
    assert result["summary"]["delta_lab_unavailable"] == 1
    assert data == original
    json.dumps(result, allow_nan=False)


def test_signed_common_white_lab_chroma_hue_and_reverse_pair_use_shared_owner():
    white = (100, 100, 100)
    data, context, params, keys = color_pair_dataset(
        lab_to_xyz((50, 3, 4), white), lab_to_xyz((60, -4, 3), white), white=white,
    )
    before = deepcopy(data), deepcopy(context)
    result = analyze_dataset("analysis", params, data, context)
    packet = result["color_comparison_by_source"][keys[1]]
    assert packet["delta_lab"] == pytest.approx((10, -7, -1), abs=4e-13)
    assert packet["delta_chroma"] == pytest.approx(0, abs=3e-13)
    assert packet["delta_hue"] == pytest.approx(math.sqrt(50), abs=4e-13)
    assert result["delta_e_by_source"][keys[1]] == packet["delta_e2000"] > 0
    assert result["summary"]["delta_hue_range"]["count"] == 1
    assert result["summary"]["delta_lab_range"][1]["min"] == pytest.approx(-7)
    reverse = analyze_dataset(
        "analysis", dict(params, selected=[keys[0]]), data,
        dict(context, reference=data["readings"][keys[1]]["reading_ref"]),
    )["color_comparison_by_source"][keys[0]]
    assert reverse["delta_lab"] == pytest.approx(tuple(-value for value in packet["delta_lab"]))
    assert reverse["delta_hue"] == pytest.approx(-packet["delta_hue"])
    assert reverse["delta_e2000"] == pytest.approx(packet["delta_e2000"])
    assert (data, context) == before


def test_nominal_white_cannot_allow_pair_delta_e_across_native_illuminants():
    data, context, params, keys = color_pair_dataset(
        (40, 50, 60), (45, 55, 65), sample_context=dict(illuminant="D50"),
    )
    declaration = dict(kind="theoretical-lab-reference", name="D50", observer="CIE1931_2",
                       reference_y=100, unit="cd/m^2", scale="absolute; Y is luminance")
    context.update(white=declaration,
                   white_descriptor=dataset_white_descriptor(data["readings"], declaration))
    projection = analyze_dataset("projection", params, data, context)
    assert set(projection["lab_by_source"]) == set(keys)
    result = analyze_dataset("analysis", params, data, context)
    packet = result["color_comparison_by_source"][keys[1]]
    assert packet["delta_xyz"] is packet["delta_lab"] is packet["delta_e2000"] is None
    assert packet["delta_e2000_reason"] == "Pair unit, scale, observer or illuminant differs"
    assert keys[1] not in result["delta_e_by_source"]
    assert result["summary"]["color_unavailable"] == 1
    assert result["summary"]["delta_lab_unavailable"] == 1


def test_explicit_nominal_white_preserves_native_illuminant_and_enables_matching_pair_lab():
    declaration = dict(kind="theoretical-lab-reference", name="D50", observer="CIE1931_2",
                       reference_y=100, unit="cd/m^2", scale="absolute; Y is luminance")
    descriptor = dataset_white_descriptor({}, declaration)
    data, context, params, keys = color_pair_dataset(
        descriptor["xyz"], tuple(value / 2 for value in descriptor["xyz"]),
    )
    context.update(white=declaration, white_descriptor=descriptor)
    before = deepcopy(data)
    result = analyze_dataset("analysis", params, data, context)
    packet = result["color_comparison_by_source"][keys[1]]
    assert packet["lab_reference"] == (100, 0, 0)
    assert packet["lab_sample"][0] < 100
    assert packet["delta_lab"][0] < 0 and packet["delta_e2000"] > 0
    assert packet["illuminant"] == "D65"
    assert result["white_descriptor"]["name"] == "D50"
    assert packet["delta_e2000_reason"] is None
    assert data == before


def test_retained_white_conditions_withhold_only_lab_derived_pair_values():
    data, context, params, keys = color_pair_dataset(
        (40, 50, 60), (45, 55, 65), white=(100, 100, 100),
        white_context=dict(illuminant="D50"),
    )
    result = analyze_dataset("analysis", params, data, context)
    packet = result["color_comparison_by_source"][keys[1]]
    assert packet["delta_xyz"] == (5, 5, 5) and packet["delta_xy"] is not None
    assert packet["delta_lab"] is packet["delta_e2000"] is None
    assert packet["lab_reason"] == "Retained Lab white illuminant differs from the pair"
    assert result["summary"]["delta_xyz_unavailable"] == 0
    assert result["summary"]["delta_chroma_unavailable"] == 1


@pytest.mark.parametrize("source", ["live", "saved"])
def test_signed_comparison_uses_original_inherited_reference_and_exports_exact_packet(
    continued_chart, source,
):
    _, owner = continued_chart
    _automatic_companion_state(owner)
    binding = (DatasetBinding(owner=owner) if source == "live" else
               DatasetBinding(folder=owner._folder))
    before = {path.name: path.read_bytes() for path in owner._folder.iterdir()
              if path.is_file() and path.name not in _LOCK_NAMES}
    data = binding.data()
    key, row = next((key, row) for key, row in data["readings"].items() if row["inherited"])
    reference = row["reading_ref"]
    binding.set_context(str(uuid4()), 0, reference, dict(reference, kind="retained-reading"))
    params = dict(scale=100, dataset_revision=data["dataset_revision"], comparison_version=1,
                  selected=list(data["readings"]))
    result = binding.analyze("analysis", params)
    assert result["reference"] == key
    assert result["color_comparison_by_source"][key]["delta_xyz"] == (0, 0, 0)
    snapshot, _, _, _ = prepare_analysis(
        binding, dataset_revision=data["dataset_revision"], comparison_version=1,
        scope="all", selected=[], scale=100,
    )
    assert snapshot["role_inputs"]["reference"]["reading_ref"] == reference
    for record in snapshot["records"]:
        assert record["derived"]["color_comparison"] == result["color_comparison_by_source"][
            record["reading_id"]]
    assert snapshot["summary"] == result["summary"]
    assert {path.name: path.read_bytes() for path in owner._folder.iterdir()
            if path.is_file() and path.name not in _LOCK_NAMES} == before
