"""Explicit SPECT projection boundaries and companion-first recovery."""

import hashlib
import json
import os
import shlex
from dataclasses import replace
from pathlib import Path

import pytest

from color_measurement.cgats import read_cgats
from color_measurement.models import (
    CalibrationContext,
    ComputedValues,
    DeviceIdentity,
    Observation,
    ReportedValues,
    Spectrum,
)
from color_measurement.operation_exports import OperationSaveError
from color_measurement.operation_records import OperationResult, PatchResult, result_to_json
from color_measurement.spectral_exports import save_sp


def reflective_result(mode="reflective_spot_m1", condition=1):
    device = DeviceIdentity("fixture", "Synthetic spectra", "synthetic", "1", "0001", "utf8")
    calibration = CalibrationContext("not_applicable", "No hardware")
    configuration = (("native_mtype", "5"), ("native_mcond", str(condition)),
                     ("spectrum_norm", "100.0"))
    patches = tuple(PatchResult(index, f"patch-{index}", Observation(
        1, f"observation-{index}", "operation", f"patch-{index}",
        "2026-09-19T00:00:00+00:00", "simulated", device, mode, mode, configuration,
        calibration, ReportedValues(spectrum=Spectrum(
            (400, 410, 420), (-0.25, 0, 101.5 + index), "reflectance", "%"
        )), ComputedValues(), "Synthetic native output", ("SIMULATED",),
    )) for index in range(3))
    return OperationResult(
        1, "operation", "fixture", device, "simulated", mode, "scan", "completed",
        tuple(p.patch_id for p in patches), patches,
        json.dumps({"device_id": "fixture", "mode": mode, "workflow": "scan",
                    "patch_ids": [p.patch_id for p in patches]}),
        (), calibration, None, True, "not_applicable",
    )


@pytest.mark.parametrize("mode,condition,tag,label", [
    ("reflective_spot_m1", 1, "D50", "M1"),
    ("reflective_spot_m2", 3, "UVCUT", "M2"),
    ("reflective_spot_m3", 4, "POLARIZED", "M3"),
])
def test_spect_quantity_condition_companion_and_signed_values(
    tmp_path, mode, condition, tag, label
):
    record = reflective_result(mode, condition)
    path = save_sp(record, tmp_path / "out.sp", canonical_path=tmp_path / "out.json").path
    text = path.read_text()
    keywords = {}
    for line in text.splitlines():
        tokens = shlex.split(line, comments=True)
        if len(tokens) == 2:
            keywords[tokens[0]] = tokens[1]
    assert keywords["MEAS_CONDITIONS"] == tag
    assert keywords["MEAS_TYPE"] == "REFLECTIVE"
    assert keywords["SPECTRAL_NORM"] == "100"
    # read_nxspect_1 derives each SPEC_ field name from these three keywords
    # and finds that field in the format table (xspect.c:3871-3904).
    assert (keywords["SPECTRAL_BANDS"], keywords["SPECTRAL_START_NM"],
            keywords["SPECTRAL_END_NM"]) == ("3", "400", "420")
    assert keywords["CM_CONDITION"] == label
    assert keywords["CM_MODE"] == mode
    assert keywords["CM_PROVENANCE"] == "simulated"
    assert keywords["CM_CANONICAL_FILE"] == "out.json"
    canonical = (tmp_path / "out.json").read_bytes()
    assert canonical.decode() == result_to_json(record)
    assert keywords["CM_CANONICAL_SHA256"] == hashlib.sha256(canonical).hexdigest()
    assert "SPEC_400 SPEC_410 SPEC_420" in text
    rows = text.split("BEGIN_DATA\n")[1].split("END_DATA")[0].splitlines()
    # A bare all-zero column is inferred as CGATS integer type and rejected by
    # Argyll's read_nxspect, even though Python float() accepts its tokens.
    assert all("." in row.split()[1] or "e" in row.split()[1] for row in rows)
    assert [[float(value) for value in row.split()] for row in rows] == [
        [-0.25, 0, 101.5], [-0.25, 0, 102.5], [-0.25, 0, 103.5]
    ]


def test_partial_projection_keeps_rows_in_acquired_order_and_canonical_gap(tmp_path):
    original = reflective_result()
    record = replace(original, status="cancelled", error="Stopped", patches=(
        original.patches[0], original.patches[2],
    ))
    path = save_sp(record, tmp_path / "partial.sp", canonical_path=tmp_path / "partial.json").path
    assert "NUMBER_OF_SETS 2" in path.read_text()
    data = json.loads((tmp_path / "partial.json").read_text())
    assert [p["index"] for p in data["patches"]] == [0, 2]
    assert data["expected_patch_ids"] == ["patch-0", "patch-1", "patch-2"]
    assert data["status"] == "cancelled"


#: The first refusal each case must reach. Only the first patch is edited, so a grid
#: case would otherwise also fail the same-grid check against the unedited patches.
REFUSAL = {
    "m0": "supports reflective M1/M2/M3", "missing": "native reflective percent data",
    "unit": "native reflective percent data", "quantity": "native reflective percent data",
    "condition": "native reflective percent data", "norm": "native reflective percent data",
    "irregular": "uniform wavelength grid", "fractional": "integer-nm samples",
    "mixed": "same grid for every retained patch", "too_many": "integer-nm samples",
    "large_wavelength": "integer-nm samples", "precision": "exactly representable",
    "zero_rows": "requires acquired spectra", "one_band": "integer-nm samples",
}


@pytest.mark.parametrize("invalid", [
    "m0", "missing", "unit", "quantity", "condition", "norm", "irregular",
    "fractional", "mixed", "too_many", "large_wavelength", "precision", "zero_rows",
    "one_band",
])
def test_unrepresentable_projection_refuses_before_any_file_is_created(tmp_path, invalid):
    record = reflective_result()
    first = record.patches[0]
    observation = first.observation
    spectrum = observation.backend_reported.spectrum
    if invalid == "m0":
        record = reflective_result("reflective_spot_m0", 0)
    elif invalid == "zero_rows":
        record = replace(record, status="failed", error="No readings", patches=())
    else:
        if invalid == "missing":
            observation = replace(observation, configuration=())
        elif invalid in ("condition", "norm"):
            metadata = dict(observation.configuration)
            metadata["native_mcond" if invalid == "condition" else "spectrum_norm"] = "2"
            observation = replace(observation, configuration=tuple(metadata.items()))
        else:
            changes = {
                "unit": {"unit": "1"},
                "quantity": {"quantity": "spectral_radiance"},
                "irregular": {"wavelengths_nm": (400, 411, 420)},
                "fractional": {"wavelengths_nm": (400.5, 410.5, 420.5)},
                "mixed": {"wavelengths_nm": (401, 411, 421)},
                "too_many": {"wavelengths_nm": tuple(range(1, 603)), "values": (1,) * 602},
                "one_band": {"wavelengths_nm": (400,), "values": (1,)},
                "large_wavelength": {"wavelengths_nm": (2**31, 2**31 + 1, 2**31 + 2)},
                "precision": {"values": (2**53 + 1, 0, 100)},
            }[invalid]
            observation = replace(observation, backend_reported=ReportedValues(
                spectrum=replace(spectrum, **changes)
            ))
        record = replace(
            record, patches=(replace(first, observation=observation),) + record.patches[1:]
        )
    with pytest.raises(ValueError, match=REFUSAL[invalid]):
        save_sp(record, tmp_path / "refused.sp", canonical_path=tmp_path / "refused.json")
    assert list(tmp_path.iterdir()) == []


def test_companion_conflict_and_existing_sp_are_never_overwritten(tmp_path):
    record = reflective_result()
    companion = tmp_path / "out.json"
    companion.write_text("different bytes")
    with pytest.raises(ValueError, match="companion bytes differ"):
        save_sp(record, tmp_path / "out.sp", canonical_path=companion)
    assert companion.read_text() == "different bytes"
    assert not (tmp_path / "out.sp").exists()
    path = tmp_path / "existing.sp"
    path.write_text("retained file")
    with pytest.raises(OperationSaveError):
        save_sp(record, path, canonical_path=tmp_path / "new.json")
    assert path.read_text() == "retained file"
    assert not (tmp_path / "new.json").exists()


def test_sp_save_failure_retains_canonical_and_explicit_retry_reuses_it(tmp_path, monkeypatch):
    record = reflective_result()
    original_link = os.link

    def fail_sp(source, destination):
        if str(destination).endswith(".sp"):
            raise OSError("Synthetic SP publication failure")
        return original_link(source, destination)

    with monkeypatch.context() as context:
        context.setattr(os, "link", fail_sp)
        with pytest.raises(OperationSaveError) as failure:
            save_sp(record, tmp_path / "out.sp", canonical_path=tmp_path / "out.json")
    assert failure.value.result is record and failure.value.format == "sp"
    assert not (tmp_path / "out.sp").exists()
    assert (tmp_path / "out.json").read_text() == result_to_json(record)
    assert not list(tmp_path.glob(".measurement-*"))
    path = save_sp(
        failure.value.result, tmp_path / "out.sp", canonical_path=tmp_path / "out.json"
    ).path
    assert path.read_bytes() == failure.value.payload


@pytest.mark.parametrize("canonical_exists", [False, True])
def test_spect_save_reports_optional_ti3_failure_and_explicit_retry(
    tmp_path, monkeypatch, canonical_exists
):
    from color_measurement import ti3_exports

    original = reflective_result()
    record = replace(original, patches=tuple(
        replace(patch, observation=replace(patch.observation, backend_reported=ReportedValues(
            spectrum=Spectrum((400, 410, 420, 430), (-0.25, 0, 101.5, 50), "reflectance", "%")
        ))) for patch in original.patches
    ))
    companion = tmp_path / "out.json"
    canonical = result_to_json(record).encode("utf-8")
    if canonical_exists:
        companion.write_bytes(canonical)

    def unavailable(*args):
        raise OSError("Synthetic TI3 publication failure")

    with monkeypatch.context() as context:
        context.setattr(ti3_exports, "_publish_owned_file", unavailable)
        saved = save_sp(record, tmp_path / "first.sp", canonical_path=companion)
    assert saved.path == tmp_path / "first.sp" and saved.path.is_file()
    assert saved.ti3["status"] == "stale"
    assert saved.ti3["files"][0]["reason"] == "Synthetic TI3 publication failure"
    assert companion.read_bytes() == canonical
    retried = save_sp(record, tmp_path / "retry.sp", canonical_path=companion,
                      previous_ti3=saved.ti3)
    assert retried.path.is_file() and retried.ti3["status"] == "current"
    (file,) = retried.ti3["files"]
    assert file["row_ids"] == [patch.observation.observation_id for patch in record.patches]
    assert Path(file["path"]).is_file()
    assert companion.read_bytes() == canonical


@pytest.mark.parametrize("name", ["../outside.json", "out.sp", 'bad"name.json', "測定.json"])
def test_companion_path_contract(tmp_path, name):
    with pytest.raises(ValueError):
        save_sp(reflective_result(), tmp_path / "out.sp", canonical_path=tmp_path / name)
    assert list(tmp_path.iterdir()) == []


def test_the_largest_spect_grid_is_accepted_and_declared(tmp_path):
    """601 integer-nm bands is the reader's ceiling; 602 is refused above, so the
    accepted side of the bound is pinned here with the keywords it writes."""
    record = reflective_result()
    patches = tuple(
        replace(patch, observation=replace(
            patch.observation, backend_reported=ReportedValues(spectrum=replace(
                patch.observation.backend_reported.spectrum,
                wavelengths_nm=tuple(range(400, 1001)), values=(1.0,) * 601))))
        for patch in record.patches)
    record = replace(record, patches=patches)
    path = save_sp(record, tmp_path / "wide.sp", canonical_path=tmp_path / "wide.json").path
    keywords = read_cgats(path.read_text(), expected_identifier="SPECT")[0].keywords
    assert (keywords["SPECTRAL_BANDS"], keywords["SPECTRAL_START_NM"],
            keywords["SPECTRAL_END_NM"]) == ("601", "400", "1000")
