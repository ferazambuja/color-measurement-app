"""Terminal persistence and a portable native-quantity view, never acquisition."""

import ast
import builtins
import hashlib
import importlib
import io
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from color_measurement.models import (
    XYZ,
    CalibrationContext,
    ComputedValues,
    DeviceIdentity,
    Observation,
    ReportedValues,
    Spectrum,
)
from color_measurement.operation_exports import (
    OperationSaveError,
    from_mat,
    load,
    publish,
    save,
    to_mat,
)
from color_measurement.operation_records import OperationResult, PatchResult, result_to_json
from color_measurement.ti3_exports import normalize_snapshot_json, retained_snapshot, update_json


@pytest.fixture
def result():
    device = DeviceIdentity("fixture", "測定 café 🔬", "synthetic", "1", "0000123", "utf8")
    calibration = CalibrationContext("not_applicable", "Synthetic data, no instrument")
    patches = []
    for index in range(3):
        spectrum = (
            Spectrum((400, 410, 420), (-0.25, 0, 101.5), "reflectance", "%")
            if index == 0
            else Spectrum((410, 425.5, 700), (-2, 0, 8), "spectral_radiance", "mW/(m^2 sr nm)")
            if index == 1
            else None
        )
        patches.append(
            PatchResult(
                index,
                f"patch-{index}",
                Observation(
                    schema_version=1,
                    observation_id=f"observation-{index}",
                    operation_id="operation-1",
                    sample_id=f"patch-{index}",
                    acquired_at="2026-09-19T12:34:56+00:00",
                    provenance="simulated",
                    device=device,
                    requested_mode="synthetic_scan",
                    actual_mode="synthetic_scan",
                    configuration=(("_caller field invalid for MATLAB", "☀️"), ("1", "0001")),
                    calibration=calibration,
                    backend_reported=ReportedValues(
                        xyz=XYZ((-1, 0, 105), "1", "Y=100", "CIE1931_2", "D50")
                        if index != 1
                        else None,
                        spectrum=spectrum,
                    ),
                    project_computed=ComputedValues(),
                    raw_backend_output='Synthetic native text: 測定 café\r\n{"nullable": null}\n',
                    warnings=("SIMULATED",),
                ),
            )
        )
    request = json.dumps(
        {
            "device_id": device.device_id,
            "mode": "synthetic_scan",
            "workflow": "strip",
            "patch_ids": [patch.patch_id for patch in patches],
            "options": {"future": None},
        },
        indent=2,
    )
    return OperationResult(
        1,
        "operation-1",
        device.device_id,
        device,
        "simulated",
        "synthetic_scan",
        "strip",
        "completed",
        tuple(p.patch_id for p in patches),
        tuple(patches),
        request,
        (("mode_readback", "synthetic_scan"),),
        calibration,
        None,
        True,
        "not_applicable",
    )


def test_json_publication_roundtrip_and_explicit_format(result, tmp_path):
    saved = save(result, tmp_path / "outcome.any_extension")
    path = saved.path
    assert path.read_text() == result_to_json(result)
    assert load(path) == result
    for fn in (save, load):
        args = (result, path) if fn is save else (path,)
        with pytest.raises(ValueError, match="format must be"):
            fn(*args, format="unknown")


def test_json_does_not_need_mat_dependencies(result, tmp_path, monkeypatch):
    original = builtins.__import__

    def no_optional(name, *args, **kwargs):
        if name == "numpy" or name.startswith("scipy"):
            raise ImportError("intentionally absent optional dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_optional)
    path = save(result, tmp_path / "without-scipy.json").path
    assert load(path) == result
    with pytest.raises(ImportError, match=r"color-measurement\[mat\]"):
        to_mat(result)


def test_shared_native_normalizer_binds_original_values_and_observation_id(result):
    value = retained_snapshot(
        [result.patches[0].observation], "segment", "1" * 64, source_id="source.mat"
    )
    item = value["records"][0]
    expected = item["context"]["material"]
    item["name"] = "editable annotation"
    item["context"]["material"] = {"caller": "must not substitute native semantics"}
    item["context"]["provenance"].update(
        canonicalOperationJson=result_to_json(result),
        canonicalObservationId=result.patches[0].observation.observation_id,
    )
    normalized = json.loads(normalize_snapshot_json(json.dumps(value)))
    assert normalized["records"][0]["context"]["material"] == expected
    assert normalized["records"][0]["id"] == result.patches[0].observation.observation_id
    assert normalized["records"][0]["name"] == "editable annotation"
    with pytest.raises(ValueError, match="identity differs"):
        changed = json.loads(json.dumps(value))
        changed["records"][0]["id"] = "renamed-id"
        changed["ordinary_record_ids"] = ["renamed-id"]
        normalize_snapshot_json(json.dumps(changed))
    item["xyz"]["values"] = [item["xyz"]["values"][0] + 1, *item["xyz"]["values"][1:]]
    with pytest.raises(ValueError, match="quantities differ"):
        normalize_snapshot_json(json.dumps(value))


def test_native_material_excludes_transport_replies_counts_and_average_budget(result):
    original = result.patches[0].observation
    facts = {
        "identity": {"model": "unit", "firmware": "1"},
        "requested": {"averages": 1, "mode": 2},
        "capture": {"reply_count": 1},
        "replies": [{"raw": "first reply"}],
        "settings": [{"key": "Filter", "value": "M2", "presence": "present", "driver_set": True}],
    }
    first = replace(
        original,
        configuration=(
            ("native_facts_json", json.dumps(facts)),
            ("averages", "1"),
            ("port", "port1"),
        ),
    )
    facts["requested"]["averages"] = 5
    facts["capture"]["reply_count"] = 2
    facts["replies"] = [{"raw": "a different reply"}]
    second = replace(
        first,
        configuration=(
            ("native_facts_json", json.dumps(facts)),
            ("averages", "5"),
            ("port", "port2"),
        ),
    )
    a, b = [
        retained_snapshot([o], "segment", "1" * 64, source_id="source.mat")["records"][0]
        for o in (first, second)
    ]
    assert a["context"]["material"] == b["context"]["material"]
    assert a["context"]["provenance"] != b["context"]["provenance"]
    facts["settings"][0]["value"] = "M3"
    third = replace(second, configuration=(("native_facts_json", json.dumps(facts)),))
    changed = retained_snapshot([third], "segment", "1" * 64, source_id="source.mat")["records"][0]
    assert changed["context"]["material"] != a["context"]["material"]


def test_calibration_explanation_is_retained_without_splitting_equal_readiness(result, tmp_path):
    from color_measurement.instrument import calibration_context

    initial = calibration_context({"remaining_calibrations": 0})
    kept = calibration_context(
        {"remaining_calibrations": 0, "initial_calibration_suppressed": True, "performed": False}
    )
    assert initial.status == kept.status == "valid" and initial.detail != kept.detail
    original = replace(
        result.patches[0].observation,
        calibration=initial,
        backend_reported=ReportedValues(
            xyz=XYZ((7, 12, 19), "1", "relative; perfect-diffuser Y=100", "CIE1931_2", "D50")
        ),
    )
    continued = replace(original, observation_id="continued-observation", calibration=kept)
    source = retained_snapshot(
        [original, continued], "new-segment", "1" * 64, source_id="continued.mat"
    )
    first, second = source["records"]
    assert first["context"]["material"] == second["context"]["material"]
    assert first["context"]["provenance"]["calibration"] == dict(
        status="valid", detail=initial.detail
    )
    assert second["context"]["provenance"]["calibration"] == dict(
        status="valid", detail=kept.detail
    )
    source["ordinary_record_ids"] = [continued.observation_id]
    source["charts"] = [
        dict(
            id="same-pass",
            provenance="simulated",
            total_cells=2,
            target=None,
            rows=[
                dict(record_id=row["id"], sample_id=str(i + 1), sample_loc=f"A{i + 1}")
                for i, row in enumerate(source["records"])
            ],
        )
    ]
    exported = json.loads(update_json(json.dumps(source), str(tmp_path)))
    selected = next(file for file in exported["files"] if file["scope"] == "chart_reference")
    assert selected["state"] == "current" and selected["row_count"] == 2
    # The readiness fact remains material; an explanation change is not a waiver.
    changed = replace(continued, calibration=CalibrationContext("required", "Tile required"))
    other = retained_snapshot([changed], "new-segment", "1" * 64, source_id="continued.mat")[
        "records"
    ][0]
    assert other["context"]["material"] != first["context"]["material"]
    source["records"][1] = other
    refused = json.loads(update_json(json.dumps(source), str(tmp_path), json.dumps(exported)))
    selected = next(file for file in refused["files"] if file["scope"] == "chart_reference")
    assert selected["state"] == "stale" and "incompatible" in selected["reason"]


def test_native_projection_keeps_row_local_provenance_and_decodes_operation_once(
    result, tmp_path, monkeypatch
):
    from color_measurement import ti3_exports

    result = replace(
        result,
        patches=tuple(
            replace(
                patch,
                observation=replace(
                    patch.observation,
                    backend_reported=ReportedValues(
                        xyz=XYZ(
                            (-1, 0, 105),
                            "1",
                            "relative; perfect-diffuser Y=100",
                            "CIE1931_2",
                            "D50",
                        )
                    ),
                ),
            )
            for patch in result.patches
        ),
    )
    canonical = result_to_json(result)
    value = retained_snapshot(
        [p.observation for p in result.patches[:2]], "segment", "1" * 64, source_id="source.mat"
    )
    for item in value["records"]:
        item["context"]["provenance"].update(
            canonicalOperationJson=canonical,
            canonicalObservationId=item["id"],
            canonicalObservation=json.loads(canonical)["patches"][0]["observation"],
        )
    original = ti3_exports.result_from_json
    calls = []

    def decode(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(ti3_exports, "result_from_json", decode)
    text = normalize_snapshot_json(json.dumps(value))
    normalized = json.loads(text)
    assert calls == [canonical]
    assert [r["id"] for r in normalized["records"]] == [
        p.observation.observation_id for p in result.patches[:2]
    ]
    assert "canonicalOperationJson" not in text and 'canonicalObservation"' not in text
    for item, patch in zip(normalized["records"], result.patches):
        provenance = item["context"]["provenance"]
        assert (
            provenance["canonicalOperationSha256"] == hashlib.sha256(canonical.encode()).hexdigest()
        )
        assert provenance["canonicalOperationId"] == result.operation_id
        assert provenance["canonicalObservationId"] == patch.observation.observation_id
        assert provenance["acquired_at"] == patch.observation.acquired_at
    exported = json.loads(update_json(text, str(tmp_path)))
    assert exported["status"] == "current"
    (file,) = exported["files"]
    assert file["row_ids"] == [patch.observation.observation_id for patch in result.patches[:2]]
    payload = Path(file["path"]).read_text()
    assert "canonicalOperationJson" not in payload
    assert "canonicalObservationId" in payload


def test_linked_native_history_keeps_original_operation_rows_and_source_files(result, tmp_path):
    old = replace(
        result,
        patches=tuple(
            replace(
                patch,
                observation=replace(
                    patch.observation,
                    backend_reported=ReportedValues(
                        xyz=XYZ(
                            (patch.index + 1, patch.index + 10, patch.index + 20),
                            "1",
                            "relative; perfect-diffuser Y=100",
                            "CIE1931_2",
                            "D50",
                        )
                    ),
                ),
            )
            for patch in result.patches
        ),
    )
    old_json = result_to_json(old)
    source = retained_snapshot(
        [p.observation for p in old.patches],
        "original-segment",
        "1" * 64,
        source_id=str(tmp_path / "original.mat"),
    )
    for row in source["records"]:
        row["context"]["provenance"].update(
            canonicalOperationJson=old_json, canonicalObservationId=row["id"]
        )
    original = json.dumps(source)
    published = json.loads(update_json(json.dumps(source), str(tmp_path)))
    before = {file["path"]: Path(file["path"]).read_bytes() for file in published["files"]}
    linked = json.loads(json.dumps(source))
    linked.update(
        owner_id="new-segment",
        source_id=str(tmp_path / "moved" / "continued.mat"),
        source_sha256="2" * 64,
        ordinary_record_ids=[],
    )
    linked["charts"] = [
        dict(
            id="same-pass",
            provenance="simulated",
            total_cells=3,
            target=None,
            rows=[
                dict(
                    record_id=p.observation.observation_id,
                    sample_id=str(p.index + 1),
                    sample_loc=f"A{p.index + 1}",
                )
                for p in old.patches[:2]
            ],
        )
    ]
    normalized = json.loads(normalize_snapshot_json(json.dumps(linked)))
    assert normalized["ordinary_record_ids"] == []
    for row, patch in zip(normalized["records"], old.patches):
        assert row["id"] == patch.observation.observation_id
        assert row["context"]["provenance"]["canonicalOperationId"] == old.operation_id
        assert row["context"]["provenance"]["canonicalObservationId"] == row["id"]
    exported = json.loads(update_json(json.dumps(linked), str(tmp_path)))
    (file,) = exported["files"]
    assert file["scope"] == "chart_reference"
    assert file["row_ids"] == [p.observation.observation_id for p in old.patches[:2]]
    assert file["row_count"] == 2 and file["total_rows"] == 3
    assert all(Path(path).read_bytes() == data for path, data in before.items())
    assert json.dumps(source) == original and result_to_json(old) == old_json
    # Even an unselected inherited observation remains bound to its canonical values.
    linked["records"][2]["xyz"]["values"][0] += 1
    refused = json.loads(update_json(json.dumps(linked), str(tmp_path / "refused")))
    assert refused["status"] == "unavailable" and refused["snapshot_id"] is None
    assert "quantities differ" in refused["unavailable"][0]["reason"]
    assert not (tmp_path / "refused").exists()


def test_zero_reading_operation_and_projection_error_do_not_lose_canonical_save(result, tmp_path):
    empty = replace(result, status="failed", patches=(), error="failed before reading")
    saved = save(empty, tmp_path / "empty.json")
    assert load(saved.path) == empty
    assert saved.ti3["status"] == "unavailable" and saved.ti3["files"] == []
    assert saved.ti3["unavailable"][0]["record_id"] == empty.operation_id
    value = dict(
        schema_version=3,
        owner_id="segment",
        source_id="source.mat",
        source_sha256="1" * 64,
        ordinary_record_ids=[empty.operation_id],
        records=[
            dict(
                id=empty.operation_id,
                name="",
                notes="",
                kind="configuration",
                provenance=empty.provenance,
                xyz=None,
                spectrum=None,
                context=dict(
                    route="argyll",
                    material={},
                    provenance=dict(
                        canonicalOperationJson=result_to_json(empty),
                        canonicalOperationId=empty.operation_id,
                    ),
                ),
            )
        ],
        charts=[],
    )
    normalized = json.loads(normalize_snapshot_json(json.dumps(value)))
    assert normalized["records"][0]["id"] == empty.operation_id
    assert "canonicalOperationJson" not in normalized["records"][0]["context"]["provenance"]
    value["records"][0]["id"] = "renamed-outcome"
    value["ordinary_record_ids"] = ["renamed-outcome"]
    with pytest.raises(ValueError, match="canonical operation"):
        normalize_snapshot_json(json.dumps(value))
    bad = replace(result.patches[0].observation, configuration=(("native_facts_json", "[]"),))
    invalid = replace(
        result,
        patches=(replace(result.patches[0], observation=bad),),
        status="failed",
        error="retained partial data",
    )
    saved = save(invalid, tmp_path / "still-valid-source.json")
    assert load(saved.path) == invalid
    assert saved.ti3["status"] == "unavailable"


@pytest.mark.parametrize(
    "canonical",
    ["not-json", "[]", "[" * 100000 + "0" + "]" * 100000],
    ids=["malformed", "wrong-shape", "deep"],
)
def test_canonical_operation_decode_failure_is_a_typed_optional_refusal(
    result, tmp_path, canonical
):
    value = retained_snapshot(
        [result.patches[0].observation], "segment", "1" * 64, source_id="source.mat"
    )
    value["records"][0]["context"]["provenance"].update(
        canonicalOperationJson=canonical,
        canonicalObservationId=result.patches[0].observation.observation_id,
    )
    with pytest.raises(ValueError):
        normalize_snapshot_json(json.dumps(value))
    refused = json.loads(update_json(json.dumps(value), str(tmp_path / "companions")))
    assert refused["status"] == "unavailable" and not refused["retryable"]
    assert refused["snapshot_id"] is None and refused["files"] == []
    assert refused["relocatable"] is False
    assert refused["unavailable"][0]["component"] == "record"
    assert not (tmp_path / "companions").exists()


def invalidated_timing_snapshot(operation):
    return dict(
        schema_version=3,
        owner_id="segment",
        source_id="source.mat",
        ordinary_record_ids=[operation.operation_id],
        source_sha256="1" * 64,
        charts=[],
        records=[
            dict(
                id=operation.operation_id,
                name="Timing capture",
                notes="Retained failure",
                kind="timing",
                provenance=operation.provenance,
                xyz=None,
                spectrum=None,
                context=dict(
                    route="argyll",
                    material={},
                    provenance=dict(
                        canonicalOperationJson=result_to_json(operation),
                        canonicalOperationId=operation.operation_id,
                        presentationInvalidated=True,
                        presentationInvalidationReason="The timing display moved during capture.",
                    ),
                ),
            )
        ],
    )


@pytest.mark.parametrize("with_reading", [False, True])
def test_invalidated_timing_is_accounted_without_losing_valid_color_or_native_result(
    result, tmp_path, with_reading
):
    timing = importlib.import_module("test_display_timing_frontend").timing_result()
    if not with_reading:
        timing = replace(timing, patches=(), status="cancelled", error="Cancelled before capture")
    canonical = result_to_json(timing)
    value = invalidated_timing_snapshot(timing)
    color = retained_snapshot(
        [result.patches[0].observation], "segment", "1" * 64, source_id="source.mat"
    )["records"][0]
    color["xyz"]["scale"] = "relative; perfect-diffuser Y=100"
    value["records"].append(color)
    value["ordinary_record_ids"].append(color["id"])
    normalized = json.loads(normalize_snapshot_json(json.dumps(value)))
    disposition = normalized["records"][0]
    assert disposition["id"] == timing.operation_id and disposition["kind"] == "timing"
    assert disposition["xyz"] is None and disposition["spectrum"] is None
    provenance = disposition["context"]["provenance"]
    assert provenance["canonicalOperationId"] == timing.operation_id
    assert provenance["canonicalOperationSha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert provenance["presentationInvalidated"] is True
    assert provenance["presentationInvalidationReason"] == (
        "The timing display moved during capture."
    )
    exported = json.loads(update_json(json.dumps(value), str(tmp_path)))
    assert exported["status"] == "current" and not exported["retryable"]
    (refused,) = exported["unavailable"]
    assert refused == dict(
        record_id=timing.operation_id,
        component="record",
        reason=("The timing display moved during capture."),
    )
    assert [identity for file in exported["files"] for identity in file["row_ids"]] == [color["id"]]
    assert result_to_json(timing) == canonical
    assert value["records"][0]["context"]["provenance"]["canonicalOperationJson"] == canonical
    assert json.loads(update_json(json.dumps(normalized), str(tmp_path))) == exported


@pytest.mark.parametrize(
    "mutation",
    [
        "flag-false",
        "flag-number",
        "flag-missing",
        "reason-missing",
        "reason-blank",
        "reason-number",
        "operation-id",
        "record-id",
        "kind",
        "provenance",
        "xyz",
        "workflow",
    ],
)
def test_invalidated_timing_bridge_refuses_unbound_or_borrowed_dispositions(result, mutation):
    timing = importlib.import_module("test_display_timing_frontend").timing_result()
    value = invalidated_timing_snapshot(timing)
    row = value["records"][0]
    provenance = row["context"]["provenance"]
    if mutation == "flag-false":
        provenance["presentationInvalidated"] = False
    elif mutation == "flag-number":
        provenance["presentationInvalidated"] = 1
    elif mutation == "flag-missing":
        del provenance["presentationInvalidated"]
    elif mutation == "reason-missing":
        del provenance["presentationInvalidationReason"]
    elif mutation == "reason-blank":
        provenance["presentationInvalidationReason"] = "   "
    elif mutation == "reason-number":
        provenance["presentationInvalidationReason"] = 5
    elif mutation == "operation-id":
        provenance["canonicalOperationId"] = "another-operation"
    elif mutation == "record-id":
        row["id"] = timing.patches[0].observation.observation_id
        value["ordinary_record_ids"] = [row["id"]]
    elif mutation == "kind":
        row["kind"] = "configuration"
    elif mutation == "provenance":
        row["provenance"] = "physical"
    elif mutation == "xyz":
        row["xyz"] = retained_snapshot(
            [result.patches[0].observation], "segment", "1" * 64, source_id="source.mat"
        )["records"][0]["xyz"]
    elif mutation == "workflow":
        provenance["canonicalOperationJson"] = result_to_json(result)
    with pytest.raises(ValueError):
        normalize_snapshot_json(json.dumps(value))


@pytest.mark.parametrize("reason", [None, "   ", 5])
def test_normalized_invalidated_timing_still_requires_a_typed_reason(tmp_path, reason):
    timing = importlib.import_module("test_display_timing_frontend").timing_result()
    value = json.loads(normalize_snapshot_json(json.dumps(invalidated_timing_snapshot(timing))))
    value["records"][0]["context"]["provenance"]["presentationInvalidationReason"] = reason
    with pytest.raises(ValueError, match="presentation invalidation reason"):
        update_json(json.dumps(value), str(tmp_path / "companions"))
    assert not (tmp_path / "companions").exists()


@pytest.mark.parametrize("requested", [[], 5, None])
def test_malformed_retained_native_requested_preserves_durable_save_and_cli(
    result, tmp_path, capsys, requested
):
    from color_measurement.cli import main
    from color_measurement.exports import from_json, to_json
    from color_measurement.exports import save as save_observation

    original = result.patches[0].observation
    observation = replace(
        original, configuration=(("native_facts_json", json.dumps({"requested": requested})),)
    )
    operation = replace(
        result, patches=(replace(result.patches[0], observation=observation), *result.patches[1:])
    )
    saved = save_observation(observation, tmp_path / "observation.json")
    assert from_json(saved.path.read_text()) == observation
    assert saved.ti3["status"] == "unavailable" and saved.ti3["snapshot_id"] is None
    assert saved.ti3["relocatable"] is False
    assert "requested member must be an object" in saved.ti3["unavailable"][0]["reason"]
    saved_operation = save(operation, tmp_path / "operation.json")
    assert load(saved_operation.path) == operation
    assert saved_operation.ti3["snapshot_id"] is None
    assert saved_operation.ti3["relocatable"] is False
    destination = tmp_path / "cli.json"
    assert main(["export", str(saved.path), "--output", str(destination)]) == 0
    output = capsys.readouterr()
    assert "Saved complete simulated record" in output.err
    assert "TI3 companions: unavailable" in output.err and "Traceback" not in output.err
    assert from_json(destination.read_text()) == observation
    # Direct projection rejects its opaque malformed facts without an AttributeError.
    with pytest.raises(ValueError, match="requested member"):
        retained_snapshot(
            [from_json(to_json(observation))], "owner", "1" * 64, source_id="retained.json"
        )


@pytest.mark.parametrize(
    "facts",
    [
        "[[0]]",
        '{"settings":[],"identity":[[0]]}',
    ],
    ids=["array", "identity"],
)
def test_native_facts_decoder_recursion_is_a_typed_optional_refusal_on_save_and_bridge(
    result, tmp_path, capsys, monkeypatch, facts
):
    from color_measurement.cli import main
    from color_measurement.exports import from_json
    from color_measurement.exports import save as save_observation

    # Exercise the decoder failure, independent of the runtime's supported depth.
    decode = json.loads

    def recurse(value, *args, **kwargs):
        if value == facts:
            raise RecursionError("Synthetic native-facts decoder recursion")
        return decode(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", recurse)
    original = result.patches[0].observation
    bad = replace(original, configuration=(("native_facts_json", facts),))
    operation = replace(
        result, patches=(replace(result.patches[0], observation=bad), *result.patches[1:])
    )
    saved = save_observation(bad, tmp_path / "observation.json")
    saved_operation = save(operation, tmp_path / "operation.json")
    assert from_json(saved.path.read_text()) == bad
    assert load(saved_operation.path) == operation
    for disposition in (saved.ti3, saved_operation.ti3):
        assert disposition["status"] == "unavailable" and not disposition["retryable"]
        assert disposition["snapshot_id"] is None
        assert "nesting" in disposition["unavailable"][0]["reason"]
    assert main(["export", str(saved.path), "--output", str(tmp_path / "cli.json")]) == 0
    assert "Traceback" not in capsys.readouterr().err
    source = retained_snapshot([original], result.operation_id, "1" * 64, source_id="canonical.mat")
    source["records"][0]["context"]["provenance"].update(
        canonicalOperationJson=result_to_json(operation),
        canonicalObservationId=bad.observation_id,
    )
    with pytest.raises(ValueError, match="nesting"):
        normalize_snapshot_json(json.dumps(source))
    refused = json.loads(update_json(json.dumps(source), str(tmp_path / "bridge")))
    assert refused["status"] == "unavailable" and not refused["retryable"]
    assert refused["snapshot_id"] is None and refused["files"] == []


def test_projection_json_reencode_translates_recursion_and_keeps_nested_context(monkeypatch):
    from color_measurement.ti3_exports import _json

    nested = {"identity": [[["retained"]]], "settings": []}
    assert json.loads(_json(nested)) == nested
    encode = json.dumps

    def recurse(value, *args, **kwargs):
        if value is nested:
            raise RecursionError("Synthetic encoder recursion")
        return encode(value, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", recurse)
    with pytest.raises(ValueError, match="nesting"):
        _json(nested)


@pytest.mark.parametrize("keep_active", [False, True])
@pytest.mark.parametrize("retained_payload", [False, True])
def test_canonical_projection_refusal_keeps_retired_rows_and_disposition(
    result, tmp_path, keep_active, retained_payload
):
    from color_measurement.ti3_exports import save_companions

    first_observation = result.patches[0].observation
    second_observation = replace(
        first_observation,
        observation_id=result.patches[1].observation.observation_id,
        sample_id=result.patches[1].observation.sample_id,
        configuration=(("synthetic_material", "other"),),
    )
    result = replace(
        result,
        patches=(
            result.patches[0],
            replace(result.patches[1], observation=second_observation),
            result.patches[2],
        ),
    )
    path = tmp_path / "operation.json"
    first = save(result, path).ti3
    if not retained_payload:
        for file in first["files"]:
            Path(file["path"]).write_bytes(b"foreign bytes")
    source = retained_snapshot(
        [p.observation for p in result.patches],
        result.operation_id,
        first["source_sha256"],
        source_id=str(path.resolve()),
    )
    source["records"] = source["records"][:1] if keep_active else []
    source["ordinary_record_ids"] = [r["id"] for r in source["records"]]
    previous = json.loads(update_json(json.dumps(source), str(tmp_path), json.dumps(first)))
    retired = [file for file in previous["files"] if file["state"] == "retired"]
    assert retired and all(bool(file["payload_sha256"]) == retained_payload for file in retired)
    original = result.patches[0]
    bad = replace(
        result,
        patches=(
            replace(
                original,
                observation=replace(
                    original.observation,
                    configuration=(("native_facts_json", "[]"),),
                ),
            ),
            *result.patches[1:],
        ),
    )
    payload = result_to_json(bad).encode()
    path.write_bytes(payload)
    refused = save_companions(bad, path, payload, previous=previous)
    assert load(path) == bad
    assert len(refused["files"]) == len(previous["files"])
    actual = {file["group_id"]: file for file in refused["files"]}
    for file in retired:
        assert actual[file["group_id"]]["state"] == "retired"
        assert actual[file["group_id"]]["payload_sha256"] == file["payload_sha256"]
        assert actual[file["group_id"]]["row_ids"] == file["row_ids"]
    assert refused["status"] == ("stale" if keep_active else "unavailable")
    assert not refused["retryable"]


@pytest.mark.parametrize("matching_edited_digest", [False, True])
def test_optional_canonical_save_refuses_deep_companion_identity_without_losing_source(
    result, tmp_path, matching_edited_digest
):
    from color_measurement.ti3_exports import save_companions

    path = tmp_path / "operation.json"
    saved = save(result, path)
    canonical = path.read_bytes()
    previous = saved.ti3
    file = previous["files"][0]
    companion = Path(file["path"])
    nested = '"' + "[" * 100000 + "0" + "]" * 100000 + '"'
    original = '"""' + file["row_ids"][0] + '"""'
    changed = companion.read_text().replace(original, nested)
    assert changed != companion.read_text()
    companion.write_text(changed)
    if matching_edited_digest:
        file["payload_sha256"] = hashlib.sha256(companion.read_bytes()).hexdigest()
    refused = save_companions(result, path, canonical, previous=previous)
    assert refused["status"] == "stale" and not refused["retryable"]
    assert refused["files"][0]["payload_sha256"] is None
    reason = refused["files"][0]["reason"]
    if matching_edited_digest:
        assert "nesting" in reason or "retained row identities differ" in reason
    else:
        assert "bytes changed" in reason
    assert path.read_bytes() == canonical and load(path) == result
    assert companion.read_text() == changed


@pytest.mark.parametrize(
    "state,retryable,relocatable",
    [
        ("current", False, False),
        ("retired", False, False),
        ("stale", True, True),
        ("stale", False, True),
        ("unavailable", False, False),
        ("stale", False, False),
    ],
)
def test_cli_ti3_status_has_no_trailing_space_and_reports_actionable_retryability(
    result, tmp_path, capsys, monkeypatch, state, retryable, relocatable
):
    import color_measurement.cli as cli
    from color_measurement.exports import save as save_observation

    observation = result.patches[0].observation
    source = save_observation(observation, tmp_path / "source.json").path
    real_save = cli.save

    def saved(*args, **kwargs):
        value = real_save(*args, **kwargs)
        return replace(
            value,
            ti3=dict(
                value.ti3,
                status=state if state != "retired" else "unavailable",
                retryable=retryable,
                relocatable=relocatable,
                files=[
                    dict(
                        state=state,
                        path="output.ti3",
                        reason="" if state in ("current", "retired") else "specific refusal",
                    )
                ],
                unavailable=[],
            ),
        )

    monkeypatch.setattr(cli, "save", saved)
    assert cli.main(["export", str(source), "--output", str(tmp_path / "dest.json")]) == 0
    lines = capsys.readouterr().err.splitlines()
    assert all(line == line.rstrip() for line in lines)
    if state in ("stale", "unavailable"):
        assert f"retryable={'true' if retryable else 'false'}" in "\n".join(lines)
        assert f"relocatable={'true' if relocatable else 'false'}" in "\n".join(lines)
        assert "specific refusal" in "\n".join(lines)
    else:
        assert f"{state}: output.ti3" in lines


@pytest.mark.parametrize("first,second", [("csv", "json"), ("json", "csv")])
def test_two_canonical_saves_own_distinct_companions_without_overwrite(
    result, tmp_path, capsys, first, second
):
    from color_measurement.cli import main
    from color_measurement.exports import save as save_observation

    original = result.patches[0].observation
    observation = replace(
        original,
        backend_reported=replace(
            original.backend_reported,
            xyz=XYZ((1, 2, 3), "1", "relative; perfect-diffuser Y=100", "CIE1931_2", "D50"),
        ),
    )
    one = save_observation(observation, tmp_path / f"saved.{first}", format=first)
    assert one.ti3["status"] == "current"
    owned = {f["path"]: Path(f["path"]).read_bytes() for f in one.ti3["files"]}
    two = save_observation(observation, tmp_path / f"saved.{second}", format=second)
    assert two.ti3["status"] == "current"
    assert one.ti3["source_id"] != two.ti3["source_id"]
    assert not set(owned) & {f["path"] for f in two.ti3["files"]}
    assert all(Path(path).read_bytes() == data for path, data in owned.items())

    # The documented CLI recovery can export the existing source into JSON/CSV.
    destination = tmp_path / f"recovered.{second}"
    assert (
        main(
            [
                "export",
                str(one.path),
                "--input-format",
                first,
                "--output",
                str(destination),
                "--format",
                second,
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert "TI3 companions: current" in output.err and "not owned" not in output.err
    assert all(Path(path).read_bytes() == data for path, data in owned.items())


def test_optional_failure_identity_is_absent_or_actual_snapshot_digest(result, tmp_path):
    import hashlib

    from color_measurement.exports import save as save_observation
    from color_measurement.ti3_exports import save_companions

    observation = result.patches[0].observation
    saved = save_observation(observation, tmp_path / "identity.json")
    payload = saved.path.read_bytes()
    source = hashlib.sha256(payload).hexdigest()
    expected = retained_snapshot(
        [observation], observation.observation_id, source, source_id=str(saved.path.resolve())
    )
    canonical = json.dumps(
        expected, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode("ascii")
    failed = save_companions(observation, saved.path, payload, previous={"invalid": "shape"})
    assert failed["status"] == "unavailable"
    assert failed["snapshot_id"] == hashlib.sha256(canonical).hexdigest()
    assert failed["snapshot_id"] != failed["source_sha256"]


@pytest.mark.parametrize("requested", [[], 5, None])
def test_canonical_native_refusal_retains_authenticated_files_and_recovers(
    result, tmp_path, requested
):
    from color_measurement.ti3_exports import save_companions

    path = tmp_path / "canonical.json"
    first = save(result, path).ti3
    assert first["status"] == "current" and first["files"]
    before = {file["path"]: Path(file["path"]).read_bytes() for file in first["files"]}
    original = result.patches[0]
    bad = replace(
        result,
        patches=(
            replace(
                original,
                observation=replace(
                    original.observation,
                    configuration=(("native_facts_json", json.dumps({"requested": requested})),),
                ),
            ),
            *result.patches[1:],
        ),
    )
    bad_payload = result_to_json(bad).encode()
    path.write_bytes(bad_payload)
    failed_save = save_companions(bad, path, bad_payload, previous=first)
    assert failed_save["status"] == "stale"
    assert failed_save["snapshot_id"] is None and not failed_save["retryable"]
    assert [file["payload_sha256"] for file in failed_save["files"]] == [
        file["payload_sha256"] for file in first["files"]
    ]
    assert all(file["state"] == "stale" for file in failed_save["files"])
    path.write_bytes(result_to_json(result).encode("utf-8"))
    assert save_companions(result, path, path.read_bytes(), previous=failed_save) == first

    # MATLAB passes the immutable operation into the same update boundary.
    good = retained_snapshot(
        [p.observation for p in result.patches],
        result.operation_id,
        first["source_sha256"],
        source_id=str(path.resolve()),
    )
    for row in good["records"]:
        row["context"]["provenance"].update(
            canonicalOperationJson=result_to_json(bad), canonicalObservationId=row["id"]
        )
    refused = json.loads(update_json(json.dumps(good), str(tmp_path), json.dumps(first)))
    assert refused["status"] == "stale" and refused["snapshot_id"] is None
    assert not refused["retryable"]
    assert "requested member" in refused["unavailable"][0]["reason"]
    assert all(Path(name).read_bytes() == payload for name, payload in before.items())
    for row in good["records"]:
        row["context"]["provenance"]["canonicalOperationJson"] = result_to_json(result)
    recovered = json.loads(update_json(json.dumps(good), str(tmp_path), json.dumps(refused)))
    assert recovered["status"] == "current"


@pytest.mark.parametrize(
    "module,factory",
    [
        ("test_refresh_frequency", "outcome"),
        ("test_display_timing_frontend", "timing_result"),
    ],
)
def test_canonical_scalar_and_timing_save_keep_native_kind_and_explicit_disposition(
    tmp_path, module, factory
):
    original = getattr(importlib.import_module(module), factory)()
    saved = save(original, tmp_path / f"{original.workflow}.json")
    assert load(saved.path) == original
    assert saved.ti3["status"] == "unavailable" and saved.ti3["files"] == []
    (disposition,) = saved.ti3["unavailable"]
    observation = original.patches[0].observation
    assert disposition["record_id"] == observation.observation_id
    assert observation.record_kind in disposition["reason"]
    assert set(tmp_path.iterdir()) == {saved.path}


def test_canonical_zero_patch_calibration_save_is_not_a_measurement(result, tmp_path):
    request = json.loads(result.request_json)
    request.update(workflow="calibrate", patch_ids=[])
    original = replace(
        result,
        workflow="calibrate",
        patches=(),
        expected_patch_ids=(),
        request_json=json.dumps(request),
    )
    saved = save(original, tmp_path / "calibration.json")
    assert load(saved.path) == original
    assert saved.ti3["status"] == "unavailable" and saved.ti3["files"] == []
    assert saved.ti3["unavailable"][0]["record_id"] == original.operation_id
    assert "configuration" in saved.ti3["unavailable"][0]["reason"]
    assert set(tmp_path.iterdir()) == {saved.path}


def test_canonical_partial_failed_color_save_still_exports_retained_eligible_xyz(result, tmp_path):
    first = result.patches[0]
    xyz = replace(first.observation.backend_reported.xyz, scale="relative; perfect-diffuser Y=100")
    reading = replace(
        first.observation, backend_reported=replace(first.observation.backend_reported, xyz=xyz)
    )
    original = replace(
        result,
        patches=(replace(first, observation=reading),),
        status="failed",
        error="failure after retaining valid data",
    )
    saved = save(original, tmp_path / "partial.json")
    assert load(saved.path) == original
    assert saved.ti3["status"] == "current"
    (file,) = saved.ti3["files"]
    assert file["row_ids"] == [reading.observation_id]
    assert file["row_count"] == 1 and file["scope"] == "ordinary"
    assert Path(file["path"]).is_file()


ACQUISITION_MODULES = {
    "operations",
    "instrument",
    "instrument_frontend",
    "native_protocol",
    "discovery",
    "frontend",
}


def _imported_package_modules(source):
    """Read direct project-module dependencies without executing the source."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(
                alias.name.split(".")[1]
                for alias in node.names
                if alias.name.startswith("color_measurement.")
            )
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                if node.module:
                    names.add(node.module.split(".")[0])
                else:
                    names.update(alias.name for alias in node.names)
            elif node.level == 0 and node.module == "color_measurement":
                names.update(alias.name for alias in node.names)
            elif node.level == 0 and (node.module or "").startswith("color_measurement."):
                names.add(node.module.split(".")[1])
    return names


@pytest.mark.parametrize(
    "source,expected",
    [
        ("import color_measurement.frontend as ui", {"frontend"}),
        ("import color_measurement.frontend.child", {"frontend"}),
        ("from color_measurement.instrument import Instrument", {"instrument"}),
        ("from .operations import SimulatedDevice", {"operations"}),
        ("from . import native_protocol as native", {"native_protocol"}),
        ("from color_measurement import discovery as devices", {"discovery"}),
        ("from example import frontend", set()),
        ("from .models import Observation", set()),
    ],
)
def test_export_import_census_recognizes_project_import_forms(source, expected):
    assert _imported_package_modules(source) & ACQUISITION_MODULES == expected


def test_export_modules_do_not_directly_import_acquisition():
    """Enforce the direct dependency boundary, including package-level imports.

    This source check covers neither transitive nor dynamic imports. Persistence
    failure tests separately check retained payloads and explicit retry behavior.
    """
    import color_measurement.exports as exports
    import color_measurement.operation_exports as operation_exports

    for module in (exports, operation_exports):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not _imported_package_modules(source) & ACQUISITION_MODULES, module.__name__


@pytest.mark.parametrize("format", ["json", "mat"])
def test_failure_retains_payload_and_result_for_retry(result, tmp_path, format):
    if format == "mat":
        pytest.importorskip("scipy.io")
    with pytest.raises(OperationSaveError) as failure:
        save(result, tmp_path / "missing" / "result", format=format)
    assert failure.value.result is result
    assert failure.value.format == format
    assert isinstance(failure.value.payload, bytes)
    if format == "mat":
        assert from_mat(failure.value.payload) == result
    else:
        assert failure.value.payload.decode() == result_to_json(result)
    saved = save(failure.value.result, tmp_path / "復元 café", format=format)
    path = saved.path
    assert load(path, format=format) == result
    previous = path.read_bytes()
    with pytest.raises(OperationSaveError, match="Could not save"):
        save(result, path, format=format)
    assert path.read_bytes() == previous
    expected = {path, *(Path(f["path"]) for f in saved.ti3["files"])}
    if saved.ti3["files"]:
        expected.add(tmp_path / ".ti3-export.lock")
    assert set(tmp_path.iterdir()) == expected


def test_failed_atomic_publish_has_no_partial_file(result, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("synthetic filesystem refusal")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OperationSaveError) as failure:
        save(result, tmp_path / "outcome.json")
    assert failure.value.result is result
    assert list(tmp_path.iterdir()) == []


def test_mat_native_quantities_metadata_and_exact_canonical_json(result, mat):
    np, scipy_io = mat
    payload = to_mat(result)
    assert from_mat(payload) == result
    data = scipy_io.loadmat(io.BytesIO(payload), struct_as_record=False, mat_dtype=True)
    assert data["record_json"].item() == result_to_json(result)
    operation = data["operation"].item()
    assert operation.patches.shape == (1, 3)
    assert operation.device.item().serial.item() == "0000123"
    model = operation.device.item().model.item()
    assert model.encoding.item() == "UTF-16"
    assert model.code_units.dtype == np.uint16
    assert model.code_units.astype("<u2").tobytes().decode("utf-16-le") == "測定 café 🔬"
    reflective = operation.patches[0, 0].item()
    assert np.array_equal(reflective.xyz_values, [[-1, 0, 105]])
    assert np.array_equal(reflective.spectral_values, [[-0.25, 0, 101.5]])
    assert reflective.configuration.shape == (2, 2)
    assert reflective.configuration[0, 0].item() == "_caller field invalid for MATLAB"
    assert reflective.configuration[0, 1].item() == "☀️"
    spectral_only = operation.patches[0, 1].item()
    assert np.array_equal(spectral_only.wavelengths_nm, [[410, 425.5, 700]])
    assert spectral_only.spectral_unit.item() == "mW/(m^2 sr nm)"
    assert spectral_only.xyz_values.shape == (1, 0)
    assert spectral_only.xyz_present.dtype == np.bool_
    assert not spectral_only.xyz_present.item()
    xyz_only = operation.patches[0, 2].item()
    assert xyz_only.spectral_values.shape == (1, 0)
    assert not xyz_only.spectrum_present.item()
    assert operation.software_stopped.item()


@pytest.mark.parametrize(
    "status,indices",
    [
        ("failed", ()),
        ("cancelled", (0, 2)),
        ("failed", (0, 1, 2)),
    ],
)
def test_mat_failed_cancelled_and_partial_outcomes_retain_requested_indices(
    result, mat, status, indices
):
    partial = replace(
        result,
        status=status,
        patches=tuple(result.patches[i] for i in indices),
        error="Synthetic failure",
        software_stopped=False,
        instrument_state="unknown",
    )
    assert from_mat(to_mat(partial)) == partial
    data = mat[1].loadmat(io.BytesIO(to_mat(partial)), struct_as_record=False)
    operation = data["operation"].item()
    assert operation.patches.shape == (1, len(indices))
    assert operation.expected_patch_ids.shape == (1, 3)


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_mat_zero_patch_calibration_and_null_serial(result, mat, status):
    request = json.loads(result.request_json)
    request.update(workflow="calibrate", patch_ids=[])
    calibration = replace(
        result,
        workflow="calibrate",
        status=status,
        expected_patch_ids=(),
        patches=(),
        device=replace(result.device, serial=None, serial_encoding=None),
        request_json=json.dumps(request),
        error=None if status == "completed" else "Synthetic calibration stop",
    )
    assert from_mat(to_mat(calibration)) == calibration
    operation = (
        mat[1]
        .loadmat(io.BytesIO(to_mat(calibration)), struct_as_record=False, mat_dtype=True)[
            "operation"
        ]
        .item()
    )
    assert operation.patches.shape == (1, 0)
    assert not operation.device.item().serial_present.item()


def test_mat_retains_native_byte_serial_encoding(result, mat):
    device = replace(result.device, serial="í\u00a0\u0080", serial_encoding="bytes")
    byte_result = replace(
        result,
        device=device,
        patches=tuple(
            replace(patch, observation=replace(patch.observation, device=device))
            for patch in result.patches
        ),
    )
    payload = to_mat(byte_result)
    assert from_mat(payload) == byte_result
    exported = mat[1].loadmat(io.BytesIO(payload), struct_as_record=False)["operation"].item()
    assert exported.device.item().serial_encoding.item() == "bytes"


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "numeric",
        "grid",
        "unit",
        "provenance",
        "index",
        "availability",
        "orientation",
        "missing",
        "extra",
        "version",
        "canonical",
        "unicode",
        "unicode_type",
        "unicode_encoding",
    ],
)
def test_mat_rejects_changed_convenience_or_canonical_fields(result, mat, mutation):
    np, scipy_io = mat
    # Record arrays survive savemat unchanged; mat_struct objects do not, and their
    # re-save alone failed the inspection-view check, so every case passed.
    data = scipy_io.loadmat(io.BytesIO(to_mat(result)), mat_dtype=True)
    data = {key: value for key, value in data.items() if not key.startswith("__")}

    def field(struct, name):
        return struct[name][0, 0]

    def assign(struct, name, value):
        struct[name][0, 0] = value

    operation = data["operation"]
    patch = field(operation, "patches")[0, 0]
    model = field(field(operation, "device"), "model")
    if mutation == "none":
        pass
    elif mutation == "numeric":
        field(patch, "spectral_values")[0, 0] = 0
    elif mutation == "grid":
        field(patch, "wavelengths_nm")[0, 1] = 411
    elif mutation == "unit":
        assign(patch, "spectral_unit", "1")
    elif mutation == "provenance":
        assign(operation, "provenance", "physical")
    elif mutation == "index":
        field(patch, "index_zero_based")[0, 0] = 1
    elif mutation == "availability":
        assign(patch, "spectrum_present", False)
    elif mutation == "orientation":
        assign(patch, "wavelengths_nm", field(patch, "wavelengths_nm").T)
    elif mutation == "missing":
        data.pop("operation")
    elif mutation == "extra":
        data["extra"] = "unexpected"
    elif mutation == "version":
        data["export_schema"] = np.array([[2]])
    elif mutation == "unicode":
        field(model, "code_units")[0, -1] += 1
    elif mutation == "unicode_type":
        assign(model, "code_units", field(model, "code_units").astype(np.uint32))
    elif mutation == "unicode_encoding":
        assign(model, "encoding", "UTF-8")
    else:
        canonical = json.loads(data["record_json"].item())
        canonical["patches"][0]["observation"]["backend_reported"]["xyz"] = []
        data["record_json"] = json.dumps(canonical)
    stream = io.BytesIO()
    scipy_io.savemat(stream, data)
    if mutation == "none":
        assert from_mat(stream.getvalue()) == result
        return
    with pytest.raises(ValueError):
        from_mat(stream.getvalue())


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not MAT",
        b"MATLAB 5.0 MAT-file",
        b"MATLAB 5.0 MAT-file".ljust(20, b" "),
        b"MATLAB 5.0 MAT-file".ljust(100, b" "),
        "text",
        None,
    ],
)
def test_invalid_mat_payload_is_validation_error(payload, mat):
    with pytest.raises(ValueError):
        from_mat(payload)


def test_mat_refuses_lossy_double_conversion_but_json_preserves_it(result, mat):
    original = result.patches[0]
    observation = original.observation
    too_precise = replace(observation.backend_reported.xyz, values=(2**53 + 1, 0, 105))
    patch = replace(
        original,
        observation=replace(
            observation, backend_reported=replace(observation.backend_reported, xyz=too_precise)
        ),
    )
    record = replace(result, patches=(patch,) + result.patches[1:])
    assert str(2**53 + 1) in result_to_json(record)
    with pytest.raises(ValueError, match="exactly representable"):
        to_mat(record)


def test_mat_refuses_nul_in_view_instead_of_silent_text_loss(result, mat):
    original = result.patches[0]
    observation = replace(original.observation, warnings=("trailing NUL\x00",))
    record = replace(
        result, patches=(replace(original, observation=observation), *result.patches[1:])
    )
    assert "\\u0000" in result_to_json(record)
    with pytest.raises(ValueError, match="NUL"):
        to_mat(record)


def test_canonical_publish_returns_exact_bytes_without_companions(result, tmp_path, monkeypatch):
    import color_measurement.ti3_exports as exporter

    def forbidden(*args, **kwargs):
        pytest.fail("Canonical publication attempted optional companions")

    monkeypatch.setattr(exporter, "save_companions", forbidden)
    target = tmp_path / "canonical.json"
    publication = publish(result, target)
    assert publication.path == target
    assert publication.payload == target.read_bytes() == result_to_json(result).encode("utf-8")
    assert publication.file_sync in {"fsync", "fullsync"}
    assert publication.directory_synced is (os.name != "nt")
    if os.name == "nt":
        assert "Directory synchronization is unsupported or unconfirmed" in publication.limitations
    with pytest.raises(OperationSaveError):
        publish(result, target)


def test_directory_sync_failure_retains_exact_already_published_operation(
    result, tmp_path, monkeypatch
):
    import color_measurement.exports as exporter

    target = tmp_path / "published-before-sync-fault.json"

    def fail_sync(folder):
        assert target.read_bytes() == result_to_json(result).encode()
        assert not list(tmp_path.glob(".measurement-*"))
        raise OSError("directory synchronization denied")

    monkeypatch.setattr(exporter, "_sync_directory", fail_sync)
    with pytest.raises(OperationSaveError) as caught:
        publish(result, target)
    assert caught.value.result == result and caught.value.payload == target.read_bytes()
    assert any("bytes were published" in note for note in caught.value.__notes__)


def test_publication_directory_sync_follows_complete_canonical_visibility(
    result, tmp_path, monkeypatch
):
    import color_measurement.exports as exporter

    target = tmp_path / "complete.json"
    folders = []
    sync = exporter._sync_directory

    def observe(folder):
        assert target.exists() and load(target) == result
        assert not list(tmp_path.glob(".measurement-*"))
        folders.append(folder)
        return sync(folder)

    monkeypatch.setattr(exporter, "_sync_directory", observe)
    publish(result, target)
    assert folders == [tmp_path]


@pytest.mark.parametrize(
    "code", ["ENOTSUP", "EOPNOTSUPP", "EINVAL", "ENOSYS", "ENOTTY", "EIO", "EPERM"]
)
def test_macos_full_sync_unsupported_falls_back_but_io_errors_fail(
    result, tmp_path, monkeypatch, caplog, code
):
    import errno
    import sys
    import types

    try:
        import fcntl
    except ImportError:
        fcntl = types.SimpleNamespace(fcntl=lambda *_: None)
        monkeypatch.setitem(sys.modules, "fcntl", fcntl)

    import color_measurement.exports as exporter

    target = tmp_path / f"full-sync-{code}.json"
    full_sync = getattr(fcntl, "F_FULLFSYNC", 51)
    monkeypatch.setattr(fcntl, "F_FULLFSYNC", full_sync, raising=False)
    original_fcntl = fcntl.fcntl
    fsync = exporter.os.fsync
    calls = []

    def observed(descriptor):
        calls.append("fsync")
        return fsync(descriptor)

    def unsupported(descriptor, command, *args):
        if command == full_sync:
            calls.append("fullsync")
            raise OSError(getattr(errno, code), code)
        return original_fcntl(descriptor, command, *args)

    monkeypatch.setattr(exporter.sys, "platform", "darwin")
    monkeypatch.setattr(exporter.os, "fsync", observed)
    monkeypatch.setattr(fcntl, "fcntl", unsupported)
    if code in {"EIO", "EPERM"}:
        with pytest.raises(OperationSaveError):
            publish(result, target)
        assert not target.exists()
        assert "unsupported" not in caplog.text
    else:
        publish(result, target)
        assert target.read_bytes() == result_to_json(result).encode()
        assert "ordinary fsync completed" in caplog.text
        assert "device-cache synchronization is unconfirmed" in caplog.text
    assert calls[:2] == ["fsync", "fullsync"]


@pytest.mark.parametrize(
    "code", ["ENOTSUP", "EOPNOTSUPP", "EINVAL", "ENOSYS", "ENOTTY", "EIO", "EPERM"]
)
@pytest.mark.skipif(os.name == "nt", reason="Windows has no stdlib directory synchronization call")
def test_directory_sync_unsupported_is_reported_but_io_and_permission_fail(
    result, tmp_path, monkeypatch, code
):
    import errno
    import stat

    import color_measurement.exports as exporter

    fsync = exporter.os.fsync

    def reject_directory(descriptor):
        if stat.S_ISDIR(exporter.os.fstat(descriptor).st_mode):
            raise OSError(getattr(errno, code), code)
        return fsync(descriptor)

    monkeypatch.setattr(exporter.os, "fsync", reject_directory)
    target = tmp_path / "directory-sync.json"
    if code in {"EIO", "EPERM"}:
        with pytest.raises(OperationSaveError) as caught:
            publish(result, target)
        assert caught.value.publication.payload == target.read_bytes()
        assert not caught.value.publication.directory_synced
    else:
        publication = publish(result, target)
        assert publication.payload == target.read_bytes()
        assert not publication.directory_synced and publication.limitations


@pytest.mark.parametrize(
    "code", ["ENOTSUP", "EOPNOTSUPP", "EINVAL", "ENOSYS", "ENOTTY", "EIO", "EPERM"]
)
@pytest.mark.skipif(os.name == "nt", reason="Windows has no stdlib directory synchronization open")
def test_directory_open_unsupported_is_reported_but_io_and_permission_fail(
    result, tmp_path, monkeypatch, code
):
    import errno

    import color_measurement.exports as exporter

    original_open = exporter.os.open

    def reject_directory(path, flags, *args, **kwargs):
        if Path(path) == tmp_path:
            raise OSError(getattr(errno, code), code)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(exporter.os, "open", reject_directory)
    target = tmp_path / "directory-open.json"
    if code in {"EIO", "EPERM"}:
        with pytest.raises(OperationSaveError) as caught:
            publish(result, target)
        assert caught.value.publication.payload == target.read_bytes()
        assert not caught.value.publication.directory_synced
    else:
        publication = publish(result, target)
        assert publication.payload == target.read_bytes()
        assert not publication.directory_synced and publication.limitations


def test_new_publication_identical_collision_does_not_claim_prior_inode_sync(tmp_path, monkeypatch):
    import os

    import color_measurement.exports as exporter

    target = tmp_path / "existing.json"
    payload = b"identical bytes from an earlier save\n"
    target.write_bytes(payload)
    before = target.stat()
    synchronized = []
    sync = exporter._sync_file

    def observed_sync(descriptor, path):
        metadata = os.fstat(descriptor)
        synchronized.append((metadata.st_dev, metadata.st_ino))
        return sync(descriptor, path)

    monkeypatch.setattr(exporter, "_sync_file", observed_sync)
    with pytest.raises(OSError) as caught:
        exporter._publish_new_file(target, payload, lambda cause: OSError(str(cause)))
    assert not hasattr(caught.value, "publication")
    assert target.read_bytes() == payload
    after = target.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
    )
    assert synchronized and (before.st_dev, before.st_ino) not in synchronized
    assert not list(tmp_path.glob(".measurement-*"))
    # The explicit owned-file path separately authenticates and syncs this inode.
    receipt = exporter._publish_owned_file(target, payload, None)
    assert receipt.payload == payload and receipt.directory_synced is (os.name != "nt")
    assert synchronized[-1] == (before.st_dev, before.st_ino)


def test_interrupted_publication_receipt_refuses_equal_bytes_on_replaced_inode(
    tmp_path, monkeypatch,
):
    import hashlib
    from contextlib import contextmanager

    import color_measurement.exports as exporter

    target = tmp_path / "owned.json"
    previous, payload = b"earlier owned bytes", b"new published bytes"
    target.write_bytes(previous)
    replace, descriptor = exporter._replace_file, exporter._regular_descriptor
    published, replaced = [], []
    control = KeyboardInterrupt("Original replacement interruption")

    def interrupted_replace(source, destination, source_descriptor):
        outcome = replace(source, destination, source_descriptor)
        if Path(destination) == target:
            published.append(target.stat().st_ino)
            raise control
        return outcome

    @contextmanager
    def raced_descriptor(path, **kwargs):
        if path == target and published and not replaced:
            target.rename(tmp_path / "original-published-inode")
            target.write_bytes(payload)
            replaced.append(target.stat().st_ino)
        with descriptor(path, **kwargs) as opened:
            yield opened

    monkeypatch.setattr(exporter, "_replace_file", interrupted_replace)
    monkeypatch.setattr(exporter, "_regular_descriptor", raced_descriptor)
    with pytest.raises(KeyboardInterrupt) as caught:
        exporter._publish_owned_file(target, payload, hashlib.sha256(previous).hexdigest())
    assert caught.value is control and published and replaced and published != replaced
    assert target.read_bytes() == payload and not hasattr(control, "publication")


def test_identical_owned_publication_resynchronizes_without_rewriting(tmp_path, monkeypatch):
    import color_measurement.exports as exporter

    target = tmp_path / "owned.bin"
    payload = b"Exact bytes\r\nincluding\x1a\x00\xff\n"
    exporter._publish_owned_file(target, payload, None)
    identity = target.stat().st_dev, target.stat().st_ino

    def no_rewrite(*args, **kwargs):
        pytest.fail("Identical owned bytes must be synchronized without rewriting")

    monkeypatch.setattr(exporter, "_publish_file", no_rewrite)
    for _ in range(2):
        receipt = exporter._publish_owned_file(target, payload, None)
        assert receipt.payload == payload == target.read_bytes()
        assert receipt.file_sync in {"fsync", "fullsync"}
        assert (target.stat().st_dev, target.stat().st_ino) == identity


def test_strict_reader_allows_atomic_owner_replacement_without_translating_bytes(tmp_path):
    import os

    import color_measurement.exports as exporter

    target = tmp_path / "owned-雪-🎨.bin"
    old, new = b"old\r\n\x1a\x00\xff", b"new\r\n\x1a\x00\xfe"
    target.write_bytes(old)
    with exporter._regular_descriptor(target) as descriptor:
        before = os.fstat(descriptor)
        receipt = exporter._publish_owned_file(target, new, hashlib.sha256(old).hexdigest())
        assert receipt.payload == new
        assert os.read(descriptor, len(old) + 1) == old
        assert (before.st_dev, before.st_ino) != (target.stat().st_dev, target.stat().st_ino)
    assert exporter._read_regular(target) == new


def test_owned_replacement_respects_target_readonly_semantics(tmp_path):
    import stat

    import color_measurement.exports as exporter

    target = tmp_path / "readonly-owned.bin"
    old, new = b"Original retained bytes\r\n", b"Authorized new bytes\r\n"
    target.write_bytes(old)
    target.chmod(stat.S_IREAD)
    try:
        if os.name == "nt":
            with pytest.raises(OSError) as caught:
                exporter._publish_owned_file(target, new, hashlib.sha256(old).hexdigest())
            assert caught.value.__cause__.winerror == 5
            assert not hasattr(caught.value, "publication")
            assert target.read_bytes() == old
        else:
            # POSIX rename permission belongs to the parent, not the old inode.
            exporter._publish_owned_file(target, new, hashlib.sha256(old).hexdigest())
            assert target.read_bytes() == new
        assert not list(tmp_path.glob(".measurement-*"))
    finally:
        target.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize("replacement", ["inode", "hardlink", "symlink", "directory", "missing"])
@pytest.mark.parametrize("publication", ["new", "replacement"])
def test_replacement_refuses_a_changed_or_unsafe_synchronized_source(
    tmp_path, monkeypatch, replacement, publication,
):
    from contextlib import contextmanager

    import color_measurement.exports as exporter

    target = tmp_path / "owned.bin"
    old = b"Last good owned bytes\r\n"
    if publication == "replacement":
        target.write_bytes(old)
    foreign = tmp_path / "foreign.bin"
    foreign.write_bytes(b"Foreign bytes must not be published")
    reader = exporter._regular_descriptor
    changed = []

    @contextmanager
    def changed_source(source, **kwargs):
        if source.name.startswith(".measurement-"):
            changed.append(source)
            source.rename(tmp_path / "synchronized-original.bin")
            if replacement == "inode":
                source.write_bytes(foreign.read_bytes())
            elif replacement == "hardlink":
                os.link(foreign, source)
            elif replacement == "symlink":
                source.symlink_to(foreign.name)
            elif replacement == "directory":
                source.mkdir()
        with reader(source, **kwargs) as descriptor:
            yield descriptor

    monkeypatch.setattr(exporter, "_regular_descriptor", changed_source)
    with pytest.raises(OSError) as caught:
        exporter._publish_owned_file(target, b"Synchronized replacement\r\n",
                                     hashlib.sha256(old).hexdigest()
                                     if publication == "replacement" else None)
    assert len(changed) == 1
    assert (target.read_bytes() == old if publication == "replacement" else not target.exists())
    assert foreign.read_bytes() == b"Foreign bytes must not be published"
    changed_path, = changed
    if replacement in {"inode", "hardlink"}:
        assert changed_path.read_bytes() == foreign.read_bytes()
    elif replacement == "symlink":
        assert changed_path.is_symlink() and changed_path.readlink() == Path(foreign.name)
    elif replacement == "directory":
        assert changed_path.is_dir()
    else:
        assert not changed_path.exists()
    if replacement != "missing":
        assert "cleanup refused" in "; ".join(caught.value.__notes__)


@pytest.mark.parametrize("replacement", ["fifo", "hardlink", "changed", "missing", "inode"])
def test_identical_publication_reauthentication_refuses_raced_replacements(
    tmp_path, monkeypatch, replacement
):
    import os

    import color_measurement.exports as exporter

    if replacement == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO replacement requires POSIX")
    target = tmp_path / "owned.json"
    payload = b"authenticated owned bytes\n"
    target.write_bytes(payload)
    original_open = exporter._open_regular_file
    os_open = exporter.os.open
    target_opens = 0

    def refuse_blocking_fifo(path, flags, *args, **kwargs):
        if Path(path) == target and target.is_fifo():
            assert flags & os.O_NONBLOCK, "Owned-file authentication must not block on a raced FIFO"
        return os_open(path, flags, *args, **kwargs)

    def replace_at_second_open(path, *args, **kwargs):
        nonlocal target_opens
        if Path(path) != target:
            return original_open(path, *args, **kwargs)
        target_opens += 1
        if target_opens != 2:
            return original_open(path, *args, **kwargs)
        if replacement == "inode":
            descriptor = original_open(path, *args, **kwargs)
            target.unlink()
            target.write_bytes(payload)
            return descriptor
        target.unlink()
        if replacement == "fifo":
            os.mkfifo(target)
        elif replacement == "hardlink":
            foreign = tmp_path / "foreign.json"
            foreign.write_bytes(payload)
            os.link(foreign, target)
        elif replacement == "changed":
            target.write_bytes(b"external replacement\n")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(exporter, "_open_regular_file", replace_at_second_open)
    if replacement == "fifo":
        monkeypatch.setattr(exporter.os, "open", refuse_blocking_fifo)
    with pytest.raises(OSError) as caught:
        exporter._publish_owned_file(target, payload, None)
    assert target_opens == 2
    assert not hasattr(caught.value, "publication")


@pytest.mark.parametrize("boundary", ["authentication", "source_close"])
@pytest.mark.parametrize("primary", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_owned_replacement_preserves_first_control_through_recovery(
    tmp_path, monkeypatch, boundary, primary
):
    import hashlib
    from contextlib import contextmanager

    import color_measurement.exports as exporter

    target = tmp_path / "owned.json"
    previous, payload = b"previous owned bytes\n", b"replacement owned bytes\n"
    target.write_bytes(previous)
    replace, descriptor = exporter._replace_file, exporter._regular_descriptor
    close_descriptor = exporter.os.close
    first = primary("Original replacement failure")
    later = SystemExit("Later recovery failure")
    expected = first if not isinstance(first, Exception) else later
    replacements, failures, source_descriptors = [], [], []

    def interrupted_replace(source, destination, source_descriptor):
        result = replace(source, destination, source_descriptor)
        if Path(destination) == target and not replacements:
            source_descriptors.append(source_descriptor)
            replacements.append(exporter._read_regular(target))
            raise first
        return result

    @contextmanager
    def interrupted_authentication(path, **kwargs):
        if path == target and replacements and not failures:
            failures.append(path)
            raise later
        with descriptor(path, **kwargs) as opened:
            yield opened

    def interrupted_close(current):
        result = close_descriptor(current)
        if current in source_descriptors and not failures:
            failures.append(current)
            raise later
        return result

    monkeypatch.setattr(exporter, "_replace_file", interrupted_replace)
    if boundary == "authentication":
        monkeypatch.setattr(exporter, "_regular_descriptor", interrupted_authentication)
    else:
        monkeypatch.setattr(exporter.os, "close", interrupted_close)
    with pytest.raises(type(expected)) as caught:
        exporter._publish_owned_file(target, payload, hashlib.sha256(previous).hexdigest())
    assert caught.value is expected
    assert replacements == [payload] and len(failures) == 1
    assert target.read_bytes() == payload
    reason = "; ".join([str(expected), *expected.__notes__])
    assert str(first) in reason and str(later) in reason
    if boundary == "authentication":
        assert not hasattr(expected, "publication")
    else:
        assert expected.publication.payload == payload
        assert expected.publication.directory_synced is False
    retried = exporter._publish_owned_file(target, payload, hashlib.sha256(previous).hexdigest())
    assert retried.payload == target.read_bytes() == payload
    assert replacements == [payload] and not list(tmp_path.glob(".measurement-*"))


@pytest.mark.parametrize("attack", ["inode", "identical_inode", "bytes"])
@pytest.mark.parametrize("publication", ["new", "replacement"])
def test_publication_authenticates_the_effect_after_source_name_races(
    tmp_path, monkeypatch, attack, publication,
):
    import color_measurement.exports as exporter

    target = tmp_path / "owned.bin"
    previous, payload = b"Previous owned bytes\n", b"Synchronized replacement\n"
    foreign = b"Foreign bytes must not receive a save receipt\n"
    if publication == "replacement":
        target.write_bytes(previous)
    link, replace = exporter.os.link, exporter._replace_file
    attacked = []

    def change_source(source):
        source = Path(source)
        attacked.append(source)
        if attack == "bytes":
            # Use the same valid Windows sharing policy as the actual owner.
            descriptor = (exporter._open_regular_file(source, sync=True) if os.name == "nt"
                          else os.open(source, os.O_RDWR))
            with os.fdopen(descriptor, "r+b") as stream:
                stream.write(foreign)
                stream.truncate()
        else:
            source.rename(tmp_path / "synchronized-original.bin")
            source.write_bytes(payload if attack == "identical_inode" else foreign)

    def raced_link(source, destination, **kwargs):
        change_source(source)
        return link(source, destination, **kwargs)

    def raced_replace(source, destination, descriptor):
        change_source(source)
        return replace(source, destination, descriptor)

    monkeypatch.setattr(exporter.os, "link", raced_link)
    monkeypatch.setattr(exporter, "_replace_file", raced_replace)
    # Windows replacement consumes the original handle, not the changed name.
    handle_bound = os.name == "nt" and publication == "replacement" and attack != "bytes"
    if handle_bound:
        receipt = exporter._publish_owned_file(
            target, payload, hashlib.sha256(previous).hexdigest(),
        )
        assert receipt.payload == target.read_bytes() == payload
    else:
        with pytest.raises(OSError) as caught:
            exporter._publish_owned_file(target, payload, hashlib.sha256(previous).hexdigest()
                                         if publication == "replacement" else None)
        assert not hasattr(caught.value, "publication")
        assert target.read_bytes() == (payload if attack == "identical_inode" else foreign)
    changed, = attacked
    if attack != "bytes":
        # A substituted entry is foreign even when its bytes happen to match.
        if publication == "new" or handle_bound:
            assert changed.read_bytes() == (payload if attack == "identical_inode" else foreign)
        else:
            assert not changed.exists()
    else:
        assert not changed.exists()


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt, SystemExit, RuntimeError])
def test_interrupted_new_link_authenticates_its_owned_alias_before_cleanup(
    tmp_path, monkeypatch, failure,
):
    import color_measurement.exports as exporter

    target = tmp_path / "new.bin"
    payload = b"Synchronized new publication\n"
    first = failure("Interrupted after the link took effect")
    link, authenticate = exporter.os.link, exporter._authenticate_publication
    observed = []

    def interrupted_link(source, destination, **kwargs):
        link(source, destination, **kwargs)
        raise first

    def observe_alias(destination, expected, identity, descriptor, linked_source):
        assert linked_source is not None and linked_source.exists()
        assert destination.stat().st_nlink == linked_source.stat().st_nlink == 2
        result = authenticate(destination, expected, identity, descriptor, linked_source)
        observed.append(identity)
        return result

    monkeypatch.setattr(exporter.os, "link", interrupted_link)
    monkeypatch.setattr(exporter, "_authenticate_publication", observe_alias)
    with pytest.raises(failure) as caught:
        exporter._publish_owned_file(target, payload, None)
    assert (caught.value.__cause__ is first if failure is OSError else caught.value is first)
    assert caught.value.publication.payload == target.read_bytes() == payload
    assert not caught.value.publication.directory_synced
    assert len(observed) == 1 and target.stat().st_nlink == 1
    assert not list(tmp_path.glob(".measurement-*"))


@pytest.mark.parametrize("case", [
    "existing_name", "existing_directory", "existing_hardlink", "full_disk", "owned_alias_changed",
])
def test_failed_publication_reports_authentication_only_for_its_own_inode(
    tmp_path, monkeypatch, case,
):
    import errno

    import color_measurement.exports as exporter

    target = tmp_path / "owned.bin"
    existing, payload = b"Existing bytes stay in place\n", b"Refused publication\n"
    if case.startswith("existing_"):
        # The documented refusal: a new-file save never replaces an existing name.
        if case == "existing_directory":
            target.mkdir()
        else:
            target.write_bytes(existing)
            if case == "existing_hardlink":
                exporter.os.link(target, tmp_path / "foreign-alias.bin")
        publish, last = exporter._publish_new_file, OSError
    elif case == "full_disk":
        target.write_bytes(existing)

        def full(descriptor, path):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(exporter, "_sync_file", full)
        publish, last = exporter._publish_owned_file, hashlib.sha256(existing).hexdigest()
    else:
        link = exporter.os.link

        def interrupted_link(source, destination, **kwargs):
            link(source, destination, **kwargs)
            with open(destination, "r+b") as stream:
                stream.write(b"X")
            raise OSError("Interrupted after the link took effect")

        monkeypatch.setattr(exporter.os, "link", interrupted_link)
        publish, last = exporter._publish_owned_file, None
    with pytest.raises(OSError) as caught:
        publish(target, payload, last)
    notes = "; ".join(getattr(caught.value, "__notes__", ()))
    assert not hasattr(caught.value, "publication")
    if case == "owned_alias_changed":
        # This publisher's inode with other bytes is a real authentication failure.
        assert "Publication authentication failed" in notes
    else:
        # Another inode at the name is no publication, not a failed authentication.
        assert notes == ""
        assert target.is_dir() if case == "existing_directory" else target.read_bytes() == existing
    assert not list(tmp_path.glob(".measurement-*"))


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize("publication", ["new", "replacement"])
def test_replacement_source_close_failure_retains_known_publication_receipt(
    tmp_path, monkeypatch, failure, publication,
):
    from contextlib import contextmanager

    import color_measurement.exports as exporter

    target = tmp_path / "owned.json"
    old, payload = b"Earlier retained bytes\n", b"Synchronized replacement\n"
    if publication == "replacement":
        target.write_bytes(old)
    opened, reader = exporter._open_regular_file, exporter._regular_descriptor
    close = exporter.os.close
    original = failure("Original source descriptor close failure")
    sources, closed = [], []

    def observed_open(path, **kwargs):
        descriptor = opened(path, **kwargs)
        if path.name.startswith(".measurement-"):
            sources.append(descriptor)
        return descriptor

    def failed_close(descriptor):
        result = close(descriptor)
        if descriptor in sources and not closed:
            closed.append(descriptor)
            raise original
        return result

    @contextmanager
    def no_reauthentication_after_known_publication(path, **kwargs):
        if path == target and closed:
            pytest.fail("Known publication was lost before source descriptor cleanup")
        with reader(path, **kwargs) as descriptor:
            yield descriptor

    monkeypatch.setattr(exporter, "_open_regular_file", observed_open)
    monkeypatch.setattr(exporter.os, "close", failed_close)
    monkeypatch.setattr(exporter, "_regular_descriptor",
                        no_reauthentication_after_known_publication)
    with pytest.raises(failure) as caught:
        exporter._publish_owned_file(target, payload, hashlib.sha256(old).hexdigest()
                                     if publication == "replacement" else None)
    assert (caught.value.__cause__ is original if failure is OSError else caught.value is original)
    assert sources == closed and len(closed) == 1
    receipt = caught.value.publication
    assert receipt.path == target and receipt.payload == target.read_bytes() == payload
    assert receipt.file_sync in {"fsync", "fullsync"} and not receipt.directory_synced
    assert not list(tmp_path.glob(".measurement-*"))


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_first_cleanup_control_keeps_successful_publication_receipt(
    tmp_path, monkeypatch, signal
):
    import color_measurement.exports as exporter

    target = tmp_path / "owned.json"
    payload = b"published canonical bytes\n"
    unlink, sync_file, sync_directory = Path.unlink, exporter._sync_file, exporter._sync_directory
    interruption = signal("First control during temporary cleanup")
    cleaned, file_syncs, directory_syncs = [], [], []

    def interrupted_cleanup(path, *args, **kwargs):
        result = unlink(path, *args, **kwargs)
        if path.parent == tmp_path and path.name.startswith(".measurement-") and not cleaned:
            cleaned.append(path)
            raise interruption
        return result

    def recorded_file_sync(*args):
        outcome = sync_file(*args)
        file_syncs.append(outcome)
        return outcome

    def recorded_directory_sync(folder):
        directory_syncs.append(folder)
        return sync_directory(folder)

    monkeypatch.setattr(Path, "unlink", interrupted_cleanup)
    monkeypatch.setattr(exporter, "_sync_file", recorded_file_sync)
    monkeypatch.setattr(exporter, "_sync_directory", recorded_directory_sync)
    with pytest.raises(signal) as caught:
        exporter._publish_new_file(target, payload, lambda error: OSError(str(error)))
    assert caught.value is interruption
    assert len(cleaned) == len(file_syncs) == 1 and directory_syncs == []
    assert target.read_bytes() == payload
    publication = caught.value.publication
    assert publication.path == target and publication.payload == payload
    assert publication.file_sync == file_syncs[0] and publication.directory_synced is False
    inode = target.stat().st_ino
    retried = exporter._publish_owned_file(target, payload, None)
    assert retried.payload == target.read_bytes() == payload and target.stat().st_ino == inode
    assert directory_syncs == [tmp_path] and not list(tmp_path.glob(".measurement-*"))


def test_temporary_cleanup_note_keeps_one_copy_of_each_failure(tmp_path, monkeypatch):
    import errno

    import color_measurement.exports as exporter

    target = tmp_path / "owned.json"
    previous = b"earlier owned bytes"
    target.write_bytes(previous)
    first = OSError(errno.ENOSPC, "Replacement refused")
    later = OSError(errno.EIO, "Temporary unlink refused")
    unlink = Path.unlink

    def refuse_replace(*args):
        raise first

    def refuse_cleanup(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        raise later

    monkeypatch.setattr(exporter, "_replace_file", refuse_replace)
    monkeypatch.setattr(Path, "unlink", refuse_cleanup)
    with pytest.raises(OSError) as caught:
        exporter._publish_owned_file(
            target, b"retained bytes", exporter.hashlib.sha256(previous).hexdigest())
    assert "Replacement refused" in str(caught.value)
    notes = "; ".join(caught.value.__notes__)
    assert notes.count("Temporary unlink refused") == 1
    assert "Could not remove temporary file" in notes and target.read_bytes() == previous
    assert not list(tmp_path.glob(".measurement-*"))


@pytest.mark.parametrize("boundary", ["directory_sync", "stream_read"])
@pytest.mark.parametrize("primary", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize("closing", [OSError, SystemExit])
def test_real_descriptor_close_preserves_first_control_and_receipt(
    tmp_path, monkeypatch, boundary, primary, closing
):
    from color_measurement import exports

    target, payload = tmp_path / "owned.json", b"Exact owned bytes\n"
    target.write_bytes(payload)
    first, later = primary("First descriptor failure"), closing("Later descriptor close failure")
    expected = first if not isinstance(first, Exception) or isinstance(later, Exception) else later
    receipt = exports.PublicationResult(target, payload, "fsync", False)
    first.publication = receipt
    opened, closes = [], []
    if boundary == "directory_sync" and exports.os.name == "nt":
        pytest.skip("Windows has no stdlib directory synchronization descriptor")
    open_descriptor = (exports.os.open if boundary == "directory_sync"
                       else exports._open_regular_file)
    close_descriptor = exports.os.close
    sync_descriptor, open_stream = exports.os.fsync, exports.os.fdopen

    def capture(path, *args, **kwargs):
        descriptor = open_descriptor(path, *args, **kwargs)
        if Path(path) == (tmp_path if boundary == "directory_sync" else target):
            opened.append(descriptor)
        return descriptor

    def interrupted_sync(descriptor):
        if opened and descriptor == opened[-1]:
            raise first
        return sync_descriptor(descriptor)

    class InterruptedStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *failure):
            return self.stream.__exit__(*failure)

        def read(self):
            raise first

    def interrupted_stream(descriptor, *args, **kwargs):
        stream = open_stream(descriptor, *args, **kwargs)
        return InterruptedStream(stream) if descriptor in opened else stream

    def refused_close(descriptor):
        if descriptor in opened:
            closes.append(descriptor)
            raise later
        return close_descriptor(descriptor)

    if boundary == "directory_sync":
        monkeypatch.setattr(exports.os, "open", capture)
    else:
        monkeypatch.setattr(exports, "_open_regular_file", capture)
    monkeypatch.setattr(exports.os, "close", refused_close)
    if boundary == "directory_sync":
        monkeypatch.setattr(exports.os, "fsync", interrupted_sync)
    else:
        monkeypatch.setattr(exports.os, "fdopen", interrupted_stream)
    try:
        with pytest.raises(type(expected)) as caught:
            if boundary == "directory_sync":
                exports._sync_directory(tmp_path)
            else:
                exports._read_regular(target)
        assert caught.value is expected and closes == opened and len(opened) == 1
        assert expected.publication is receipt and target.read_bytes() == payload
        notes = "; ".join(expected.__notes__)
        if expected is first:
            assert str(later) in notes
        else:
            assert str(first) in notes
        assert not exports._RETIRED_PUBLICATION_LOCKS
    finally:
        # These injected failures never closed the captured descriptor.
        monkeypatch.setattr(exports.os, "close", close_descriptor)
        for descriptor in opened:
            close_descriptor(descriptor)


@pytest.mark.parametrize("alias", ["ordinary", "parent", "symlink", "relative", "renamed"])
def test_retired_lock_path_refuses_publication_into_its_folder_only(tmp_path, monkeypatch, alias):
    from color_measurement import exports

    retired, other = tmp_path / "retired", tmp_path / "other"
    retired.mkdir()
    other.mkdir()
    lock = exports._folder_lock(retired, ".batch.lock").__enter__()
    descriptor, close = lock.descriptor, exports.os.close
    close_attempted = False

    def failed_close(value):
        nonlocal close_attempted
        if value == descriptor and not close_attempted:
            close_attempted = True
            close(value)
            raise OSError("Lock close refused")
        return close(value)

    monkeypatch.setattr(exports.os, "close", failed_close)
    try:
        with pytest.raises(OSError, match="Lock close refused"):
            lock.__exit__(None, None, None)
        assert exports._RETIRED_PUBLICATION_LOCKS == {lock.path.absolute(): lock}
        destination = retired
        if alias == "parent":
            (retired / "child").mkdir()
            destination = retired / "child" / ".."
        elif alias == "symlink":
            destination = tmp_path / "alias"
            try:
                destination.symlink_to(retired, target_is_directory=True)
            except OSError:
                pytest.skip("Directory symbolic links are unavailable")
        elif alias == "relative":
            monkeypatch.chdir(tmp_path)
            destination = Path("retired")
        elif alias == "renamed":
            destination = tmp_path / "moved"
            retired.rename(destination)
        refused = (
            lambda: exports._publish_file(
                destination / "a.json", b"a", lambda cause: OSError(str(cause))
            ),
            lambda: exports._publish_owned_file(destination / "a.json", b"a", None),
            lambda: exports._folder_lock(destination, ".ti3-export.lock").__enter__(),
        )
        for call in refused:
            with pytest.raises(OSError, match="is retired in this process"):
                call()
        assert not (destination / "a.json").exists()
        assert exports._publish_owned_file(other / "a.json", b"a", None).payload == b"a"
        with exports._folder_lock(other, ".batch.lock"):
            pass
    finally:
        # The descriptor closed before its result became uncertain. Never retry
        # that number: subsequent ordinary publication can already have reused it.
        monkeypatch.setattr(exports.os, "close", close)
        lock.descriptor = None
        exports._RETIRED_PUBLICATION_LOCKS.clear()


def test_changed_lock_parent_is_refused_and_retirement_tracks_the_acquired_directory(
    tmp_path, monkeypatch,
):
    from color_measurement import exports

    folder, original, moved = tmp_path / "folder", tmp_path / "original", tmp_path / "moved"
    folder.mkdir()
    lock = exports._folder_lock(folder, ".batch.lock")
    opened = []
    closed = set()
    open_descriptor, close_descriptor = exports.os.open, exports.os.close

    def replace_before_open(path, *args, **kwargs):
        if Path(path) == lock.path:
            folder.rename(original)
            folder.mkdir()
            descriptor = open_descriptor(path, *args, **kwargs)
            opened.append(descriptor)
            return descriptor
        return open_descriptor(path, *args, **kwargs)

    def refuse_close(descriptor):
        if descriptor in opened and descriptor not in closed:
            closed.add(descriptor)
            close_descriptor(descriptor)
            raise OSError("Lock close is unconfirmed")
        return close_descriptor(descriptor)

    monkeypatch.setattr(exports.os, "open", replace_before_open)
    monkeypatch.setattr(exports.os, "close", refuse_close)
    try:
        with pytest.raises(OSError, match="Publication lock directory was replaced"):
            lock.__enter__()
        acquired = folder.stat()
        assert lock.directory_identity == (acquired.st_dev, acquired.st_ino)
        assert lock.release_failure is not None and len(opened) == 1
        folder.rename(moved)
        with pytest.raises(OSError, match="is retired in this process"):
            exports._publish_owned_file(moved / "a.json", b"a", None)
        with pytest.raises(OSError, match="is retired in this process"):
            exports._folder_lock(moved, ".ti3-export.lock").__enter__()
        assert not (moved / "a.json").exists()
        assert exports._publish_owned_file(original / "a.json", b"a", None).payload == b"a"
    finally:
        monkeypatch.setattr(exports.os, "close", close_descriptor)
        for descriptor in opened:
            if descriptor not in closed:
                close_descriptor(descriptor)
        lock.descriptor = None
        exports._RETIRED_PUBLICATION_LOCKS.clear()


def _retire(lock, monkeypatch):
    """Enter ``lock`` and make its close unconfirmed, retiring its destination."""
    from color_measurement import exports

    lock.__enter__()
    close_descriptor = exports.os.close
    descriptor = lock.descriptor

    def refuse_close(number):
        close_descriptor(number)
        if number == descriptor:
            raise OSError("Lock close is unconfirmed")

    monkeypatch.setattr(exports.os, "close", refuse_close)
    with pytest.raises(OSError, match="unconfirmed"):
        lock.__exit__(None, None, None)
    monkeypatch.setattr(exports.os, "close", close_descriptor)
    assert exports._RETIRED_PUBLICATION_LOCKS[lock.path] is lock


def test_an_unidentifiable_destination_is_neither_retired_nor_an_error(tmp_path, monkeypatch):
    # Catches: the identity lookup raising NotADirectoryError (or any OSError
    # other than a missing path) out of a snapshot once any lock is retired.
    from color_measurement import exports

    monkeypatch.setattr(exports, "_RETIRED_PUBLICATION_LOCKS", {})
    retired = tmp_path / "retired"
    retired.mkdir()
    _retire(exports._folder_lock(retired, ".batch.lock"), monkeypatch)
    plain = tmp_path / "plain.txt"
    plain.write_text("not a directory\n")
    assert exports._publication_cleanup_reason(plain / "inside") is None
    assert exports._publication_cleanup_reason(tmp_path / "absent") is None
    assert "is retired in this process" in exports._publication_cleanup_reason(retired)
    renamed = tmp_path / "renamed"
    retired.rename(renamed)
    assert "is retired in this process" in exports._publication_cleanup_reason(renamed)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no stdlib directory handle")
def test_a_directory_descriptor_close_failure_is_reported_and_never_retried(
    tmp_path, monkeypatch, caplog,
):
    # Catches: the directory close failure raised from __exit__, which left the
    # lock unretired yet made every later release raise "no descriptor retry",
    # so the owner could neither finish nor exit.
    import logging

    from color_measurement import exports

    monkeypatch.setattr(exports, "_RETIRED_PUBLICATION_LOCKS", {})
    folder = tmp_path / "folder"
    folder.mkdir()
    lock = exports._folder_lock(folder, ".batch.lock")
    lock.__enter__()
    directory = lock.directory.descriptor
    close_descriptor = exports.os.close

    def refuse_directory_close(number):
        if number == directory:
            raise OSError("Directory close is unconfirmed")
        return close_descriptor(number)

    monkeypatch.setattr(exports.os, "close", refuse_directory_close)
    with caplog.at_level(logging.WARNING, logger="color_measurement.exports"):
        assert lock.__exit__(None, None, None) is False
        assert lock.__exit__(None, None, None) is False
    assert lock.descriptor is None and lock.directory.descriptor == directory
    assert lock.directory.release_failure is not None
    assert not exports._RETIRED_PUBLICATION_LOCKS
    assert "was not released" in caplog.text
    monkeypatch.setattr(exports.os, "close", close_descriptor)
    fresh = exports._folder_lock(folder, ".batch.lock").__enter__()
    assert fresh.__exit__(None, None, None) is False


@pytest.mark.skipif(os.name == "nt", reason="Windows has no stdlib directory handle")
def test_a_retired_lock_holds_its_directory_so_the_identity_cannot_be_reused(
    tmp_path, monkeypatch,
):
    # Catches: a retirement keyed by (st_dev, st_ino) alone, which a directory
    # created after the retired one is deleted could inherit on filesystems
    # that reuse inode numbers. The held descriptor keeps the inode allocated.
    from color_measurement import exports

    monkeypatch.setattr(exports, "_RETIRED_PUBLICATION_LOCKS", {})
    folder = tmp_path / "folder"
    folder.mkdir()
    confirmed = exports._folder_lock(folder, ".batch.lock")
    confirmed.__enter__()
    assert confirmed.directory.descriptor is not None
    held = os.fstat(confirmed.directory.descriptor)
    assert (held.st_dev, held.st_ino) == confirmed.directory_identity == (
        folder.stat().st_dev, folder.stat().st_ino,
    )
    confirmed.__exit__(None, None, None)
    assert confirmed.directory.descriptor is None, "a confirmed release closes the directory"
    lock = exports._folder_lock(folder, ".batch.lock")
    _retire(lock, monkeypatch)
    assert lock.directory.descriptor is not None, "a retired lock keeps its directory open"
    (folder / ".batch.lock").unlink()
    folder.rmdir()
    # The directory is gone from the namespace but its inode is still ours:
    # the held descriptor still answers with the retired identity. This is the
    # check: a held descriptor keeps the inode allocated, so no later directory
    # can receive it. The "elsewhere" assertions below state the consequence;
    # they cannot fail on APFS, which does not reuse inode numbers promptly,
    # and are not a demonstration of reuse.
    unlinked = os.fstat(lock.directory.descriptor)
    assert (unlinked.st_dev, unlinked.st_ino) == lock.directory_identity
    # The same path stays retired by name; a directory created elsewhere can
    # never receive the pinned inode, so it is not retired by identity.
    assert "is retired in this process" in exports._publication_cleanup_reason(folder)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    created = elsewhere.stat()
    assert (created.st_dev, created.st_ino) != lock.directory_identity
    assert exports._publication_cleanup_reason(elsewhere) is None
