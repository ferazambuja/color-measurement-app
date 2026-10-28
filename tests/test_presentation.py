"""Numerical bundled presentation DTO tests; no browser, measurement, or appearance proof."""

import base64
import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from color_measurement import presentation as projection


def native_row(values=(20, 30, 40)):
    return {
        "observation": {
            "backend_reported": {
                "xyz": {
                    "values": values,
                    "observer": "CIE1931_2",
                    "unit": "cd/m^2",
                    "scale": "absolute; Y is luminance",
                    "illuminant": "none (emissive)",
                }
            }
        }
    }


def test_measurement_dto_preserves_raw_and_declared_numerical_fields():
    row = native_row()
    before = copy.deepcopy(row)
    result = projection.display_projections({"sample": row}, 100)["sample"]["srgb"]
    status = result["gamut_status"]
    assert status["below_zero"] == [0]
    assert status["numerical_state"] == "outside"
    assert status["max_lower_excess"] > 0
    assert status["max_upper_excess"] == 0
    assert status["fit_positive_rgb_scale"] > status["required_positive_rgb_scale"]
    assert status["chromaticity"]["xyy"]["numerical_state"] == "outside"
    assert row == before


@pytest.mark.parametrize(
    "key,value",
    [
        ("observer", "CIE1964_10"),
        ("unit", "%"),
        ("scale", "relative"),
        ("illuminant", "D50"),
    ],
)
def test_incompatible_measurement_context_stays_unavailable(key, value):
    row = native_row()
    row["observation"]["backend_reported"]["xyz"][key] = value
    result = projection.display_projections({"sample": row}, 100)["sample"]
    assert all(
        value["display_rgb"] is None and value["gamut_status"] is None and value["reason"]
        for value in result.values()
    )


def test_missing_xyz_has_explicit_preview_and_lab_absence_and_cannot_be_a_white():
    missing = native_row()
    missing["observation"]["backend_reported"]["xyz"] = None
    rows = {"available": native_row(), "missing": missing}
    before = copy.deepcopy(rows)
    previews = projection.display_projections(rows, 100)
    assert all(value["display_rgb"] is None and value["gamut_status"] is None
               and "XYZ is unavailable" in value["reason"]
               for value in previews["missing"].values())
    assert all(value["display_rgb"] is not None for value in previews["available"].values())
    white = projection.resolve_white(
        rows, {"kind": "retained-reading", "source_id": "available"}
    )
    values, reasons = projection.lab_projections(rows, white)
    assert set(values) == {"available"}
    assert reasons == {"missing": "Native XYZ is unavailable."}
    with pytest.raises(ValueError, match="no native XYZ"):
        projection.resolve_white(rows, {"kind": "retained-reading", "source_id": "missing"})
    assert rows == before


@pytest.mark.parametrize("context", [
    {"unit": "1", "scale": "relative; perfect-diffuser Y=100", "illuminant": "D50"},
    {"unit": "1", "scale": "relative; perfect-transmission Y=100", "illuminant": "D50"},
    {"unit": "lux", "scale": "absolute; Y is illuminance", "illuminant": "none (ambient)"},
])
def test_reference_models_do_not_borrow_absolute_display_scale_for_other_quantities(context):
    row = native_row()
    row["observation"]["backend_reported"]["xyz"].update(context)
    rows = {"native": row}
    before = copy.deepcopy(rows)
    white = projection.resolve_white(rows, {"kind": "retained-reading", "source_id": "native"})
    assert projection.lab_projections(rows, white)[0]["native"]
    models = projection.reference_projections(100, white, readings=rows)
    for model in models.values():
        assert model["xyz_points"] is model["lab_points"] is None
        assert model["component_reasons"]["xyz"] and model["component_reasons"]["lab"]
        assert model["reference_unit"] == "cd/m^2"
        assert model["reference_scale"] == "absolute; Y is luminance"
        assert model["native_contexts"][0]["unit"] == context["unit"]
        # Same declared observer; the diagram has unit one.
        assert model["primaries"] and model["white_point"]
    assert rows == before


def test_reference_chromaticity_model_refuses_other_observer_without_erasing_native_values():
    row = native_row()
    row["observation"]["backend_reported"]["xyz"]["observer"] = "CIE1964_10"
    before = copy.deepcopy(row)
    models = projection.reference_projections(100, readings={"native": row})
    for model in models.values():
        assert model["xyz_points"] is model["primaries"] is model["white_point"] is None
        assert model["component_reasons"]["chromaticity"]
    assert row == before


def test_reference_model_missing_xyz_is_independent_of_standalone_illustration():
    row = native_row()
    row["observation"]["backend_reported"]["xyz"] = None
    for model in projection.reference_projections(100, readings={"missing": row}).values():
        assert model["xyz_points"] is model["primaries"] is None
        assert model["component_reasons"]["xyz"]
    assert all(model["xyz_points"] and model["primaries"]
               for model in projection.reference_projections(100).values())


def test_no_per_reading_brightness_normalization():
    result = projection.display_projections({"a": native_row(), "b": native_row((40, 60, 80))}, 100)
    for target in projection.TARGETS:
        assert result["a"][target]["reference_luminance"] == 100
        assert result["b"][target]["reference_luminance"] == 100
        assert result["a"][target]["display_rgb"] != result["b"][target]["display_rgb"]


def reflective_row(values=None, *, illuminant="D50"):
    capture = json.loads((Path(__file__).parent /
                          "fixtures/generic_frontend/spectroscan/reflective_spot.spot.json"
                          ).read_bytes())
    observation = capture["result"]["patches"][0]["observation"]
    xyz = observation["backend_reported"]["xyz"]
    if values is not None:
        xyz["values"] = list(values)
    xyz["illuminant"] = illuminant
    return {"observation": observation}


@pytest.mark.parametrize("illuminant", ["D50", "D65", "A"])
def test_relative_reflective_previews_use_one_declared_white_without_native_mutation(illuminant):
    from color_measurement.illumination import illuminant_white

    white = illuminant_white(
        illuminant, observer="CIE1931_2", reference_y=100, unit="1",
        scale="relative; perfect-diffuser Y=100", observer_csv=projection.RESOURCE.read_bytes(),
    )
    rows = {"white": reflective_row(white.xyz, illuminant=illuminant),
            "half": reflective_row(tuple(value / 2 for value in white.xyz), illuminant=illuminant),
            "black": reflective_row((0, 0, 0), illuminant=illuminant)}
    before = copy.deepcopy(rows)
    previews = projection.display_projections(rows, 150)
    for target in projection.TARGETS:
        full, half, black = (previews[key][target] for key in ("white", "half", "black"))
        assert full["display_rgb"] == pytest.approx((1, 1, 1))
        assert half["linear_rgb"] == pytest.approx((.5, .5, .5), abs=1e-12)
        assert black["display_rgb"] == (0, 0, 0)
        for item in (full, half, black):
            assert item["common_reference_y"] == 100 and item["reference_unit"] == "1"
            assert "reference_luminance" not in item
            assert item["reference_scale"] == "relative; perfect-diffuser Y=100"
            assert item["source_white"]["name"] == illuminant
            assert item["source_white"]["reference_y"] == 100
            assert item["native_context"]["illuminant"] == illuminant
            assert item["algorithm"] == "bradford-corresponding-color-display"
            assert item["gamut_status"] is None
    assert rows == before


@pytest.mark.parametrize("field,value", [
    ("observer", "CIE1964_10"), ("unit", "%"), ("scale", "relative"),
    ("illuminant", "unknown"), ("actual_mode", "emissive_spot"),
    ("actual_mode", "transmission_spot"), ("backend", "direct_serial"),
    ("provenance", "replay"),
    ("values", (-1, 25, 10)), ("xyz", None),
])
def test_relative_preview_refuses_unknown_incompatible_or_missing_native_context(field, value):
    row = reflective_row()
    if field == "actual_mode":
        row["observation"][field] = value
    elif field == "backend":
        row["observation"]["device"][field] = value
    elif field == "provenance":
        row["observation"]["device"]["backend"] = "argyll-demo"
        row["observation"][field] = value
    elif field == "xyz":
        row["observation"]["backend_reported"][field] = value
    else:
        row["observation"]["backend_reported"]["xyz"][field] = value
    before = copy.deepcopy(row)
    result = projection.display_projections({"retained": row}, 100)["retained"]
    assert all(item["display_rgb"] is None and item["gamut_status"] is None
               and item["reason"] for item in result.values())
    assert row == before


def test_relative_preview_scale_and_chart_color_use_the_same_common_white_owner():
    from dataclasses import asdict

    from test_chart_targets import CHART_OBSERVER, chart_source

    from color_measurement.chart_science import chart_reading_colors

    source = chart_source(values=(20, 25, 10))
    rows = {"reflective": {"observation": asdict(source["observation"])},
            "emissive": native_row()}
    first, second = (projection.display_projections(rows, scale) for scale in (100, 200))
    assert first["reflective"] == second["reflective"]
    assert first["emissive"] != second["emissive"]
    for target in projection.TARGETS:
        board = chart_reading_colors(source, observer_csv=CHART_OBSERVER, color_space=target)
        ordinary = first["reflective"][target]
        assert ordinary["display_rgb"] == tuple(board["color"]["encoding"]["display_rgb"])
        assert ordinary["linear_rgb"] == tuple(board["color"]["encoding"]["linear_rgb"])
        assert ordinary["source_white"]["xyz"] == tuple(board["context"]["white"]["xyz"])
    source["accepted_context"]["material"]["illuminant"]["certainty"] = "requested"
    refused = chart_reading_colors(source, observer_csv=CHART_OBSERVER)
    assert not refused["available"] and "unestablished" in refused["reason"]


def test_cmf_rows_and_derived_attribution_are_authenticated():
    assert hashlib.sha256(projection.RESOURCE.read_bytes()).hexdigest() == projection.CMF_SHA256
    assert len(projection.cie_cmf_rows()) == 471
    metadata_path = projection.RESOURCE.with_name("CIE_xyz_1931_2deg.csv_metadata.json")
    metadata_bytes = metadata_path.read_bytes()
    receipt = json.loads(metadata_path.with_name("fetch-receipt.json").read_text())
    expected = next(row["sha256"] for row in receipt["files"] if row["file"] == metadata_path.name)
    assert hashlib.sha256(metadata_bytes).hexdigest() == expected
    metadata = json.loads(metadata_bytes)
    assert metadata["identifier"]["identifier"] == "10.25039/CIE.DS.xvudnb9b"
    assert metadata["rightsList"][0]["rightsIdentifier"] == "CC BY-SA 4.0"
    for dto in (
        projection.chromaticity_field(),
        projection.wavelength_guide(),
        projection.spectral_locus(),
    ):
        assert dto["observer"] == "CIE1931_2"
        assert dto["source_doi"] == "10.25039/CIE.DS.xvudnb9b"
        assert dto["resource_sha256"] == projection.CMF_SHA256
        assert dto["license"] == "CC BY-SA 4.0"


def test_changed_resource_is_refused_without_mutating_published_input(tmp_path, monkeypatch):
    fake = tmp_path / "changed.csv"
    fake.write_bytes(projection.RESOURCE.read_bytes() + b" ")
    projection.cie_cmf_rows.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(projection, "RESOURCE", fake)
            with pytest.raises(ValueError, match="bytes changed"):
                projection.cie_cmf_rows()
    finally:
        projection.cie_cmf_rows.cache_clear()


def test_full_locus_retained_and_raster_declares_each_coordinate_view():
    field, locus = projection.chromaticity_field(), projection.spectral_locus()
    assert len(locus["samples"]) == 471
    assert field["authoritative_boundary_step_nm"] == 1
    assert set(field["views"]) == {"xyy", "uvy"}
    for view, raster in field["views"].items():
        assert raster["width"] == raster["height"] == 384
        assert raster["row_order"] == "top-to-bottom; y decreases"
        x0, x1, y0, y1 = raster["extent"]
        assert all(x0 < p[view][0] < x1 and y0 < p[view][1] < y1 for p in locus["samples"])
        assert raster["valid_pixels"] > 10000
        for target, encoded in raster["rgba8"].items():
            raw = base64.b64decode(encoded, validate=True)
            assert len(raw) == 384 * 384 * 4
            assert raw[:4] == b"\0\0\0\0"
            assert sum(a == 255 for a in raw[3::4]) == raster["valid_pixels"]
            assert set(raw[3::4]) == {0, 255}


def test_raster_pixels_match_coordinate_inverse_not_face_interpolation():
    field = projection.chromaticity_field()
    for view, raster in field["views"].items():
        x0, x1, y0, y1 = raster["extent"]
        for target in projection.TARGETS:
            raw = base64.b64decode(raster["rgba8"][target])
            # Independent inverse equations evaluate sparse interior centers.
            for row in range(30, 360, 31):
                for col in range(25, 370, 37):
                    start = (row * 384 + col) * 4
                    pixel = raw[start : start + 4]
                    if pixel[3] == 0:
                        continue
                    a = x0 + (col + 0.5) / 384 * (x1 - x0)
                    b = y1 - (row + 0.5) / 384 * (y1 - y0)
                    xyz = (
                        (a / b, 1.0, (1 - a - b) / b)
                        if view == "xyy"
                        else (9 * a / (4 * b), 1.0, (12 - 3 * a - 20 * b) / (4 * b))
                    )
                    expected = projection.illustrative_targets(xyz)[target]["display_rgb"]
                    assert all(
                        abs(c / 255 - e) <= 0.5 / 255 + 1e-14 for c, e in zip(pixel[:3], expected)
                    )
                    assert max(pixel[:3]) <= 255


def test_normalized_illustration_is_scale_invariant_and_retains_lower_clamp():
    for wavelength in (360, 550, 830):
        xyz = projection.cie_cmf_rows()[wavelength - 360][1:]
        actual = projection.illustrative_targets(xyz)
        scaled = projection.illustrative_targets(tuple(value * 1e-100 for value in xyz))
        for target in projection.TARGETS:
            assert actual[target]["negative_channels"]
            assert all(
                math.isclose(a, b, abs_tol=5e-16, rel_tol=0)
                for a, b in zip(actual[target]["display_rgb"], scaled[target]["display_rgb"])
            )
            assert max(actual[target]["display_rgb"]) <= 1


def test_illustration_caps_actual_positive_channel_luminance_independently():
    # CSS Color 4's rational Y row, independently stated from the primary tables.
    weights = {
        "srgb": (87098 / 409605, 175762 / 245763, 12673 / 175545),
        "display-p3": (35783 / 156275, 247089 / 357200, 198249 / 2500400),
    }
    def decode(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    for xyz in (
        (0.9504559270516716, 1, 1.0890577507598784),
        *(row[1:] for row in projection.cie_cmf_rows()[::17]),
    ):
        for target, result in projection.illustrative_targets(xyz).items():
            linear = tuple(decode(c) for c in result["display_rgb"])
            assert math.fsum(w * c for w, c in zip(weights[target], linear)) <= 0.25 + 3e-15
    field = projection.chromaticity_field()
    assert field["relative_y_cap"] == 0.25
    for raster in field["views"].values():
        for target in projection.TARGETS:
            raw = base64.b64decode(raster["rgba8"][target])
            for offset in range(0, len(raw), 4):
                if raw[offset + 3]:
                    linear = tuple(decode(c / 255) for c in raw[offset : offset + 3])
                    # One RGBA8 rounding step can slightly raise ideal luminance.
                    assert math.fsum(w * c for w, c in zip(weights[target], linear)) < 0.254


def test_wavelength_guide_preserves_common_scale_and_dim_cmf_tails():
    guide = projection.wavelength_guide()
    assert len(guide["samples"]) == 95
    assert guide["reference_scale"] == 1.0
    for sample, row in zip(guide["samples"], projection.cie_cmf_rows()[::5]):
        assert sample["wavelength_nm"] == row[0]
        assert sample["xyz"] == row[1:]
        assert "normalization_scales" not in sample
        for target in projection.TARGETS:
            expected = projection.xyz_to_rgb(row[1:], color_space=target, reference_luminance=1.0)
            assert sample["colors"][target] == expected.display_rgb
            assert sample["negative_channels"][target] == tuple(
                i for i, value in enumerate(expected.linear_rgb) if value < 0
            )
            assert sample["upper_channels"][target] == tuple(
                i for i, value in enumerate(expected.linear_rgb) if value > 1
            )
    assert max(guide["samples"][0]["colors"]["srgb"]) < 0.1
    assert max(guide["samples"][-1]["colors"]["srgb"]) < 0.01
    assert "identification" in guide["scope"].lower()
    assert "no per-band normalization" in guide["recipe"]


def test_reference_projection_does_not_own_field_or_depend_on_its_scale():
    field = projection.chromaticity_field()
    for scale in (1, 100, 120):
        references = projection.reference_projections(scale)
        assert all("color_field" not in value for value in references.values())
        assert projection.chromaticity_field() is field


def test_theoretical_white_has_explicit_scale_without_native_or_preview_mutation():
    readings = {"a": native_row()}
    before = copy.deepcopy(readings)
    preview = projection.display_projections(readings, 120)
    request = {
        "kind": "theoretical-lab-reference",
        "name": "D65",
        "observer": "CIE1931_2",
        "reference_y": 100,
        "unit": "cd/m^2",
        "scale": "absolute; Y is luminance",
    }
    white = projection.resolve_white(readings, request)
    lab, reasons = projection.lab_projections(readings, white)
    assert "a" in lab and not reasons
    assert white["xyz"] == pytest.approx((95.04559270516715, 100, 108.90577507598785))
    assert white["kind"] == "theoretical-lab-reference" and "observation_id" not in white
    assert readings == before
    assert projection.display_projections(readings, 120) == preview
    changed = projection.resolve_white(readings, {**request, "reference_y": 200})
    assert changed["xyz"] == pytest.approx(tuple(2 * v for v in white["xyz"]))
    assert projection.lab_projections(readings, changed)[0] != lab


def test_retained_white_conditions_and_theoretical_white_context_are_distinct():
    rows = {"a": native_row(), "b": native_row()}
    rows["b"]["observation"]["backend_reported"]["xyz"]["illuminant"] = "D50"
    retained = projection.resolve_white(rows, {"kind": "retained-reading", "source_id": "a"})
    values, reasons = projection.lab_projections(rows, retained)
    assert set(values) == {"a"} and set(reasons) == {"b"}
    request = {
        "kind": "theoretical-lab-reference",
        "name": "D50",
        "observer": "CIE1931_2",
        "reference_y": 100,
        "unit": "cd/m^2",
        "scale": "absolute; Y is luminance",
    }
    theoretical = projection.resolve_white(rows, request)
    assert "illuminant" not in theoretical
    assert set(projection.lab_projections(rows, theoretical)[0]) == {"a", "b"}
    rows["b"]["observation"]["backend_reported"]["xyz"]["observer"] = "CIE1964_10"
    assert set(projection.lab_projections(rows, theoretical)[1]) == {"b"}


@pytest.mark.parametrize(
    "white_request",
    [
        "D65",
        {},
        {"kind": "retained-reading", "source_id": "missing"},
        {"kind": "theoretical-lab-reference", "name": "D65"},
        {"kind": "legacy", "source_id": "a"},
    ],
)
def test_invalid_or_obsolete_white_declaration_is_refused(white_request):
    with pytest.raises(ValueError):
        projection.resolve_white({"a": native_row()}, white_request)


def test_reference_surface_colors_are_vertex_projections_and_lab_has_no_adaptation():
    d50 = projection.standard_reference_white(
        "D50",
        observer="CIE1931_2",
        reference_y=100,
        unit="cd/m^2",
        scale="absolute; Y is luminance",
    )
    from dataclasses import asdict

    refs = projection.reference_projections(100, asdict(d50))
    for model in refs.values():
        assert "colored_faces" not in model
        assert model["white"] == "D65 target"
        for i in (0, 100, 385):
            assert model["lab_points"][i] == projection.xyz_to_lab(model["xyz_points"][i], d50.xyz)
            for target in projection.TARGETS:
                assert (
                    model["vertex_colors"][target][i]
                    == projection.encoded_targets(model["xyz_points"][i], 100)[target]
                )


def test_reference_lab_refuses_wrong_white_context_without_losing_xyz():
    request = {
        "kind": "theoretical-lab-reference",
        "name": "D65",
        "observer": "CIE1931_2",
        "reference_y": 100,
        "unit": "1",
        "scale": "relative",
    }
    descriptor = projection.resolve_white({"a": native_row()}, request)
    refs = projection.reference_projections(100, descriptor)
    for target, model in refs.items():
        assert model["lab_points"] is None
        assert "units/scale/observer" in model["component_reasons"]["lab"]
        assert model["xyz_points"] == projection.reference_projections(100)[target]["xyz_points"]
        assert len(model["lines"]) == 12 * 8


@pytest.mark.parametrize("color_space", ["srgb", "display-p3"])
@pytest.mark.parametrize("white", [(96.42, 100, 82.51), (95.05, 100, 108.88)])
def test_corresponding_color_common_white_scale_black_and_signed_channels(color_space, white):
    params = dict(reference_white=white, observer="CIE1931_2", color_space=color_space)
    result = projection.corresponding_color(white, **params)
    assert result.linear_rgb == pytest.approx((1, 1, 1), abs=2e-14)
    assert result.display_rgb == pytest.approx((1, 1, 1), abs=2e-14)
    assert result.clipped_channels == (False, False, False)
    assert result.algorithm_version == "2"
    raw = projection.xyz_to_rgb(
        result.adapted_xyz, color_space=color_space, reference_luminance=white[1],
    )
    assert result.linear_rgb == raw.linear_rgb and result.display_rgb == raw.display_rgb
    assert raw.clipped_channels == tuple(value < 0 or value > 1 for value in raw.linear_rgb)
    assert result.common_reference_y == 100 and result.source_white == white
    assert projection.corresponding_color((0, 0, 0), **params).display_rgb == (0, 0, 0)
    half = projection.corresponding_color(tuple(v / 2 for v in white), **params)
    assert half.linear_rgb == pytest.approx((0.5,) * 3, abs=2e-14)
    assert all(v < 1 for v in half.display_rgb)
    signed = projection.corresponding_color((-20, 30, 40), **params)
    assert any(v < 0 for v in signed.linear_rgb) and any(signed.clipped_channels)
    above = projection.corresponding_color(tuple(v * (1 + 1e-10) for v in white), **params)
    assert above.clipped_channels == (True, True, True)
    near = projection.corresponding_color(tuple(math.nextafter(v, math.inf) for v in white),
                                          **params)
    assert near.clipped_channels == tuple(value < 0 or value > 1 for value in near.linear_rgb)
    assert result.source_white == white


def test_corresponding_color_white_exception_keeps_large_and_negative_excursion_flags(monkeypatch):
    from color_measurement.science import RGBEncoding

    white = (95.05, 100, 108.88)
    # An encoder witness isolates the declared diagnostic bound. The scientific
    # encoder's real arithmetic and strict masks are checked by the cases above.
    encoded = RGBEncoding("srgb", (1 + 16 * math.ulp(1.0), 1 + 17 * math.ulp(1.0), -1e-16),
                          (1, 1, 0), (True, True, True))
    monkeypatch.setattr(projection, "xyz_to_rgb", lambda *args, **kwargs: encoded)
    result = projection.corresponding_color(
        white, reference_white=white, observer="CIE1931_2", color_space="srgb",
    )
    assert result.clipped_channels == (False, True, True)
    assert result.linear_rgb == encoded.linear_rgb and result.display_rgb == encoded.display_rgb


def test_corresponding_color_observer_white_and_target_refusals():
    from color_measurement.science import ColorDomainError

    params = dict(reference_white=(1, 1, 1), observer="CIE1931_2", color_space="display-p3")
    for replacement in (
        {"observer": "CIE1964_10"},
        {"reference_white": (0, 1, 1)},
        {"color_space": "unknown"},
    ):
        with pytest.raises(ColorDomainError):
            projection.corresponding_color((1, 1, 1), **(params | replacement))
