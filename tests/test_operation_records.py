"""Operation envelopes retain real subsets without changing schema-1 readings."""

import json
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from color_measurement.exports import from_json, to_json
from color_measurement.models import (
    XYZ,
    CalibrationContext,
    ComputedValues,
    DeviceIdentity,
    Observation,
    ReportedValues,
    Spectrum,
)
from color_measurement.operation_records import (
    OperationResult,
    PatchResult,
    result_from_json,
    result_to_json,
)


def request_json(**changes) -> str:
    request = {
        "schema_version": 1,
        "device_id": "fixture",
        "configuration": "reflective",
        "mode": "reflective_scan",
        "workflow": "strip",
        "patch_ids": ["patch-0", "patch-1", "patch-2"],
        "options": [],
        "layout": {"rows": 1, "columns": 3},
    }
    request.update(changes)
    return json.dumps(request, indent=2) + "\n"


def reading(index: int, *, spectrum_only: bool = False) -> Observation:
    native = (
        ReportedValues(spectrum=Spectrum((410, 425.5, 700), (-0.3, 0, 101), "reflectance", "%"))
        if spectrum_only
        else ReportedValues(xyz=XYZ((20, 25, 10), "1", "Y=100", "CIE1931_2", "D50"))
    )
    return Observation(
        schema_version=1,
        observation_id=f"reading-{index}",
        operation_id="operation-1",
        sample_id=f"patch-{index}",
        acquired_at="2026-09-18T12:00:00+00:00",
        provenance="simulated",
        device=DeviceIdentity("fixture", "synthetic model", "fixture", "1", None),
        requested_mode="reflective_scan",
        actual_mode="reflective_scan",
        configuration=(("fixture", "synthetic"),),
        calibration=CalibrationContext("not_applicable", "Synthetic fixture"),
        backend_reported=native,
        project_computed=ComputedValues(),
        raw_backend_output='Native text: 測定 café\r\n{"quantity": null}\n',
        warnings=("SIMULATED: no instrument operated",),
    )


@pytest.fixture
def complete():
    return OperationResult(
        schema_version=1,
        operation_id="operation-1",
        device_id="fixture",
        device=DeviceIdentity("fixture", "synthetic model", "fixture", "1", None),
        provenance="simulated",
        mode="reflective_scan",
        workflow="strip",
        status="completed",
        expected_patch_ids=("patch-0", "patch-1", "patch-2"),
        patches=tuple(
            PatchResult(i, f"patch-{i}", reading(i, spectrum_only=i == 1)) for i in range(3)
        ),
        request_json=request_json(),
        configuration=(("mode_readback", "reflective_scan"),),
        calibration=CalibrationContext("not_applicable", "Synthetic fixture"),
        error=None,
        software_stopped=True,
        instrument_state="not_applicable",
    )


def test_complete_roundtrip_preserves_legacy_observation_bytes_and_optional_quantities(complete):
    original = tuple(to_json(patch.observation) for patch in complete.patches)
    canonical = result_to_json(complete)
    loaded = result_from_json(canonical)
    assert loaded == complete
    assert result_to_json(loaded) == canonical
    for patch, legacy_json in zip(loaded.patches, original):
        assert from_json(legacy_json) == patch.observation
        assert to_json(patch.observation) == legacy_json
    assert loaded.patches[0].observation.backend_reported.spectrum is None
    assert loaded.patches[1].observation.backend_reported.xyz is None
    assert loaded.patches[1].observation.backend_reported.spectrum.values == (-0.3, 0, 101)
    assert loaded.patches[1].observation.backend_reported.spectrum.wavelengths_nm == (
        410, 425.5, 700
    )
    assert all(p.observation.device.serial is None for p in loaded.patches)
    assert loaded.request_json == complete.request_json
    assert loaded.configuration == complete.configuration


@pytest.mark.parametrize("site", ["root", "field", "request"])
def test_deep_operation_json_is_a_typed_decode_refusal(complete, site):
    nested = "[" * 100000 + "0" + "]" * 100000
    if site == "root":
        text = nested
    elif site == "field":
        text = '{"operation_id":' + nested + "}"
    else:
        data = json.loads(result_to_json(complete))
        data["request_json"] = nested
        text = json.dumps(data)
    # Python 3.14 can decode this depth; its schema refusal is equally valid.
    schema_reason = {
        "root": "Expected a record object",
        "field": "OperationResult fields differ",
        "request": "Retained request must be a JSON object",
    }[site]
    with pytest.raises(ValueError, match=f"Operation-result JSON nesting|{schema_reason}"):
        result_from_json(text)


@pytest.mark.parametrize("site", ["root", "request"])
def test_operation_json_decoder_recursion_is_a_typed_refusal(complete, monkeypatch, site):
    text = result_to_json(complete)
    refused = text if site == "root" else complete.request_json
    decode = json.loads

    def recurse(value, *args, **kwargs):
        if value == refused:
            raise RecursionError("Synthetic decoder recursion")
        return decode(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", recurse)
    with pytest.raises(ValueError, match="Operation-result JSON nesting"):
        result_from_json(text)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("indices", [(), (0,), (1,), (0, 2), (0, 1, 2)])
def test_incomplete_operations_keep_only_acquired_patches_and_original_positions(
    complete, status, indices
):
    result = replace(
        complete,
        status=status,
        patches=tuple(complete.patches[i] for i in indices),
        error="Synthetic stop after selected readings",
        instrument_state="unknown",
    )
    loaded = result_from_json(result_to_json(result))
    assert loaded == result
    assert tuple(p.index for p in loaded.patches) == indices
    assert loaded.expected_patch_ids == complete.expected_patch_ids
    assert loaded.instrument_state == "unknown"


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_calibration_operation_has_no_fabricated_observation(complete, status):
    result = replace(
        complete,
        workflow="calibrate",
        expected_patch_ids=(),
        patches=(),
        request_json=request_json(workflow="calibrate", patch_ids=[]),
        status=status,
        error=None if status == "completed" else "Calibration stopped",
    )
    assert result_from_json(result_to_json(result)) == result
    assert json.loads(result_to_json(result))["patches"] == []
    assert json.loads(result_to_json(result))["device"]["serial"] is None
    assert result.device.backend_version == "1"
    assert result.provenance == "simulated"


def test_mode_and_measurement_workflow_names_are_extensible(complete):
    mode = "future_mode"
    patches = tuple(
        replace(p, observation=replace(p.observation, requested_mode=mode, actual_mode=mode))
        for p in complete.patches
    )
    result = replace(
        complete,
        mode=mode,
        workflow="future_workflow",
        patches=patches,
        request_json=request_json(mode=mode, workflow="future_workflow"),
    )
    assert result_from_json(result_to_json(result)) == result


@pytest.mark.parametrize(
    "changes,refusal",
    [
        ({"schema_version": True}, "Unsupported operation-result schema version"),
        ({"schema_version": 2}, "Unsupported operation-result schema version"),
        ({"operation_id": " "}, "Required text fields must be nonempty strings"),
        ({"device_id": ""}, "Required text fields must be nonempty strings"),
        ({"device": {}}, "device identity must match its device ID"),
        ({"device": DeviceIdentity("other", "synthetic model", "fixture", "1")},
         "device identity must match its device ID"),
        ({"provenance": "unknown"}, "Unknown operation provenance"),
        ({"provenance": "physical"}, "device identity and provenance must match the operation"),
        ({"mode": None}, "Required text fields must be nonempty strings"),
        ({"workflow": ""}, "Required text fields must be nonempty strings"),
        ({"status": "partial"}, "Unknown operation-result status"),
        ({"expected_patch_ids": ["patch-0", "patch-1", "patch-2"]},
         "Expected patch IDs must be an immutable tuple"),
        ({"expected_patch_ids": ("patch-0", "patch-0", "patch-2")},
         "Expected patch IDs must be unique"),
        ({"expected_patch_ids": ("patch-0", None, "patch-2")},
         "Required text fields must be nonempty strings"),
        ({"patches": []}, "Patches must be an immutable tuple of PatchResult records"),
        ({"patches": ({},)}, "Patches must be an immutable tuple of PatchResult records"),
        ({"calibration": {}}, "requires a CalibrationContext record"),
        ({"configuration": [["mode_readback", "reflective_scan"]]},
         "Configuration must be immutable text key/value pairs"),
        ({"configuration": (("mode_readback", "reflective_scan"), ("mode_readback", "other"))},
         "Duplicate configuration keys"),
        ({"configuration": (("mode_readback", None),)},
         "Configuration must be immutable text key/value pairs"),
        ({"error": "Unexpected error on completed result"},
         "Completed operation must have no error"),
        ({"status": "failed", "error": None}, "Required text fields must be nonempty strings"),
        ({"status": "failed", "error": " "}, "Required text fields must be nonempty strings"),
        ({"status": "cancelled", "error": []}, "Required text fields must be nonempty strings"),
        ({"software_stopped": 1}, "Software termination state must be boolean"),
        ({"instrument_state": "ready"}, "Unknown instrument state"),
        ({"workflow": "calibrate"}, "Calibration or configuration query must not contain"),
        ({"expected_patch_ids": (), "patches": ()},
         "Measurement workflow must declare expected patch IDs"),
    ],
)
def test_invalid_envelope_metadata_is_rejected_directly(complete, changes, refusal):
    with pytest.raises(ValueError, match=refusal):
        replace(complete, **changes)


@pytest.mark.parametrize("index", [True, 1.5, -1, "0"])
def test_patch_index_requires_nonnegative_integer(index):
    with pytest.raises(ValueError, match="nonnegative integer"):
        PatchResult(index, "patch-0", reading(0))


@pytest.mark.parametrize("patch_id,refusal", [
    (None, "Required text fields must be nonempty strings"),
    ("", "Required text fields must be nonempty strings"),
    (" ", "Required text fields must be nonempty strings"),
    ("wrong-sample", "Patch ID must match the observation sample ID"),
])
def test_patch_identity_requires_real_matching_sample(patch_id, refusal):
    with pytest.raises(ValueError, match=refusal):
        PatchResult(0, patch_id, reading(0))


def test_patch_requires_observation_record():
    with pytest.raises(ValueError, match="record"):
        PatchResult(0, "patch-0", {})


@pytest.mark.parametrize("indices,refusal", [
    ((0,), "Completed operation requires every expected patch"),
    ((0, 0, 2), "Patch indices must be increasing"),
    ((2, 1, 0), "Patch indices must be increasing"),
    ((0, 2), "Completed operation requires every expected patch"),
])
def test_completed_requires_every_patch_once_in_requested_order(complete, indices, refusal):
    with pytest.raises(ValueError, match=refusal):
        replace(complete, patches=tuple(complete.patches[i] for i in indices))


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_partial_operations_refuse_reordered_duplicate_or_out_of_range_patches(complete, status):
    for patches in (
        (complete.patches[1], complete.patches[0]),
        (complete.patches[0], complete.patches[0]),
        (replace(complete.patches[0], index=3),),
    ):
        with pytest.raises(ValueError, match="indices"):
            replace(complete, status=status, error="Stop", patches=patches)


def test_patch_id_must_match_expected_position(complete):
    with pytest.raises(ValueError, match="requested position"):
        replace(
            complete,
            expected_patch_ids=("different", "patch-1", "patch-2"),
            request_json=request_json(patch_ids=["different", "patch-1", "patch-2"]),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "other-operation"},
        {"device": DeviceIdentity("other-device", "synthetic model", "fixture", "1")},
        {"requested_mode": "other-mode", "actual_mode": "other-mode"},
    ],
)
def test_observation_identity_must_match_envelope(complete, changes):
    patch = replace(complete.patches[1], observation=replace(reading(1), **changes))
    with pytest.raises(ValueError, match="identity disagrees"):
        replace(complete, patches=(complete.patches[0], patch, complete.patches[2]))


@pytest.mark.parametrize(
    "changes,refusal",
    [
        ({"observation_id": "reading-0"}, "Observation IDs must be unique"),
        ({"provenance": "physical"}, "device identity and provenance must match the operation"),
        ({"device": DeviceIdentity("fixture", "other model", "fixture", "1")},
         "device identity and provenance must match the operation"),
        ({"device": DeviceIdentity("fixture", "synthetic model", "fixture", "2")},
         "device identity and provenance must match the operation"),
        ({"device": DeviceIdentity("fixture", "synthetic model", "fixture", "1",
                                   "other-serial", "utf8")},
         "device identity and provenance must match the operation"),
    ],
)
def test_patches_cannot_mix_observation_ids_device_metadata_or_provenance(
        complete, changes, refusal):
    patch = replace(complete.patches[1], observation=replace(reading(1), **changes))
    with pytest.raises(ValueError, match=refusal):
        replace(complete, patches=(complete.patches[0], patch, complete.patches[2]))


@pytest.mark.parametrize("location", ["envelope", "patch", "observation", "device"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_json_rejects_schema_drift_and_duplicate_fields_at_each_level(complete, location, mutation):
    data = asdict(complete)
    path = {
        "envelope": (),
        "patch": ("patches", 0),
        "observation": ("patches", 0, "observation"),
        "device": ("patches", 0, "observation", "device"),
    }[location]
    target = data
    for component in path:
        target = target[component]
    field = next(iter(target))
    if mutation == "missing":
        target.pop(field)
    elif mutation == "extra":
        target["unknown"] = None
    if mutation == "duplicate":
        original = target[field]
        target[field] = "__duplicate_field_value__"
        text = json.dumps(data)
        placeholder = json.dumps(field) + ": " + json.dumps(target[field])
        valid_pair = json.dumps(field) + ": " + json.dumps(original)
        assert text.count(placeholder) == 1
        text = text.replace(placeholder, valid_pair + ", " + valid_pair, 1)
        pairs = json.loads(text, object_pairs_hook=list)
        for component in path:
            pairs = pairs[component] if isinstance(component, int) else dict(pairs)[component]
        assert [value for key, value in pairs if key == field] == [original, original]
        assert json.loads(text) == json.loads(json.dumps(asdict(complete)))
    else:
        text = json.dumps(data)
    with pytest.raises(
        ValueError, match="Duplicate JSON field" if mutation == "duplicate" else None
    ):
        result_from_json(text)


@pytest.mark.parametrize("replacement", [[], {}, 0])
def test_json_cannot_replace_optional_quantity_null_with_other_values(complete, replacement):
    data = asdict(complete)
    data["patches"][0]["observation"]["backend_reported"]["spectrum"] = replacement
    with pytest.raises(ValueError, match="declared optional record type"):
        result_from_json(json.dumps(data))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True])
def test_nonfinite_and_boolean_scientific_values_are_rejected(complete, invalid):
    data = asdict(complete)
    data["patches"][0]["observation"]["backend_reported"]["xyz"]["values"] = (invalid, 25, 10)
    # The optional-record refusal retains the invalid quantity's reason.
    with pytest.raises(ValueError, match="declared optional record type"):
        result_from_json(json.dumps(data))


def test_frozen_result_patch_and_nested_observation(complete):
    for record, field, value in (
        (complete, "status", "failed"),
        (complete.patches[0], "index", 2),
        (complete.patches[0].observation, "sample_id", "changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)


def test_serializer_revalidates_direct_records_and_refuses_other_types(complete):
    with pytest.raises(ValueError, match="OperationResult record"):
        result_to_json(asdict(complete))
    object.__setattr__(complete.patches[0].observation, "schema_version", True)
    # The union refusal retains the observation's invalid schema reason.
    with pytest.raises(ValueError, match="declared optional record type"):
        result_to_json(complete)


@pytest.mark.parametrize("text,refusal", [
    ("{}", "fields differ"), ("[]", "Expected a record object"),
    ("null", "Expected a record object"), ("{", "Expecting property name"),
    ("1", "Expected a record object"), (b"{}", "Operation-result JSON must be text"),
    (None, "Operation-result JSON must be text"),
])
def test_invalid_or_wrongly_typed_json_is_rejected(text, refusal):
    with pytest.raises(ValueError, match=refusal):
        result_from_json(text)


@pytest.mark.parametrize("field", ["device_id", "mode", "workflow", "patch_ids"])
@pytest.mark.parametrize("change", ["missing", "mismatch"])
def test_request_must_identify_same_device_mode_workflow_and_patch_order(complete, field, change):
    request = json.loads(complete.request_json)
    if change == "missing":
        request.pop(field)
    else:
        request[field] = ["patch-2", "patch-1", "patch-0"] if field == "patch_ids" else "other"
    with pytest.raises(ValueError, match="Retained request"):
        replace(complete, request_json=json.dumps(request))


@pytest.mark.parametrize("request_text,refusal", [
    (None, "Retained request JSON must be text"), ([], "Retained request JSON must be text"),
    ("null", "Retained request must be a JSON object"),
    ("[]", "Retained request must be a JSON object"), ("{", "Expecting property name"),
    ("1", "Retained request must be a JSON object"),
])
def test_retained_request_must_be_json_object_text(complete, request_text, refusal):
    with pytest.raises(ValueError, match=refusal):
        replace(complete, request_json=request_text)


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_nonfinite_request_options_are_rejected_anywhere(complete, invalid):
    request = json.loads(complete.request_json)
    request["options"] = {"nested": ["replace"]}
    text = json.dumps(request).replace('"replace"', invalid)
    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        replace(complete, request_json=text)


def test_retained_request_rejects_duplicate_fields_and_operation_id(complete):
    duplicate = complete.request_json.replace('"options": []', '"options": [], "options": []')
    with pytest.raises(ValueError, match="Duplicate JSON field"):
        replace(complete, request_json=duplicate)
    duplicate_nested = complete.request_json.replace('"options": []', '"options": {"x": 1, "x": 2}')
    with pytest.raises(ValueError, match="Duplicate JSON field"):
        replace(complete, request_json=duplicate_nested)
    with pytest.raises(ValueError, match="must not assign an operation ID"):
        replace(complete, request_json=request_json(operation_id="operation-1"))


def test_observation_warnings_survive_json_without_changing_native_values(complete):
    original = complete.patches[0]
    observation = replace(original.observation, warnings=("Synthetic interpretation context",))
    patch = replace(original, observation=observation)
    result = replace(complete, patches=(patch, *complete.patches[1:]))
    loaded = result_from_json(result_to_json(result))
    assert loaded == result
    assert loaded.patches[0].observation.warnings == observation.warnings
    assert loaded.patches[0].observation.backend_reported == original.observation.backend_reported


def test_single_patch_must_match_explicit_operation_provenance_and_device(complete):
    result = replace(
        complete,
        expected_patch_ids=("patch-0",),
        patches=(complete.patches[0],),
        request_json=request_json(patch_ids=["patch-0"]),
    )
    for change in (
        {"provenance": "physical"},
        {"device": replace(result.device, serial="different-serial", serial_encoding="utf8")},
    ):
        with pytest.raises(ValueError, match="must match the operation"):
            replace(result, **change)


def test_optional_serial_refines_once_and_cannot_switch_between_patches(complete):
    patches = tuple(replace(patch, observation=replace(patch.observation,
        device=replace(patch.observation.device, serial="observed-unit", serial_encoding="utf8")))
        for patch in complete.patches)
    result = replace(complete, patches=patches)
    assert result_from_json(result_to_json(result)) == result
    switched = replace(patches[1], observation=replace(patches[1].observation,
        device=replace(patches[1].observation.device, serial="other-unit")))
    with pytest.raises(ValueError, match="device identity"):
        replace(complete, patches=(patches[0], switched, *patches[2:]))
    with pytest.raises(ValueError, match="device identity"):
        replace(result, device=replace(result.device, serial="pinned-other-unit",
                                      serial_encoding="utf8"))


def test_serial_encoding_disambiguates_native_bytes_and_utf8_across_a_batch(complete):
    serial = "í\u00a0\u0080"
    assert serial.encode("latin-1") == bytes.fromhex("ed a0 80")
    assert serial.encode("utf-8") == bytes.fromhex("c3 ad c2 a0 c2 80")
    def patches(encoding):
        return tuple(replace(patch, observation=replace(patch.observation,
            device=replace(patch.observation.device, serial=serial, serial_encoding=encoding)))
            for patch in complete.patches)
    byte_result = replace(complete, patches=patches("bytes"))
    utf8_result = replace(complete, patches=patches("utf8"))
    assert result_to_json(byte_result) != result_to_json(utf8_result)
    assert result_from_json(result_to_json(byte_result)) == byte_result
    with pytest.raises(ValueError, match="device identity"):
        replace(byte_result, patches=(byte_result.patches[0], *utf8_result.patches[1:]))
    with pytest.raises(ValueError, match="device identity"):
        replace(byte_result, device=replace(complete.device, serial=serial, serial_encoding="utf8"))


@pytest.mark.parametrize("serial,encoding", [("", "utf8"), (None, "utf8"),
    ("A", None), ("A", "unknown"), ("A\x00B", "bytes"), ("\ud800", "utf8"),
    ("🔬", "bytes")])
def test_device_serial_requires_a_representable_encoding_pair(serial, encoding):
    with pytest.raises(ValueError, match="serial"):
        DeviceIdentity("fixture", "model", "backend", "1", serial, encoding)


@pytest.mark.parametrize("changes, message", [
    ({"error_stage": "read_sample"}, "both a stage and a code, or neither"),
    ({"error_native_code": 7}, "both a stage and a code, or neither"),
    ({"error_stage": "read_sample", "error_native_code": True}, "integer inst_code"),
    ({"error_stage": "read_sample", "error_native_code": 7},
     "completed operation carries no native failure"),
    ({"error_stage": " ", "error_native_code": 7, "status": "failed", "error": "x"}, None),
])
def test_a_native_failure_is_a_paired_stage_and_code_on_a_failed_operation(
    complete, changes, message,
):
    """operation_records.py pairs the stage with its code, types the code and
    forbids both on a completed operation; each rule needs a case that fires it."""
    with pytest.raises(ValueError, match=message):
        replace(complete, **changes)
