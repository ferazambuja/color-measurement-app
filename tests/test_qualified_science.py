"""Actual retained-input composition, explicit conventions and separate derived values."""

import json
import math
from copy import deepcopy
from uuid import uuid4

import pytest
from test_analysis_exports import csv_rows, export_params
from test_dataset_binding import open_saved
from test_dataset_binding import saved as _saved_fixture
from test_desktop_session import create, measure, query, send, wait

from color_measurement import demo, qualified_science
from color_measurement.dataset_binding import content_digest
from color_measurement.demo import demo_frontends
from color_measurement.desktop_session import DesktopSession
from color_measurement.instrument import Instrument
from color_measurement.models import Spectrum
from color_measurement.qualified_science import derive_readings
from color_measurement.reading_dataset import reading_key

saved = _saved_fixture


def derive(session, request, selected=None):
    data = session.dataset()
    return query(session, "derive", dict(
        dataset_revision=data["dataset_revision"],
        selected=list(data["readings"])[:1] if selected is None else selected,
        derivation=request,
    ))


def spectral_context(**changes):
    return dict(observer="CIE1931_2", wavelength_convention="standard-air-nm",
                illuminant=None, illuminant_id=None, nonfluorescent_reflectance=False) | changes


def finish(session):
    identity = str(uuid4())
    outcome = send(session, "finish", identity=identity)
    assert outcome["admission"] == "admitted"
    wait(session, lambda state: session.batch.command_outcome(identity)["state"] != "pending")
    assert session.batch.command_outcome(identity)["state"] == "completed"


def test_adapted_white_keeps_native_scale_and_uses_matching_lab_white(saved, tmp_path):
    folder, _, _, pairs = saved
    session = DesktopSession([])
    open_saved(session, folder)
    original = session.dataset()
    key = next(iter(original["readings"]))
    native = original["readings"][key]["observation"]["backend_reported"]["xyz"]
    request = dict(method="adapt_xyz", context=dict(
        source_white=dict(pairs[0], kind="retained-reading"),
        destination_white=dict(kind="theoretical-lab-reference", name="D50", reference_y=100,
                               unit=native["unit"], scale=native["scale"],
                               observer=native["observer"]),
    ))
    answer = derive(session, request)
    output = answer["by_source"][key]
    assert output["reason"] is None
    result = output["result"]
    assert result["values"][1] == pytest.approx(native["values"][1], rel=2e-14)
    assert result["lab_white"][1] == native["values"][1]
    assert result["destination_white"]["reference_y"] == 100
    assert result["lab"] == pytest.approx((100, 0, 0), abs=2e-12)
    assert result["origin"] == "derived" and result["unit"] == native["unit"]
    assert session.dataset() == original
    target = tmp_path / "adaptation.json"
    receipt = session.export_analysis(target, **export_params(session), derivation=request)
    exported = json.loads(target.read_bytes())
    assert exported["derived_science"]["context_sha256"] == content_digest(request)
    assert exported["records"][0]["observation"]["backend_reported"]["xyz"] == \
        json.loads(json.dumps(native))
    assert receipt["selection"] == [key]


def test_native_radiance_derivation_marks_limited_range_and_preserves_source(saved):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    before = session.dataset()
    key = next(iter(before["readings"]))
    spectrum = before["readings"][key]["observation"]["backend_reported"]["spectrum"]
    result = derive(session, dict(method="spectral_xyz", context=spectral_context()))
    output = result["by_source"][key]
    assert output["reason"] is None
    computed = output["result"]
    assert computed["origin"] == "derived" and computed["unit"] == "cd/m^2"
    assert computed["spectrum"] == spectrum
    assert computed["limited_range"] == (spectrum["wavelengths_nm"][0] > 360
                                         or spectrum["wavelengths_nm"][-1] < 830)
    assert computed["observer_sha256"] and computed["algorithm_version"] == "1"
    assert session.dataset() == before


def test_missing_observer_resource_returns_spectral_reason_without_borrowing_native_xyz(
    saved, tmp_path, monkeypatch,
):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    before = session.dataset()
    missing = tmp_path / "missing-observer.csv"
    monkeypatch.setattr(qualified_science, "RESOURCE", missing)
    reply = derive(session, dict(method="spectral_xyz", context=spectral_context()))
    assert reply["by_source"]
    for output in reply["by_source"].values():
        assert output["result"] is None
        assert "Observer resource is unavailable" in output["reason"]
        assert missing.name in output["reason"]
    assert session.dataset() == before


@pytest.mark.parametrize("changes", [dict(observer="CIE1964_10"),
                                     dict(wavelength_convention="unspecified")])
def test_unqualified_spectral_context_returns_reason_without_borrowing_native_xyz(saved, changes):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    before = session.dataset()
    reply = derive(session, dict(method="spectral_xyz", context=spectral_context(**changes)))
    item, = reply["by_source"].values()
    assert item["result"] is None and "standard-air-nm" in item["reason"]
    assert session.dataset() == before


def test_reflectance_needs_real_same_grid_spd_not_nominal_xyz_white(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *args, **kwargs: pytest.fail("Native transport"),
    )
    session = DesktopSession(demo_frontends())
    settings = session.settings(None)
    token = next(row["selection_id"] for row in settings["selections"]
                 if row["device_id"] == "demo:i1pro3")
    descriptor = session.settings(token)["descriptor"]
    mode = next(mode["mode"] for mode in descriptor["modes"]
                if mode["mode"].startswith("reflective_spot") and mode["available"])
    create(session, tmp_path / "reflectance", "Reflectance Demo", "", mode=mode)
    measure(session)
    before = session.dataset()
    key = next(iter(before["readings"]))
    source = before["readings"][key]["observation"]["backend_reported"]["spectrum"]
    request = dict(method="spectral_xyz", context=spectral_context(nonfluorescent_reflectance=True))
    missing = derive(session, request)["by_source"][key]
    assert missing["result"] is None and "SPD" in missing["reason"]
    # Explicit synthetic input for software proof, not a claimed physical illuminant.
    spd = dict(wavelengths_nm=list(source["wavelengths_nm"]),
               values=[1.0] * len(source["values"]), quantity="relative_spectral_power", unit="1")
    request["context"].update(illuminant=spd, illuminant_id="Synthetic uniform SPD")
    computed = derive(session, request)["by_source"][key]
    assert computed["reason"] is None
    assert computed["result"]["reference_white"][1] == pytest.approx(100)
    assert computed["result"]["unit"] == "1" and computed["result"]["illuminant_id"] == \
        "Synthetic uniform SPD"
    # Optional file publication advances the roster clock, not scientific inputs.
    assert {key: value for key, value in session.dataset().items() if key != "roster_revision"} == \
        {key: value for key, value in before.items() if key != "roster_revision"}
    finish(session)


def test_adaptation_refuses_white_observer_mismatch_before_any_result(saved):
    folder, _, _, pairs = saved
    session = DesktopSession([])
    open_saved(session, folder)
    request = dict(method="adapt_xyz", context=dict(
        source_white=dict(pairs[0], kind="retained-reading"),
        destination_white=dict(kind="theoretical-lab-reference", name="D50", reference_y=100,
                               unit="cd/m^2", scale="absolute; Y is luminance",
                               observer="CIE1964_10"),
    ))
    with pytest.raises(ValueError, match="observer"):
        derive(session, request)


@pytest.mark.parametrize("format", ["json", "csv"])
def test_finite_adaptation_survives_overflowed_lab_white_in_saved_export(
    tmp_path, monkeypatch, format,
):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *args, **kwargs: pytest.fail("Native transport"),
    )
    apply = demo._DemoSamples.apply
    xyz = [(1e308, 1.7e308, 1e308), (0.1, 0.2, 0.1)]

    def synthetic_xyz(self, patch, mode, chart_sample=None):
        index = self.index
        apply(self, patch, mode, chart_sample)
        # Explicit synthetic XYZ substitution, not a physical observation or SPD.
        patch["backend_reported"]["XYZ"] = list(xyz[index])

    monkeypatch.setattr(demo._DemoSamples, "apply", synthetic_xyz)
    source = DesktopSession(demo_frontends())
    folder = tmp_path / "synthetic-extreme"
    create(source, folder, "Synthetic finite extreme XYZ", "Software-only XYZ substitution")
    measure(source)
    measure(source)
    finish(source)
    source_bytes = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    session = DesktopSession([])
    packet = open_saved(session, folder)
    assert packet["dataset_binding"]["read_only"] and session.batch is None
    before = session.dataset()
    white_key, sample_key = before["readings"]
    white = before["readings"][white_key]["observation"]
    native = before["readings"][sample_key]["observation"]["backend_reported"]["xyz"]
    assert white["backend_reported"]["xyz"]["values"] == xyz[0]
    assert native["values"] == xyz[1]
    request = dict(method="adapt_xyz", context=dict(
        source_white=dict(kind="retained-reading", **{
            field: white[field] for field in ("operation_id", "observation_id")
        }),
        destination_white=dict(kind="theoretical-lab-reference", name="D65", reference_y=100,
                               unit=native["unit"], scale=native["scale"],
                               observer=native["observer"]),
    ))
    answer = derive(session, request, selected=[sample_key])
    output = answer["by_source"][sample_key]
    assert output["reason"] is None
    result = output["result"]
    assert all(math.isfinite(value) for value in result["values"])
    assert result["values"] != native["values"]
    assert result["lab_white"] is None and result["lab"] is None
    assert "not finite" in result["lab_white_reason"]
    assert result["lab_reason"] == result["lab_white_reason"]
    assert tuple(result["source_white"]["xyz"]) == xyz[0]
    json.dumps(answer, allow_nan=False)  # The native reply must remain serializable too.
    target = tmp_path / ("extreme." + format)
    receipt = session.export_analysis(
        target, **export_params(session, selected=[sample_key], format=format), derivation=request,
    )
    json.dumps(receipt, allow_nan=False)
    if format == "json":
        exported = json.loads(target.read_text(encoding="utf-8"))
        record, = exported["records"]
    else:
        row, = csv_rows(target.read_text(encoding="utf-8"))
        exported = json.loads(row["export_context_json"])
        exported.update(json.loads(row["source_documents_json"]))
        record = json.loads(row["record_json"])
    json.dumps(exported, allow_nan=False)
    assert exported["binding_id"] == answer["binding_id"]
    assert exported["binding_generation"] == answer["generation"]
    science_reply = {field: value for field, value in answer.items()
                     if field not in {"binding_id", "generation"}}
    assert exported["derived_science"] == json.loads(json.dumps(science_reply))
    assert record["observation"]["backend_reported"]["xyz"] == json.loads(json.dumps(native))
    assert exported["role_inputs"]["adaptation_source_white"]["selected"] is False
    assert session.dataset() == before
    after = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    assert after == source_bytes


def ratio_context(reference, **changes):
    return dict(reference=reference, characterization=None, characterization_id=None,
                assume_ideal_white=True, setup_declaration="Declared matched synthetic setup",
                matched_setup=True, nonfluorescent_reflectance=True) | changes


def relight_context(**changes):
    return dict(reflectance=dict(kind="native"), illuminant="D65", illuminant_id=None,
                observer="CIE1931_2", wavelength_convention="standard-air-nm",
                nonfluorescent_reflectance=True, color_space="srgb") | changes


def synthetic_spectral_row(reference, values, *, wavelengths=(380, 390, 400),
                           quantity="spectral_radiance", unit="mW/(m^2 sr nm)"):
    observation = dict(
        record_kind="color", operation_id=reference["operation_id"],
        observation_id=reference["observation_id"], configuration=[],
        backend_reported=dict(xyz=None, spectrum=dict(
            wavelengths_nm=wavelengths, values=values, quantity=quantity, unit=unit,
        )),
    )
    return reading_key(reference), dict(saved=True, observation=observation, reading_ref=reference)


def test_characterized_factor_preserves_qualified_reference_and_original_inputs():
    reference = dict(operation_id="reference", observation_id="same-native-id")
    inherited = dict(reference, segment_id="11111111-1111-4111-8111-111111111111",
                     source_manifest_sha256="a" * 64)
    sample = dict(operation_id="sample", observation_id="sample")
    selected, row = synthetic_spectral_row(sample, (1, 2, 3))
    readings = dict([synthetic_spectral_row(reference, (20, 40, 60)),
                     synthetic_spectral_row(inherited, (2, 4, 6)), (selected, row)])
    before = deepcopy(readings)
    characterization = dict(wavelengths_nm=(380, 390, 400), values=(0.8, 0.6, 0.4),
                            quantity="reflectance", unit="1")
    request = dict(method="characterized_reflectance", context=ratio_context(
        inherited, assume_ideal_white=False, characterization=characterization,
        characterization_id="Declared synthetic characterization",
    ))
    reply = derive_readings(readings, [selected], request)
    result = reply["by_source"][selected]["result"]
    assert result["spectrum"]["values"] == pytest.approx((0.4, 0.3, 0.2))
    assert result["characterization_sha256"] == content_digest(characterization)
    assert result["source_inputs"]["reference"]["reading_ref"] == inherited
    assert result["source_inputs"]["reference"]["observation"] == \
        readings[reading_key(inherited)]["observation"]
    assert result["source_inputs"]["reference"]["observation_sha256"] == content_digest(
        readings[reading_key(inherited)]["observation"])
    assert readings == before


def test_valid_reference_factor_survives_unavailable_target_and_signed_estimates():
    reference = dict(operation_id="reference", observation_id="reference")
    sample = dict(operation_id="sample", observation_id="sample")
    selected, row = synthetic_spectral_row(sample, (-1, 10, 3))
    readings = dict([synthetic_spectral_row(reference, (2, 4, 6)), (selected, row)])
    request = dict(method="relight_reflectance", context=relight_context(
        reflectance=dict(kind="characterized-reference", context=ratio_context(reference)),
        illuminant="Unidentified target",
    ))
    reply = derive_readings(readings, [selected], request)
    item = reply["by_source"][selected]
    assert item["reason"] is None
    result = item["result"]
    assert result["reference_factor"]["spectrum"]["values"] == (-0.5, 2.5, 0.5)
    assert result["xyz"] is None and "D50, D65 or A" in result["xyz_reason"]
    assert result["lab"] is None and result["corresponding_color"] is None
    assert result["reference_factor"]["assume_ideal_white"] is True


def test_native_constant_reflectance_relights_with_common_white_and_authenticated_resources():
    reference = dict(operation_id="sample", observation_id="sample")
    selected, row = synthetic_spectral_row(reference, (0.5,) * 471,
        wavelengths=tuple(range(360, 831)), quantity="reflectance", unit="1")
    before = deepcopy(row)
    request = dict(method="relight_reflectance", context=relight_context())
    result = derive_readings({selected: row}, [selected], request)["by_source"][selected]["result"]
    assert result["xyz"]["values"] == pytest.approx(
        tuple(value / 2 for value in result["xyz"]["reference_white"]), rel=3e-14)
    assert result["xyz"]["values"][1] == pytest.approx(50, rel=3e-14)
    assert result["lab"] == pytest.approx((116 * math.cbrt(0.5) - 16, 0, 0), abs=2e-12)
    assert result["corresponding_color"]["linear_rgb"] == pytest.approx((0.5,) * 3, abs=2e-5)
    assert result["target_illuminant"]["source_sha256"] in result["xyz"]["illuminant_id"]
    assert result["xyz"]["observer_sha256"]
    assert result["reference_factor"] is None and row == before


def test_truncated_target_white_keeps_xyz_when_lab_and_display_model_are_unavailable():
    reference = dict(operation_id="sample", observation_id="sample")
    selected, row = synthetic_spectral_row(reference, (0.5, 0.5), wavelengths=(650, 660),
                                           quantity="reflectance", unit="1")
    result = derive_readings({selected: row}, [selected], dict(
        method="relight_reflectance", context=relight_context(),
    ))["by_source"][selected]["result"]
    assert result["xyz"]["values"][1] == pytest.approx(50)
    assert result["xyz"]["limited_range"] is True
    assert result["lab"] is None and result["lab_reason"]
    assert result["corresponding_color"] is None and result["color_reason"]


@pytest.mark.parametrize("reference_values,wavelengths,reason", [
    ((0, 4, 6), (380, 390, 400), "nonpositive"),
    ((2, 4, 6), (380, 395, 410), "same-grid"),
])
def test_reference_factor_refuses_weak_or_different_native_grid(
    reference_values, wavelengths, reason,
):
    reference = dict(operation_id="reference", observation_id="reference")
    sample = dict(operation_id="sample", observation_id="sample")
    selected, row = synthetic_spectral_row(sample, (1, 2, 3))
    readings = dict([synthetic_spectral_row(reference, reference_values, wavelengths=wavelengths),
                     (selected, row)])
    item = derive_readings(readings, [selected], dict(
        method="characterized_reflectance", context=ratio_context(reference),
    ))["by_source"][selected]
    assert item["result"] is None and reason in item["reason"]


def test_ratio_declarations_and_native_relighting_cannot_borrow_radiance():
    reference = dict(operation_id="reference", observation_id="reference")
    selected, row = synthetic_spectral_row(reference, (1, 2, 3))
    with pytest.raises(ValueError, match="explicit boolean"):
        derive_readings({selected: row}, [selected], dict(
            method="characterized_reflectance", context=ratio_context(reference, matched_setup=1),
        ))
    item = derive_readings({selected: row}, [selected], dict(
        method="relight_reflectance", context=relight_context(),
    ))["by_source"][selected]
    assert item["result"] is None and "not radiance" in item["reason"]


def test_actual_saved_ratio_and_relighting_are_separate_authenticated_derived_views(saved):
    folder, _, _, _ = saved
    session = DesktopSession([])
    open_saved(session, folder)
    before = session.dataset()
    reference_key, sample_key = list(before["readings"])[:2]
    context = ratio_context(before["readings"][reference_key]["reading_ref"])
    ratio = derive(session, dict(method="characterized_reflectance", context=context),
                   selected=[sample_key])["by_source"][sample_key]
    assert ratio["reason"] is None
    assert ratio["result"]["source_inputs"]["reference"]["observation"] == \
        before["readings"][reference_key]["observation"]
    relight = derive(session, dict(method="relight_reflectance", context=relight_context(
        reflectance=dict(kind="characterized-reference", context=context),
    )), selected=[sample_key])["by_source"][sample_key]
    assert relight["reason"] is None and relight["result"]["xyz"] is not None
    assert session.dataset() == before


def test_actual_cs_parser_output_qualifies_retained_reference_and_absolute_xyz_without_mutation():
    from color_measurement.serial_protocols import cs_spectrum

    reference = dict(operation_id="cs-reference", observation_id="native-reference",
                     segment_id="11111111-1111-4111-8111-111111111111",
                     source_manifest_sha256="a" * 64)
    sample = dict(operation_id="cs-sample", observation_id="native-sample")
    spectra = []
    for value in ("10", "1"):
        blocks = [",".join([value] * count) for count in (100, 100, 100, 101)]
        if value == "1":
            blocks[0] = "-0.25," + ",".join([value] * 99)
        spectra.append(cs_spectrum(blocks))
    readings = dict(synthetic_spectral_row(
        identity, native.values, wavelengths=native.wavelengths_nm,
        quantity=native.quantity, unit=native.unit,
    ) for identity, native in zip((reference, sample), spectra))
    before = deepcopy(readings)
    key = reading_key(sample)
    factor = derive_readings(readings, [key], dict(
        method="characterized_reflectance", context=ratio_context(reference),
    ))["by_source"][key]
    assert factor["reason"] is None
    assert factor["result"]["spectrum"]["values"] == pytest.approx(
        [-0.025] + [0.1] * 400)
    assert factor["result"]["source_inputs"]["reference"]["reading_ref"] == reference
    absolute = derive_readings(readings, [key], dict(
        method="spectral_xyz", context=spectral_context(),
    ))["by_source"][key]
    assert absolute["reason"] is None and absolute["result"]["unit"] == "cd/m^2"
    assert len(absolute["result"]["values"]) == 3
    assert readings == before


@pytest.mark.parametrize("producer", ["pr0", "cr0"])
def test_retained_unknown_serial_density_cannot_be_promoted_by_retained_reference(producer):
    from color_measurement.serial_protocols import cr_spectrum

    if producer == "pr0":
        # Preserve the original code-0 PR observation, not an executable retired parser.
        native = Spectrum(tuple(float(wave) for wave in range(380, 781, 4)), (1.0,) * 101,
                          "unverified instrument spectral quantity",
                          "unverified PR M5 units code 0")
    else:
        native, _ = cr_spectrum("380,780,4,101", ["1"] * 101, "0,1,2")
    reference = dict(operation_id="unknown-reference", observation_id="reference")
    sample = dict(operation_id="unknown-sample", observation_id="sample")
    readings = dict(synthetic_spectral_row(
        identity, native.values, wavelengths=native.wavelengths_nm,
        quantity=native.quantity, unit=native.unit,
    ) for identity in (reference, sample))
    before = deepcopy(readings)
    key = reading_key(sample)
    for method, context in (("characterized_reflectance", ratio_context(reference)),
                            ("spectral_xyz", spectral_context())):
        reply = derive_readings(readings, [key], dict(method=method, context=context))
        item = reply["by_source"][key]
        assert item["result"] is None and item["reason"]
    assert readings == before


@pytest.mark.parametrize("code,quantity,unit,xyz_unit", [
    (11, "spectral_radiance", "W/(m^2 sr nm)", "cd/m^2"),
    (12, "spectral_irradiance", "W/(m^2 nm)", "lux"),
])
def test_retained_pr_m5_quantities_derive_only_under_their_original_geometry(
    code, quantity, unit, xyz_unit,
):
    # These are the original direct PR observation inputs. Current acquisition uses Argyll.
    native = Spectrum(tuple(float(wave) for wave in range(380, 781, 4)), (1.0,) * 101,
                      quantity, unit)
    metadata = dict(units_code=float(code), peak_wavelength_nm=555.0,
                    instrument_integrated_reading=1.0, integrated_photon_reading=2.0)
    identity = dict(operation_id="pr-radiometric", observation_id="native-sample")
    key, row = synthetic_spectral_row(identity, native.values, wavelengths=native.wavelengths_nm,
                                     quantity=native.quantity, unit=native.unit)
    row["observation"]["configuration"] = [("direct_capture_json", json.dumps(metadata))]
    before = deepcopy(row)
    item = derive_readings({key: row}, [key], dict(
        method="spectral_xyz", context=spectral_context(),
    ))["by_source"][key]
    assert item["reason"] is None and item["result"]["unit"] == xyz_unit
    assert row == before


@pytest.mark.parametrize("mismatch", ["geometry", "unit", "grid"])
def test_cs_retained_reference_keeps_geometry_unit_and_grid_guards(mismatch):
    from color_measurement.serial_protocols import cs_spectrum

    native = cs_spectrum([",".join(["1"] * count) for count in (100, 100, 100, 101)])
    sample = dict(operation_id="cs-mismatch-sample", observation_id="sample")
    reference = dict(operation_id="cs-mismatch-reference", observation_id="reference")
    key, row = synthetic_spectral_row(sample, native.values, wavelengths=native.wavelengths_nm,
                                     quantity=native.quantity, unit=native.unit)
    ref_key, ref_row = synthetic_spectral_row(
        reference, native.values, wavelengths=native.wavelengths_nm,
        quantity=native.quantity, unit=native.unit)
    spectrum = ref_row["observation"]["backend_reported"]["spectrum"]
    if mismatch == "geometry":
        spectrum.update(quantity="spectral_irradiance", unit="W/(m^2 nm)")
    elif mismatch == "unit":
        spectrum["unit"] = "mW/(m^2 sr nm)"
    else:
        spectrum["wavelengths_nm"] = tuple(wave + 1 for wave in native.wavelengths_nm)
    readings = {key: row, ref_key: ref_row}
    before = deepcopy(readings)
    item = derive_readings(readings, [key], dict(
        method="characterized_reflectance", context=ratio_context(reference),
    ))["by_source"][key]
    assert item["result"] is None and "compatible same-grid" in item["reason"]
    assert readings == before
