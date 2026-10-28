"""Native-selected typed spectra over actual saved inputs; no transport or Qt loop."""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_analysis_exports import export_params
from test_dataset_binding import open_saved
from test_dataset_binding import saved as _saved_fixture
from test_desktop_session import query
from test_qualified_science import ratio_context, relight_context

from color_measurement import desktop_session
from color_measurement.desktop import _choose_science_material
from color_measurement.desktop_session import DesktopSession

saved = _saved_fixture


@pytest.fixture
def materials(saved):
    session = DesktopSession([])
    open_saved(session, saved[0])
    data = session.dataset()
    scope = dict(binding_id=data["binding_id"], generation=data["generation"],
                 dataset_revision=data["dataset_revision"])
    return session, data, scope


def material_file(tmp_path, data, role):
    spectrum = next(iter(data["readings"].values()))["observation"]["backend_reported"]["spectrum"]
    document = dict(wavelengths_nm=list(spectrum["wavelengths_nm"]),
                    values=[1.0 if role == "illuminant" else 0.8] * len(spectrum["values"]),
                    quantity="relative_spectral_power" if role == "illuminant" else "reflectance",
                    unit="1")
    path = tmp_path / (role + ".json")
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


def request(data, *, illuminant=None, characterization=None):
    reference = next(iter(data["readings"].values()))["reading_ref"]
    context = ratio_context(reference, characterization=characterization,
                            assume_ideal_white=characterization is None)
    return dict(method="relight_reflectance", context=relight_context(
        reflectance=dict(kind="characterized-reference", context=context),
        illuminant=illuminant if illuminant is not None else "D65",
    ))


def derive(session, data, derivation, **changes):
    return query(session, "derive", dict(dataset_revision=data["dataset_revision"],
                                        selected=list(data["readings"])[1:2],
                                        derivation=derivation) | changes)


@pytest.mark.parametrize("accepted", [False, True])
def test_native_material_chooser_returns_only_selected_json_or_cancellation(accepted, tmp_path):
    path = tmp_path / "selected-spectrum.json"
    settings = []
    def file_path(title, **options):
        settings.append((title, options))
        return path if accepted else None
    ui = SimpleNamespace(file_path=file_path)
    assert _choose_science_material(ui, "illuminant") == (path if accepted else None)
    assert settings == [("Choose a declared illuminant spectrum JSON file",
                         {"extensions": ["json"]})]
    with pytest.raises(ValueError, match="illuminant or characterization"):
        _choose_science_material(SimpleNamespace(), "unknown")


def test_captured_tokens_resolve_both_derive_and_real_export_without_changing_sources(
    materials, tmp_path,
):
    session, data, scope = materials
    choices, documents = {}, {}
    for role in ("illuminant", "characterization"):
        path, document = material_file(tmp_path, data, role)
        choices[role] = session.stage_science_material(path, **scope, role=role)
        documents[role] = document
        assert set(choices[role]) == {"token", "source_name", "sha256", "identity", "role"}
        assert choices[role]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    declaration = request(data, **{role: {"token": choice["token"]}
                                  for role, choice in choices.items()})
    before = deepcopy(declaration)
    answer = derive(session, data, declaration)
    context = answer["request"]["context"]
    assert json.loads(json.dumps(context["illuminant"])) == documents["illuminant"]
    reference = context["reflectance"]["context"]
    assert json.loads(json.dumps(reference["characterization"])) == documents["characterization"]
    assert context["illuminant_id"] == choices["illuminant"]["identity"]
    assert reference["characterization_id"] == choices["characterization"]["identity"]
    output, = answer["by_source"].values()
    assert output["reason"] is None and output["result"]["xyz"] is not None
    path = tmp_path / "analysis.json"
    session.export_analysis(path, **export_params(session, selected=list(answer["by_source"])),
                            derivation=declaration)
    exported = json.loads(path.read_bytes())["derived_science"]
    assert exported["context_sha256"] == answer["context_sha256"]
    assert exported["request"] == json.loads(json.dumps(answer["request"]))
    assert declaration == before and session.dataset() == data
    # Rebinding the same scientific dataset keeps captured data reusable; RPC
    # calls still require the new renderer generation.
    session._binding.bind()
    assert derive(session, data, declaration)["context_sha256"] == answer["context_sha256"]


@pytest.mark.parametrize("fault", ["extra", "duplicate", "nonfinite", "quantity", "unit", "size"])
def test_material_parser_refuses_invalid_current_shapes_without_retaining_tokens(
    materials, tmp_path, fault,
):
    session, data, scope = materials
    path, document = material_file(tmp_path, data, "illuminant")
    if fault == "extra":
        document["filename"] = "untrusted path"
    elif fault == "quantity":
        document["quantity"] = "spectral_radiance"
    elif fault == "unit":
        document["unit"] = "%"
    elif fault == "nonfinite":
        document["values"][0] = float("nan")
    text = json.dumps(document)
    if fault == "duplicate":
        text = '{"unit":"1",' + text[1:]
    elif fault == "size":
        text += " " * desktop_session._SCIENCE_MATERIAL_BYTES
    path.write_text(text)
    with pytest.raises(ValueError):
        session.stage_science_material(path, **scope, role="illuminant")
    assert session._science_materials == {} and session.dataset() == data


@pytest.mark.parametrize("fault", ["bytes", "inode", "role", "token", "identity", "token_fields"])
def test_material_use_refuses_changed_source_or_forged_token_before_export(
    materials, tmp_path, fault,
):
    session, data, scope = materials
    path, _ = material_file(tmp_path, data, "illuminant")
    choice = session.stage_science_material(path, **scope, role="illuminant")
    declaration = request(data, illuminant={"token": choice["token"]})
    if fault == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif fault == "inode":
        raw = path.read_bytes()
        path.rename(tmp_path / "previous.json")
        path.write_bytes(raw)
    elif fault == "role":
        declaration = dict(method="characterized_reflectance", context=ratio_context(
            next(iter(data["readings"].values()))["reading_ref"],
            characterization={"token": choice["token"]}, assume_ideal_white=False,
        ))
    elif fault == "token":
        declaration["context"]["illuminant"]["token"] = "unknown"
    elif fault == "identity":
        declaration["context"]["illuminant_id"] = "Forged captured source"
    else:
        declaration["context"]["illuminant"]["path"] = str(path)
    with pytest.raises(ValueError):
        derive(session, data, declaration)
    output = tmp_path / "refused.json"
    with pytest.raises(ValueError):
        session.export_analysis(output, **export_params(session), derivation=declaration)
    assert not output.exists() and session.dataset() == data


def test_stale_selection_and_dataset_tokens_are_refused_before_file_use(
    materials, tmp_path, monkeypatch,
):
    session, data, scope = materials
    path, _ = material_file(tmp_path, data, "illuminant")
    choice = session.stage_science_material(path, **scope, role="illuminant")
    with monkeypatch.context() as guard:
        guard.setattr(desktop_session, "_read_regular",
                      lambda *args, **kwargs: pytest.fail("Stale read"))
        for changes in ({"generation": scope["generation"] + 1},
                        {"dataset_revision": "old revision"}, {"binding_id": "old binding"}):
            with pytest.raises(ValueError):
                session.stage_science_material(path, **(scope | changes), role="illuminant")
        with pytest.raises(ValueError, match="revision changed"):
            derive(session, data, request(data, illuminant={"token": choice["token"]}),
                   dataset_revision="old revision")
    session.activate_live(expected_binding_id=scope["binding_id"],
                          expected_generation=scope["generation"])
    current = session.dataset()
    with pytest.raises(ValueError, match="another dataset revision"):
        derive(session, current, request(data, illuminant={"token": choice["token"]}))


def test_valid_material_capture_defers_grid_refusal_to_numerical_owner(materials, tmp_path):
    session, data, scope = materials
    path = tmp_path / "different-grid.json"
    path.write_text(json.dumps(dict(wavelengths_nm=[380, 390], values=[1, 1],
                                   quantity="relative_spectral_power", unit="1")))
    choice = session.stage_science_material(path, **scope, role="illuminant")
    output, = derive(session, data, request(data, illuminant={"token": choice["token"]}))[
        "by_source"].values()
    assert output["reason"] is None
    assert output["result"]["reference_factor"] is not None
    assert output["result"]["xyz"] is None and "exact grid" in output["result"]["xyz_reason"]
