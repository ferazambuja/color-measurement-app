"""Retained-data TI3 quantities, grouping, coverage and owned publication."""

import base64
import copy
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from color_measurement.cgats import REAL, read_cgats
from color_measurement.chart_targets import import_ti2_json
from color_measurement.exports import _folder_lock, _read_regular
from color_measurement.ti3_exports import update_json


def row(identity="original-1", *, provenance="simulated", xyz=True, spectrum=True):
    return dict(
        id=identity,
        name='Name "測定"\nline',
        notes="annotation\r\n☀",
        provenance=provenance,
        kind="color",
        xyz=dict(
            values=[-2, 0, 101.5],
            unit="1",
            scale="relative; perfect-diffuser Y=100",
            observer="CIE1931_2",
            illuminant="D50",
            origin="native",
            method="native XYZ",
        )
        if xyz
        else None,
        spectrum=dict(
            wavelengths_nm=[400.5, 410.5, 420.5, 430.5],
            values=[-1, 0, 101, 50],
            quantity="reflectance",
            unit="%",
        )
        if spectrum
        else None,
        context=dict(
            route="demo",
            material={"mode": "reflective_spot_m2"},
            provenance={"raw": "response", "timestamp": "old"},
        ),
    )


def snapshot(rows=None, charts=None):
    rows = [row()] if rows is None else rows
    return dict(
        schema_version=3,
        owner_id="stable-segment",
        source_id="authoritative-source",
        source_sha256="1" * 64,
        records=rows,
        ordinary_record_ids=[item["id"] for item in rows],
        charts=charts or [],
    )


def chart(ids=("original-1",), locations=("A1",), provenance="simulated"):
    return dict(
        id="stable-pass",
        provenance=provenance,
        total_cells=24,
        target=None,
        rows=[
            dict(record_id=i, sample_id=f"patch-{n}", sample_loc=loc)
            for n, (i, loc) in enumerate(zip(ids, locations))
        ],
    )


def update(value, folder, previous=None):
    return json.loads(update_json(json.dumps(value), str(folder), json.dumps(previous)))


def table(file):
    (value,) = read_cgats(Path(file["path"]).read_text())
    return value


def test_current_ordinary_subset_preserves_order_and_inherited_chart_identity(tmp_path):
    first, inherited, last = (row(f"segment-{segment}:1") for segment in ("A", "B", "C"))
    value = snapshot([first, inherited, last], [chart((inherited["id"],), ("A1",))])
    value["ordinary_record_ids"] = [last["id"], first["id"]]
    original = copy.deepcopy(value)
    result = update(value, tmp_path)
    ordinary, = [f for f in result["files"] if f["scope"] == "ordinary"]
    selected, = [f for f in result["files"] if f["scope"] == "chart_reference"]
    assert ordinary["row_ids"] == [last["id"], first["id"]]
    parsed = table(ordinary)
    assert [json.loads(r[parsed.index("CM_RECORD_ID")]) for r in parsed.rows] == [
        last["id"], first["id"]
    ]
    assert selected["row_ids"] == [inherited["id"]]
    assert value == original


@pytest.mark.parametrize("ids", [None, "original-1", {}, [1], [True], [None], [" "],
                                 ["missing"], ["original-1", "original-1"]])
def test_malformed_ordinary_subset_refuses_before_publication(tmp_path, ids):
    value = snapshot()
    value["ordinary_record_ids"] = ids
    with pytest.raises(ValueError):
        update(value, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("defect", ["missing-selection", "old-schema", "invalid-inherited"])
def test_ordinary_exclusion_never_bypasses_current_schema_or_record_validation(tmp_path, defect):
    value = snapshot()
    value["ordinary_record_ids"] = []
    if defect == "missing-selection":
        del value["ordinary_record_ids"]
    elif defect == "old-schema":
        value["schema_version"] = 2
    else:
        value["records"][0]["xyz"]["values"][0] = True
    with pytest.raises(ValueError):
        update(value, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_chart_only_inherited_target_rows_export_without_duplicate_ordinary_reads(tmp_path):
    value, _ = imported_chart(tmp_path, 2)
    value["ordinary_record_ids"] = []
    result = update(value, tmp_path)
    assert result["status"] == "current"
    file, = result["files"]
    assert file["scope"] == "chart_target" and file["row_count"] == 2
    assert table(file).keywords["DEVICE_CLASS"] == "OUTPUT"
    assert update(value, tmp_path, result) == result


def test_current_unsupported_read_is_accounted_beside_inherited_chart(tmp_path):
    current = row("new-segment:1", xyz=False, spectrum=False)
    current["kind"] = "scalar"
    value = snapshot([row(), current], [chart()])
    value["ordinary_record_ids"] = [current["id"]]
    result = update(value, tmp_path)
    assert result["status"] == "current"
    file, = result["files"]
    assert file["scope"] == "chart_reference" and file["row_ids"] == ["original-1"]
    assert result["unavailable"] == [dict(
        record_id=current["id"], component="record", reason="CTI3 cannot represent scalar outcomes"
    )]


def test_inherited_history_cannot_pool_incompatible_pass_contexts(tmp_path):
    inherited, current = row("old-segment:1"), row("new-segment:1")
    current["context"]["material"]["filter"] = "changed optical filter"
    value = snapshot([inherited, current], [chart(
        (inherited["id"], current["id"]), ("A1", "A2")
    )])
    value["ordinary_record_ids"] = [current["id"]]
    exported = update(value, tmp_path)
    ordinary, = [file for file in exported["files"] if file["scope"] == "ordinary"]
    selected, = [file for file in exported["files"] if file["scope"] == "chart_reference"]
    assert ordinary["state"] == "current" and ordinary["row_ids"] == [current["id"]]
    assert selected["state"] == "unavailable" and "incompatible" in selected["reason"]
    assert not Path(selected["path"]).exists()
    # A new pass is independently eligible; inherited history still resolves.
    value["charts"].append(dict(chart((current["id"],), ("A2",)), id="new-pass"))
    revised = update(value, tmp_path, exported)
    assert next(f for f in revised["files"] if f["chart_id"] == "new-pass")["state"] == "current"


def test_values_real_types_identity_annotations_and_provenance(tmp_path):
    source = snapshot()
    result = update(source, tmp_path)
    assert result["status"] == "current" and result["unavailable"] == []
    (file,) = result["files"]
    parsed = table(file)
    assert "measurements-simulated-" in Path(file["path"]).name
    assert file["provenance"] == parsed.keywords["CM_PROVENANCE"] == "simulated"
    assert parsed.identifier == "CTI3" and parsed.keywords["DEVICE_CLASS"] == "INPUT"
    assert "COLOR_REP" not in parsed.keywords
    assert parsed.reals("XYZ_Y") == (0.0,)
    assert parsed.reals("XYZ_X") == (-2.0,)
    assert parsed.reals("SPEC_411") == (0.0,)
    assert all(parsed.field_types[f] == REAL for f in parsed.fields if f.startswith("SPEC_"))
    for field, original in (
        ("CM_RECORD_ID", "id"),
        ("CM_NAME_JSON", "name"),
        ("CM_NOTES_JSON", "notes"),
        ("CM_CONTEXT_JSON", "context"),
    ):
        assert json.loads(parsed.rows[0][parsed.index(field)]) == source["records"][0][original]
    assert file["payload_sha256"] == hashlib.sha256(Path(file["path"]).read_bytes()).hexdigest()


def test_exchange_source_identity_is_ascii_opaque_and_authenticates_retries(tmp_path):
    source = snapshot()
    source["source_id"] = "/Users/private-account/測定/session.mat"
    first = update(source, tmp_path)
    file, = first["files"]
    path = Path(file["path"])
    expected = hashlib.sha256(source["source_id"].encode("utf-8")).hexdigest()
    assert table(file).keywords["CM_SOURCE_ID"] == expected
    assert first["source_id"] == source["source_id"]
    assert source["source_id"].encode() not in path.read_bytes()
    assert path.read_bytes().isascii()
    assert update(source, tmp_path, first) == first
    source["records"].append(row("second"))
    source["ordinary_record_ids"].append("second")
    second = update(source, tmp_path, first)
    assert second["status"] == "current" and not second["retryable"]
    assert second["files"][0]["path"] == file["path"]
    # Matching a receipt's payload digest cannot authorize another source header.
    for wrong in ("0" * 64, hashlib.sha256(b"other-source").hexdigest(), source["source_id"]):
        bad = path.read_bytes().replace(expected.encode(), wrong.encode())
        path.write_bytes(bad)
        previous = copy.deepcopy(second)
        previous["files"][0]["payload_sha256"] = hashlib.sha256(bad).hexdigest()
        source["records"][0]["xyz"]["values"][0] += 1
        refused = update(source, tmp_path, previous)
        assert refused["status"] == "stale" and not refused["retryable"]
        assert refused["files"][0]["payload_sha256"] is None
        assert "unconfirmed" in refused["files"][0]["reason"]
        assert path.read_bytes() == bad
        path.write_bytes(bad.replace(wrong.encode(), expected.encode()))


@pytest.mark.parametrize("matching_edited_digest", [False, True])
def test_nested_untrusted_record_identity_refuses_edited_companion_without_escape(
    tmp_path, matching_edited_digest
):
    source = snapshot()
    previous = update(source, tmp_path)
    file, = previous["files"]
    path = Path(file["path"])
    nested = '"' + "[" * 100000 + "0" + "]" * 100000 + '"'
    changed = path.read_text().replace('"""original-1"""', nested)
    assert changed != path.read_text()
    path.write_text(changed)
    if matching_edited_digest:
        previous["files"][0]["payload_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    source["records"][0]["xyz"]["values"][0] += 1
    refused = update(source, tmp_path, previous)
    assert refused["status"] == "stale" and not refused["retryable"]
    assert refused["files"][0]["payload_sha256"] is None
    reason = refused["files"][0]["reason"]
    if matching_edited_digest:
        assert "nesting" in reason or "retained row identities differ" in reason
    else:
        assert "bytes changed" in reason
    assert path.read_text() == changed


def test_authentic_cleartext_source_header_requires_new_destination(tmp_path):
    # Authentic output and receipt from the digest-bound prior writer are inert inputs.
    # Ownership and payload bytes stay intact; no Git history or old code runs.
    fixtures = Path(__file__).parent / "fixtures/ti3"
    retained = json.loads((fixtures / "original-cleartext-source.json").read_text())
    assert retained["producer_source_sha256"] == (
        "af979a051732a1c08f5f937f2a0cbdccbb363ad8b1c8dd7a24ce12d63a2ebfca"
    )
    source, previous = retained["snapshot"], retained["receipt"]
    current = snapshot()
    current.pop("ordinary_record_ids")
    current["schema_version"] = 2
    assert source == current
    # Keep historical source/receipt bytes as evidence; construct current input.
    source = snapshot(source["records"])
    # The historical receipt is inert evidence. Supply the current protocol's
    # required recovery decision; the authentic payload/ownership stay intact.
    previous["relocatable"] = False
    file, = previous["files"]
    path = tmp_path / file["path"]
    path.write_bytes((fixtures / file["path"]).read_bytes())
    file["path"] = str(path.resolve())
    original = path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == (
        "a8551d2712d12f790a2a4732abe8b7a72ce017650e0182bebcd93c739f1a0bb0"
    )
    assert table(file).keywords["CM_SOURCE_ID"] == source["source_id"]
    assert hashlib.sha256(original).hexdigest() == file["payload_sha256"]
    for _ in range(2):
        refused = update(source, tmp_path, previous)
        assert refused["status"] == "stale" and not refused["retryable"]
        assert refused["relocatable"] is True
        assert refused["files"][0]["payload_sha256"] is None
        assert "old cleartext CM_SOURCE_ID" in refused["files"][0]["reason"]
        assert "new destination" in refused["files"][0]["reason"]
        assert path.read_bytes() == original
    current = update(source, tmp_path / "new-destination")
    assert current["status"] == "current" and not current["retryable"]
    assert table(current["files"][0]).keywords["CM_SOURCE_ID"] == hashlib.sha256(
        source["source_id"].encode()
    ).hexdigest()
    assert path.read_bytes() == original


def test_first_save_creates_missing_sibling_folder_and_retry_reuses_publication(tmp_path):
    source = tmp_path / "session.mat"
    source.write_bytes(b"retained canonical source")
    folder = source.with_name(f"{source.stem}-ti3")
    value = snapshot()
    first = update(value, folder)
    assert first["status"] == "current"
    (file,) = first["files"]
    assert file["row_ids"] == ["original-1"]
    payload = Path(file["path"]).read_bytes()
    assert (folder / ".ti3-export.lock").is_file()
    assert update(value, folder, first) == first
    assert Path(file["path"]).read_bytes() == payload
    assert source.read_bytes() == b"retained canonical source"


def test_companion_folder_refuses_existing_non_directory(tmp_path):
    folder = tmp_path / "session-ti3"
    folder.write_bytes(b"unrelated existing file")
    result = update(snapshot(), folder)
    assert result["status"] == "stale"
    assert all(file["state"] == "stale" for file in result["files"])
    assert folder.read_bytes() == b"unrelated existing file"


def test_incidental_context_does_not_split_material_group_or_acquired_order(tmp_path):
    first, second = row("first"), row("second")
    second["context"].update(route="direct", provenance={"timestamp": "new", "port": "COM2"})
    second.update(name="renamed", notes="changed")
    result = update(snapshot([second, first]), tmp_path)
    (file,) = result["files"]
    assert file["row_ids"] == ["second", "first"]
    assert file["row_count"] == 2
    second["context"]["material"]["filter"] = "M3"
    split = update(snapshot([second, first]), tmp_path / "absent")
    assert len(split["files"]) == 2  # Independent typed material contexts.


@pytest.mark.parametrize("provenance", ["physical", "simulated", "replay"])
def test_provenance_separate_file_names_and_headers(tmp_path, provenance):
    result = update(snapshot([row(provenance=provenance)]), tmp_path)
    (file,) = result["files"]
    assert f"measurements-{provenance}-" in file["path"]
    assert table(file).keywords["CM_PROVENANCE"] == provenance


@pytest.mark.parametrize(
    "xyz_patch,spectrum_patch,reason",
    [
        ({"unit": "lux", "scale": "absolute"}, {"quantity": "irradiance"}, "no qualified"),
        ({"unit": "cd s/m^2", "scale": "flash"}, {"quantity": "radiant_exposure"}, "no qualified"),
        ({"observer": "CIE1964_10"}, {"quantity": "unknown"}, "observer"),
        ({"values": [2**60 + 1, 0, 1]}, {"quantity": "unknown"}, "representable"),
    ],
)
def test_unsupported_components_do_not_turn_into_other_quantities(
    tmp_path, xyz_patch, spectrum_patch, reason
):
    value = row()
    value["xyz"].update(xyz_patch)
    value["spectrum"].update(spectrum_patch)
    result = update(snapshot([value]), tmp_path)
    assert result["status"] == "unavailable" and result["files"] == []
    assert any(reason in d["reason"] for d in result["unavailable"])
    assert list(tmp_path.iterdir()) == []


def test_confirmed_w_density_converts_only_spectral_component(tmp_path):
    value = row()
    value["xyz"]["unit"] = "unknown"
    value["spectrum"].update(
        quantity="spectral_radiance", unit="W/(sr m2 nm)", values=[0.1, 0, -0.25, 0.5]
    )
    value["context"]["material"]["spectralUnitsConfirmed"] = True
    result = update(snapshot([value]), tmp_path)
    (file,) = result["files"]
    parsed = table(file)
    assert parsed.keywords["DEVICE_CLASS"] == "EMISINPUT"
    assert parsed.keywords["NORMALIZED_TO_Y_100"] == "NO"
    assert parsed.keywords["CM_SPECTRAL_UNIT"] == "mW/(m^2 sr nm)"
    assert parsed.reals("SPEC_401") == (100.0,)
    assert parsed.reals("SPEC_421") == (-250.0,)
    assert parsed.index("XYZ_X") == -1
    assert result["unavailable"][0]["component"] == "xyz"


@pytest.mark.parametrize(
    "grid,values,reason",
    [
        ([400], [1], "2..601"),
        ([400, 402, 410], [1, 2, 3], "irregular"),
        ([400, 400.1], [1, 2], "collide"),
        ([400, 405], [2**60 + 1, 2], "representable"),
        ([400, 2**31], [1, 2], "integer range"),
        (list(range(300, 902)), [50] * 602, "2..601"),
    ],
)
def test_spectral_refusals_leave_good_xyz_available(tmp_path, grid, values, reason):
    value = row()
    value["spectrum"].update(wavelengths_nm=grid, values=values)
    result = update(snapshot([value]), tmp_path)
    (file,) = result["files"]
    assert result["status"] == "current"
    assert not any(f.startswith("SPEC_") for f in table(file).fields)
    assert any(reason in d["reason"] for d in result["unavailable"])


def test_normalization_and_conversion_overflow_are_explicit(tmp_path):
    value = row()
    value["context"]["material"] = {"configuration": {"spectrum_norm": "1"}}
    result = update(snapshot([value]), tmp_path)
    assert any("normalization" in d["reason"] for d in result["unavailable"])
    value["context"]["material"] = {}
    value["spectrum"].update(quantity="radiance", unit="W/(m^2 sr nm)", values=[1e308, 0, 0, 0])
    result = update(snapshot([value]), tmp_path)
    assert any("conversion" in d["reason"] for d in result["unavailable"])


def test_ordinary_export_has_no_chart_size_limit(tmp_path):
    result = update(snapshot([row(f"original-{n}") for n in range(175)]), tmp_path)
    (file,) = result["files"]
    assert file["row_count"] == 175


def test_chart_selected_order_partial_coverage_and_ordinary_unselected_reads(tmp_path):
    result = update(
        snapshot([row("a"), row("retry"), row("b")], [chart(("b", "a"), ("B1", "A1"))]), tmp_path
    )
    ordinary = next(f for f in result["files"] if f["scope"] == "ordinary")
    selected = next(f for f in result["files"] if f["scope"] == "chart_reference")
    assert ordinary["row_ids"] == ["a", "retry", "b"]
    assert selected["row_ids"] == ["b", "a"]
    assert selected["chart_id"] == "stable-pass" and selected["total_rows"] == 24
    parsed = table(selected)
    assert parsed.keywords["CM_COVERAGE"] == "partial"
    assert tuple(r[parsed.index("SAMPLE_LOC")] for r in parsed.rows) == ("B1", "A1")


def test_one_ineligible_chart_selection_withholds_whole_chart(tmp_path):
    first, second = row("a"), row("b")
    second["xyz"]["illuminant"] = "D65"
    result = update(snapshot([first, second], [chart(("a", "b"), ("A1", "A2"))]), tmp_path)
    selected = next(f for f in result["files"] if f["scope"] == "chart_reference")
    assert selected["state"] == "unavailable" and not Path(selected["path"]).exists()
    assert any(f["state"] == "current" for f in result["files"] if f["scope"] == "ordinary")


def test_chart_mixed_spectrum_keeps_xyz_and_records_reason(tmp_path):
    first, second = row("a"), row("b", spectrum=False)
    result = update(snapshot([first, second], [chart(("a", "b"), ("A1", "A2"))]), tmp_path)
    selected = next(f for f in result["files"] if f["scope"] == "chart_reference")
    assert selected["state"] == "current"
    assert not any(f.startswith("SPEC_") for f in table(selected).fields)
    assert any("Chart reference is XYZ-only" in d["reason"] for d in result["unavailable"])


@pytest.mark.parametrize("locations", [("A1", "A01"), ("Dmin", "GS00"), ("Dmax", "GS23")])
def test_normalized_chart_location_collisions_refuse_before_publication(tmp_path, locations):
    with pytest.raises(ValueError, match="normalization"):
        update(snapshot([row("a"), row("b")], [chart(("a", "b"), locations)]), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_strict_json_and_boolean_number_validation_before_writes(tmp_path):
    with pytest.raises(ValueError):
        update_json('{"schema_version":1,"schema_version":1}', str(tmp_path))
    value = snapshot()
    value["records"][0]["xyz"]["values"][0] = True
    with pytest.raises(ValueError, match="not booleans"):
        update(value, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_owned_atomic_update_and_failed_update_retain_actual_last_good(tmp_path, monkeypatch):
    from color_measurement import exports

    original = snapshot()
    result = update(original, tmp_path)
    (file,) = result["files"]
    prior = Path(file["path"]).read_bytes()
    revised = copy.deepcopy(original)
    revised["source_sha256"] = "2" * 64
    revised["records"].append(row("second"))
    revised["ordinary_record_ids"].append("second")

    def fail(*args):
        raise OSError("injected rename failure")

    with monkeypatch.context() as context:
        context.setattr(exports, "_replace_file", fail)
        stale = update(revised, tmp_path, result)
    (old,) = stale["files"]
    assert stale["status"] == old["state"] == "stale"
    assert old["row_ids"] == ["original-1"] and old["row_count"] == 1
    assert old["snapshot_id"] == file["snapshot_id"]
    assert Path(old["path"]).read_bytes() == prior
    current = update(revised, tmp_path, stale)
    assert current["status"] == "current" and current["files"][0]["row_count"] == 2
    assert Path(old["path"]).read_bytes() != prior
    assert not any(p.name.startswith(".measurement-") for p in tmp_path.iterdir())


def test_changed_or_foreign_file_never_overwritten_or_claimed_last_good(tmp_path):
    value = snapshot()
    result = update(value, tmp_path)
    (file,) = result["files"]
    path = Path(file["path"])
    path.write_text("foreign bytes")
    revised = copy.deepcopy(value)
    revised["source_sha256"] = "2" * 64
    stale = update(revised, tmp_path, result)
    assert stale["status"] == "stale" and path.read_text() == "foreign bytes"
    assert stale["files"][0]["payload_sha256"] is None
    assert stale["files"][0]["snapshot_id"] is None
    # A supplied digest does not authorize a file without this owner's metadata.
    result["files"][0]["payload_sha256"] = hashlib.sha256(b"foreign bytes").hexdigest()
    assert update(revised, tmp_path, result)["status"] == "stale"
    assert path.read_text() == "foreign bytes"


@pytest.mark.parametrize("failure", ["foreign", "write", "lock"])
def test_publisher_distinguishes_same_folder_retry_and_new_destination(
    tmp_path, monkeypatch, failure
):
    import color_measurement.ti3_exports as exporter

    canonical = tmp_path / "source.mat"
    canonical.write_bytes(b"Retained authoritative source; no reacquisition")
    value = snapshot()
    value.update(source_id=str(canonical.resolve()),
                 source_sha256=hashlib.sha256(canonical.read_bytes()).hexdigest())
    folder = tmp_path / "original"
    previous = update(value, folder)
    assert previous["retryable"] is False and previous["relocatable"] is False
    path = Path(previous["files"][0]["path"])
    if failure == "foreign":
        path.write_bytes(b"Foreign output must remain untouched")
    before = path.read_bytes()
    value["records"][0]["xyz"]["values"][0] += 1

    def fail(*args):
        raise OSError("Retained destination is unavailable")

    with monkeypatch.context() as context:
        if failure != "foreign":
            context.setattr(exporter, "_publish_owned_file" if failure == "write"
                            else "_folder_lock", fail)
        refused = update(value, folder, previous)
    assert refused["status"] == "stale"
    assert refused["retryable"] == (failure != "foreign")
    assert refused["relocatable"] is True
    assert path.read_bytes() == before
    relocated = update(value, tmp_path / "another-folder")
    assert relocated["status"] == "current"
    assert relocated["retryable"] is False and relocated["relocatable"] is False
    assert relocated["source_id"] == refused["source_id"] == str(canonical.resolve())
    assert canonical.read_bytes() == b"Retained authoritative source; no reacquisition"
    assert path.read_bytes() == before


@pytest.mark.parametrize("flag", ["missing", None, 0, 1, "true", []])
def test_previous_result_requires_current_typed_relocation_decision(tmp_path, flag):
    value = snapshot()
    previous = update(value, tmp_path)
    before = {file["path"]: Path(file["path"]).read_bytes() for file in previous["files"]}
    if flag == "missing":
        del previous["relocatable"]
    else:
        previous["relocatable"] = flag
    with pytest.raises(ValueError):
        update(value, tmp_path, previous)
    assert all(Path(path).read_bytes() == payload for path, payload in before.items())


def test_previous_unprojected_result_cannot_claim_relocation(tmp_path):
    value = snapshot([])
    previous = update(value, tmp_path)
    previous.update(snapshot_id=None, relocatable=True)
    with pytest.raises(ValueError, match="relocatable projection"):
        update(value, tmp_path, previous)


def test_lost_response_reuses_exact_new_payload_with_old_receipt(tmp_path):
    original = snapshot()
    previous = update(original, tmp_path)
    revised = copy.deepcopy(original)
    revised["source_sha256"] = "2" * 64
    revised["records"].append(row("second"))
    revised["ordinary_record_ids"].append("second")
    published = update(revised, tmp_path, previous)
    repeated = update(revised, tmp_path, previous)
    assert repeated == published
    assert repeated["status"] == "current"


def test_return_to_prior_source_refuses_lost_receipt_and_recovers_known_owner(tmp_path):
    a = snapshot()
    a["source_id"] = str(tmp_path / "A.mat")
    first = update(a, tmp_path)
    path = Path(first["files"][0]["path"])
    before = path.read_bytes()
    b = copy.deepcopy(a)
    b["source_id"] = str(tmp_path / "B.mat")
    second = update(b, tmp_path)
    assert first["files"][0]["path"] != second["files"][0]["path"]
    a["source_sha256"] = "2" * 64
    a["records"].append(row("second"))
    a["ordinary_record_ids"].append("second")
    blocked = update(a, tmp_path)
    assert blocked["status"] == "stale" and not blocked["retryable"]
    assert blocked["files"][0]["payload_sha256"] is None
    assert path.read_bytes() == before
    repeated = update(a, tmp_path, blocked)
    assert repeated == blocked and path.read_bytes() == before
    recovered = update(a, tmp_path, first)
    assert recovered["status"] == "current" and not recovered["retryable"]
    assert recovered["files"][0]["row_ids"] == ["original-1", "second"]
    assert path.read_bytes() != before
    with pytest.raises(ValueError, match="another authoritative source"):
        update(a, tmp_path, second)


@pytest.mark.parametrize("keep_one", [False, True])
def test_erasing_last_group_row_retires_bytes_without_permanent_stale(tmp_path, keep_one):
    one, two = row("first"), row("second")
    two["context"]["material"]["mode"] = "different-context"
    source = snapshot([one, two])
    first = update(source, tmp_path)
    before = {file["path"]: Path(file["path"]).read_bytes() for file in first["files"]}
    removed = copy.deepcopy(source)
    removed["records"] = [one] if keep_one else []
    removed["ordinary_record_ids"] = ["first"] if keep_one else []
    removed["source_sha256"] = "2" * 64
    retired = update(removed, tmp_path, first)
    for _ in range(3):
        assert retired["status"] == ("current" if keep_one else "unavailable")
        assert not retired["retryable"]
        inactive = [file for file in retired["files"] if file["state"] == "retired"]
        assert len(inactive) == (1 if keep_one else 2)
        assert all(file["payload_sha256"] and file["snapshot_id"] for file in inactive)
        assert all(Path(file["path"]).read_bytes() == before[file["path"]] for file in inactive)
        retired = update(removed, tmp_path, retired)
    source["source_sha256"] = "3" * 64
    restored = update(source, tmp_path, retired)
    assert restored["status"] == "current" and not restored["retryable"]
    assert all(file["state"] == "current" for file in restored["files"])


def test_retirement_keeps_changed_foreign_bytes_unconfirmed_and_untouched(tmp_path):
    source = snapshot()
    first = update(source, tmp_path)
    path = Path(first["files"][0]["path"])
    path.write_bytes(b"foreign file")
    erased = snapshot([])
    retired = update(erased, tmp_path, first)
    assert retired["status"] == "unavailable" and not retired["retryable"]
    file, = retired["files"]
    assert file["state"] == "retired"
    assert file["payload_sha256"] is None and file["snapshot_id"] is None
    refused = update(source, tmp_path, retired)
    assert refused["status"] == "stale" and not refused["retryable"]
    assert path.read_bytes() == b"foreign file"


@pytest.mark.parametrize("imported", [False, True])
def test_impossible_zero_row_retired_receipt_is_rejected(tmp_path, imported):
    source = imported_chart(tmp_path, 0)[0] if imported else snapshot([], charts=[chart(ids=())])
    previous = update(source, tmp_path)
    file, = previous["files"]
    assert file["state"] == "unavailable" and file["row_count"] == 0
    assert file["payload_sha256"] is None and file["snapshot_id"] is None
    file["state"] = "retired"
    with pytest.raises(ValueError, match="retired.*nonempty historical row identities"):
        update(source, tmp_path, previous)
    assert not Path(file["path"]).exists()


@pytest.mark.parametrize("imported", [False, True])
def test_unchanged_never_published_empty_chart_stays_unavailable(tmp_path, imported):
    source = imported_chart(tmp_path, 0)[0] if imported else snapshot([], charts=[chart(ids=())])
    previous = None
    for _ in range(3):
        result = update(source, tmp_path, previous)
        assert result["status"] == "unavailable" and not result["retryable"]
        file, = result["files"]
        assert file["state"] == "unavailable" and file["row_ids"] == []
        assert file["payload_sha256"] is None and file["snapshot_id"] is None
        assert not Path(file["path"]).exists()
        assert "No selected" in file["reason"]
        if previous:
            assert result == previous
        previous = result
    with _folder_lock(tmp_path, ".ti3-export.lock"):
        blocked = update(source, tmp_path, previous)
    assert blocked["status"] == "unavailable" and not blocked["retryable"]
    assert blocked["files"][0]["state"] == "unavailable"
    assert update(source, tmp_path, blocked) == result


@pytest.mark.parametrize("imported", [False, True])
def test_erasing_last_chart_selection_retires_its_previous_output(tmp_path, imported):
    source = imported_chart(tmp_path, 2)[0] if imported else snapshot(charts=[chart()])
    first = update(source, tmp_path)
    before = {file["path"]: Path(file["path"]).read_bytes() for file in first["files"]}
    erased = copy.deepcopy(source)
    erased["records"] = []
    erased["ordinary_record_ids"] = []
    erased["charts"][0]["rows"] = []
    erased["source_sha256"] = "2" * 64
    retired = update(erased, tmp_path, first)
    for _ in range(2):
        assert retired["status"] == "unavailable" and not retired["retryable"]
        assert all(file["state"] == "retired" for file in retired["files"])
        assert all(Path(file["path"]).read_bytes() == before[file["path"]]
                   for file in retired["files"])
        retired = update(erased, tmp_path, retired)
    source["source_sha256"] = "3" * 64
    restored = update(source, tmp_path, retired)
    assert restored["status"] == "current" and not restored["retryable"]
    assert all(file["state"] == "current" for file in restored["files"])


@pytest.mark.parametrize("defect", ["retirement", "ownership_retry"])
def test_retirement_and_ownership_oracles_refuse_clean_semantic_mutants(tmp_path, defect):
    import color_measurement.ti3_exports as exporter

    source = Path(exporter.__file__).read_text()
    old, new = (
        ('file["state"] = "retired"', 'file["state"] = "stale"')
        if defect == "retirement" else
        ('result["retryable"] |= not isinstance(error, FileExistsError)',
         'result["retryable"] = True')
    )
    assert source.count(old) == (3 if defect == "retirement" else 2)
    changed = source.replace(old, new)
    assert changed != source
    namespace = {"__name__": exporter.__name__, "__package__": "color_measurement"}
    exec(compile(changed, f"<removed-{defect}-guard>", "exec"), namespace)
    value = snapshot()
    previous = update(value, tmp_path)
    if defect == "retirement":
        value["records"] = []
        value["ordinary_record_ids"] = []
        real = update(value, tmp_path, previous)
        actual = json.loads(namespace["update_json"](
            json.dumps(value), str(tmp_path), json.dumps(previous)
        ))
        assert real["status"] == "unavailable" and not real["retryable"]
        with pytest.raises(AssertionError):
            assert actual["status"] == "unavailable", "Retirement remains actively stale"
    else:
        value["source_sha256"] = "2" * 64
        value["records"].append(row("another"))
        value["ordinary_record_ids"].append("another")
        real = update(value, tmp_path)
        actual = json.loads(namespace["update_json"](json.dumps(value), str(tmp_path)))
        assert real["status"] == "stale" and not real["retryable"]
        with pytest.raises(AssertionError):
            assert not actual["retryable"], "Missing ownership receipt offers futile retry"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_fifo_destination_and_lock_refuse_without_opening(tmp_path, monkeypatch):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    def must_not_open(*args, **kwargs):
        raise AssertionError("A FIFO must be refused before open")

    monkeypatch.setattr(os, "open", must_not_open)
    with pytest.raises(OSError, match="ordinary"):
        _read_regular(fifo)
    fifo.rename(tmp_path / ".ti3-export.lock")
    with pytest.raises(OSError, match="ordinary"):
        with _folder_lock(tmp_path, ".ti3-export.lock"):
            pytest.fail("FIFO lock accepted")


def test_symlink_and_hardlink_destination_refusals(tmp_path):
    other = tmp_path / "other"
    other.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(other)
    with pytest.raises(OSError):
        _read_regular(link)
    link.unlink()
    os.link(other, link)
    with pytest.raises(OSError):
        _read_regular(link)
    assert other.read_text() == "keep"


@pytest.mark.skipif(os.name == "nt", reason="POSIX kernel-lock proof")
def test_cooperating_process_cannot_take_held_lock_and_can_take_released_lock(tmp_path):
    command = [sys.executable, "-c", (
        "import fcntl,sys; f=open(sys.argv[1],'r+b');\n"
        "try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "except BlockingIOError: sys.exit(0)\n"
        "sys.exit(1)"), str(tmp_path / ".ti3-export.lock")]
    with _folder_lock(tmp_path, ".ti3-export.lock"):
        assert subprocess.run(command, timeout=5).returncode == 0
    assert subprocess.run(command, timeout=5).returncode == 1


def test_busy_publication_returns_stale_without_waiting_or_losing_winner_bytes(tmp_path):
    value = snapshot()
    previous = update(value, tmp_path)
    old = Path(previous["files"][0]["path"]).read_bytes()
    value["records"].append(row("second"))
    value["ordinary_record_ids"].append("second")
    command = [sys.executable, "-c", (
        "import sys;from color_measurement.ti3_exports import update_json;"
        "print(update_json(sys.argv[1],sys.argv[2],sys.argv[3]))"),
        json.dumps(value), str(tmp_path), json.dumps(previous)]
    with _folder_lock(tmp_path, ".ti3-export.lock"):
        run = subprocess.run(command, capture_output=True, text=True, timeout=5)
    assert run.returncode == 0, run.stderr
    blocked = json.loads(run.stdout)
    assert blocked["status"] == "stale"
    assert blocked["retryable"] is True
    assert blocked["relocatable"] is True
    assert Path(previous["files"][0]["path"]).read_bytes() == old
    assert blocked["files"][0]["payload_sha256"] == previous["files"][0]["payload_sha256"]
    assert update(value, tmp_path, blocked)["status"] == "current"


def test_semantic_refusal_preserves_previous_and_allows_later_valid_update(tmp_path, monkeypatch):
    import color_measurement.ti3_exports as exporter
    value = snapshot()
    first = update(value, tmp_path)
    original = exporter.normalize_snapshot_json
    def refused(text):
        raise ValueError("retained native requested member must be an object")
    with monkeypatch.context() as context:
        context.setattr(exporter, "normalize_snapshot_json", refused)
        without_previous = update(value, tmp_path)
        assert without_previous["status"] == "unavailable"
        assert without_previous["snapshot_id"] is None and not without_previous["retryable"]
        assert without_previous["relocatable"] is False
        assert without_previous["files"] == []
        failed = update(value, tmp_path, first)
        assert failed["status"] == "stale" and not failed["retryable"]
        assert failed["snapshot_id"] is None
        assert failed["files"][0]["payload_sha256"] == first["files"][0]["payload_sha256"]
        assert failed["files"][0]["snapshot_id"] == first["snapshot_id"]
    assert exporter.normalize_snapshot_json is original
    # Exact source-aware identity and last-good authentication survive refusal.
    assert update(value, tmp_path, failed) == first
    assert update(value, tmp_path, without_previous) == first


@pytest.mark.parametrize("already_retired", [False, True])
@pytest.mark.parametrize("retained_payload", [False, True])
def test_real_busy_lock_keeps_empty_chart_historical_rows_without_retry(
    tmp_path, already_retired, retained_payload
):
    source = snapshot(charts=[chart()])
    first = update(source, tmp_path)
    before = {file["path"]: Path(file["path"]).read_bytes() for file in first["files"]}
    if not retained_payload:
        for file in first["files"]:
            Path(file["path"]).write_bytes(b"foreign bytes")
        before = {path: b"foreign bytes" for path in before}
    source["records"] = []
    source["ordinary_record_ids"] = []
    source["charts"][0]["rows"] = []
    previous = update(source, tmp_path, first) if already_retired else first
    command = [sys.executable, "-c", (
        "import sys;from color_measurement.ti3_exports import update_json;"
        "print(update_json(sys.argv[1],sys.argv[2],sys.argv[3]))"),
        json.dumps(source), str(tmp_path), json.dumps(previous)]
    with _folder_lock(tmp_path, ".ti3-export.lock"):
        run = subprocess.run(command, capture_output=True, text=True, timeout=5)
    assert run.returncode == 0, run.stderr
    refused = json.loads(run.stdout)
    assert refused["status"] == "unavailable" and not refused["retryable"]
    assert refused["relocatable"] is False
    assert len(refused["files"]) == len(first["files"])
    for old in first["files"]:
        file = next(file for file in refused["files"] if file["group_id"] == old["group_id"])
        assert file["state"] == "retired" and file["row_ids"] == old["row_ids"]
        assert file["row_count"] == old["row_count"]
        assert bool(file["payload_sha256"]) == retained_payload
        if file["scope"] == "chart_reference":
            assert "No selected measured chart readings" in file["reason"]
        else:
            assert "Companion group is retired from the current snapshot" in file["reason"]
    assert all(Path(path).read_bytes() == payload for path, payload in before.items())
    assert update(source, tmp_path, refused)["status"] == "unavailable"


@pytest.mark.parametrize("target", ["not-json", "[]", "[" * 100000 + "0" + "]" * 100000],
                         ids=["malformed", "wrong-shape", "deep"])
def test_retained_target_decode_failure_is_a_typed_request_refusal(tmp_path, target):
    value = snapshot(charts=[dict(chart(), target=target)])
    with pytest.raises(ValueError):
        update(value, tmp_path / "companions")
    assert not (tmp_path / "companions").exists()


@pytest.mark.parametrize("failure", ["normalization", "lock"])
@pytest.mark.parametrize("keep_active", [False, True])
@pytest.mark.parametrize("retained_payload", [False, True])
def test_failed_update_preserves_retired_history_and_only_active_failures_count(
    tmp_path, monkeypatch, failure, keep_active, retained_payload
):
    import color_measurement.ti3_exports as exporter

    one, two = row("first"), row("second")
    two["context"]["material"]["mode"] = "other"
    source = snapshot([one, two])
    first = update(source, tmp_path)
    if not retained_payload:
        for file in first["files"]:
            Path(file["path"]).write_bytes(b"foreign bytes")
    source["records"] = [one] if keep_active else []
    source["ordinary_record_ids"] = ["first"] if keep_active else []
    previous = update(source, tmp_path, first)
    retired = [file for file in previous["files"] if file["state"] == "retired"]
    assert len(retired) == (1 if keep_active else 2)
    assert all(bool(file["payload_sha256"]) == retained_payload for file in retired)
    before = {file["path"]: Path(file["path"]).read_bytes() for file in previous["files"]}

    def fail(*args):
        if failure == "normalization":
            raise ValueError("invalid retained facts")
        raise BlockingIOError("companion lock is held")

    with monkeypatch.context() as context:
        context.setattr(exporter, "normalize_snapshot_json" if failure == "normalization"
                        else "_folder_lock", fail)
        refused = update(source, tmp_path, previous)
    assert len(refused["files"]) == len(previous["files"])
    actual = {file["group_id"]: file for file in refused["files"]}
    for file in retired:
        assert actual[file["group_id"]]["state"] == "retired"
        assert actual[file["group_id"]]["payload_sha256"] == file["payload_sha256"]
        assert actual[file["group_id"]]["snapshot_id"] == file["snapshot_id"]
        assert actual[file["group_id"]]["row_ids"] == file["row_ids"]
    assert refused["status"] == ("stale" if keep_active else "unavailable")
    assert refused["retryable"] == (keep_active and failure == "lock")
    assert refused["relocatable"] == (keep_active and failure == "lock")
    assert all(Path(path).read_bytes() == payload for path, payload in before.items())
    assert update(source, tmp_path, refused)["status"] == previous["status"]


def test_noncolor_and_empty_chart_have_explicit_unavailable_outcomes(tmp_path):
    value = row(xyz=False, spectrum=False)
    value["kind"] = "timing"
    result = update(snapshot([value], [chart((), ())]), tmp_path)
    assert result["status"] == "unavailable"
    assert result["unavailable"][0]["component"] == "record"
    assert result["files"][0]["chart_id"] == "stable-pass"
    assert "No selected" in result["files"][0]["reason"]


def imported_chart(tmp_path, count=6):
    source = Path(__file__).parent / "fixtures/targets/asymmetric-six-patch.ti2"
    path = tmp_path / "original.ti2"
    path.write_bytes(source.read_bytes())
    target_text = import_ti2_json(str(path))
    target = json.loads(target_text)
    path.unlink()
    records = []
    selected = []
    # Acquisition/application traversal deliberately differs from frozen table order.
    for index, item in enumerate(reversed(target["rows"][:count])):
        record = row(f"actual-{item['source_row']}")
        record["xyz"]["values"] = [0, 0, 0] if index == 0 else [index, index * 2, index * 3]
        records.append(record)
        selected.append(
            dict(record_id=record["id"], sample_id=item["sample_id"], sample_loc=item["sample_loc"])
        )
    chart_view = dict(
        id="imported-pass", provenance="simulated", total_cells=6, target=target_text, rows=selected
    )
    return snapshot(records, [chart_view]), target


@pytest.mark.parametrize("count", [0, 2, 6])
def test_target_output_has_exact_portable_pair_and_actual_source_order(tmp_path, count):
    value, target = imported_chart(tmp_path, count)
    result = update(value, tmp_path / "companions")
    file = next(f for f in result["files"] if f["scope"] == "chart_target")
    assert not any(f["scope"] == "chart_reference" for f in result["files"])
    assert file["total_rows"] == 6 and file["row_count"] == count
    if count == 0:
        assert file["state"] == "unavailable" and "No selected" in file["reason"]
        assert not Path(file["path"]).exists() and not Path(file["target_path"]).exists()
        return
    assert file["state"] == "current"
    assert Path(file["path"]).with_suffix(".ti2") == Path(file["target_path"])
    assert Path(file["target_path"]).read_bytes() == base64.b64decode(target["source_base64"])
    parsed = table(file)
    assert parsed.keywords["DEVICE_CLASS"] == "OUTPUT"
    assert parsed.keywords["COLOR_REP"] == "RGB_XYZ"
    assert parsed.keywords["CM_TARGET_SHA256"] == file["target_sha256"]
    assert "TARGET_INSTRUMENT" not in parsed.keywords
    assert parsed.keywords["CM_COVERAGE"] == ("complete" if count == 6 else "partial")
    assert [r[parsed.index("SAMPLE_LOC")] for r in parsed.rows] == [
        r["sample_loc"] for r in target["rows"][:count]
    ]
    observations = {r["id"]: r for r in value["records"]}
    for output, original in zip(parsed.rows, target["rows"][:count]):
        record_id = json.loads(output[parsed.index("CM_RECORD_ID")])
        assert output[parsed.index("SAMPLE_ID")] == original["sample_id"]
        assert [output[parsed.index(f)] for f in target["channel_fields"]] == original[
            "device_values"
        ]
        assert [output[parsed.index(f)] for f in ("XYZ_X", "XYZ_Y", "XYZ_Z")] == observations[
            record_id
        ]["xyz"]["values"]
        assert observations[record_id]["xyz"]["values"] != original["values"][-3:]
    for field in (*target["channel_fields"], "XYZ_X", "XYZ_Y", "XYZ_Z", "SPEC_401"):
        assert parsed.field_types[field] == REAL
    assert any(all(v == 0 for v in observations[i]["xyz"]["values"]) for i in file["row_ids"])


@pytest.mark.parametrize("mutation", ["source_pair", "coverage", "retained_channels"])
def test_target_association_validation_precedes_all_publication(tmp_path, mutation):
    value, target = imported_chart(tmp_path, 2)
    if mutation == "source_pair":
        value["charts"][0]["rows"][0]["sample_id"] = target["rows"][-1]["sample_id"]
    elif mutation == "coverage":
        value["charts"][0]["total_cells"] = 7
    else:
        target["rows"][0]["device_values"][0] += 1
        value["charts"][0]["target"] = json.dumps(target)
    folder = tmp_path / "outputs"
    with pytest.raises(ValueError):
        update(value, folder)
    assert not folder.exists()


def test_target_retry_preserves_source_and_last_good_on_pair_tampering(tmp_path):
    value, _ = imported_chart(tmp_path, 2)
    first = update(value, tmp_path)
    file = next(f for f in first["files"] if f["scope"] == "chart_target")
    before = Path(file["path"]).read_bytes()
    Path(file["target_path"]).write_text("foreign target")
    second = update(value, tmp_path, first)
    refused = next(f for f in second["files"] if f["scope"] == "chart_target")
    assert refused["state"] == "stale" and refused["payload_sha256"] is None
    assert Path(file["path"]).read_bytes() == before
    assert Path(file["target_path"]).read_text() == "foreign target"


def test_target_partial_then_complete_and_xyz_only_have_current_coverage(tmp_path):
    value, target = imported_chart(tmp_path, 2)
    first = update(value, tmp_path)
    complete, _ = imported_chart(tmp_path, 6)
    complete["records"][0]["spectrum"] = None
    second = update(complete, tmp_path, first)
    file = next(f for f in second["files"] if f["scope"] == "chart_target")
    assert file["state"] == "current" and file["row_count"] == 6
    assert len(table(file).rows) == 6 and table(file).index("SPEC_401") == -1
    assert any("Chart target is XYZ-only" in d["reason"] for d in second["unavailable"])
    assert Path(file["target_path"]).read_bytes() == base64.b64decode(target["source_base64"])
    assert update(complete, tmp_path, second) == second


def test_target_mixed_context_withholds_entire_pass_but_keeps_ordinary_outputs(tmp_path):
    value, _ = imported_chart(tmp_path, 2)
    value["records"][0]["context"]["material"]["filter"] = "other"
    result = update(value, tmp_path)
    file = next(f for f in result["files"] if f["scope"] == "chart_target")
    assert file["state"] == "unavailable" and "incompatible" in file["reason"]
    assert not Path(file["path"]).exists()
    assert sum(f["row_count"] for f in result["files"] if f["scope"] == "ordinary") == 2


@pytest.mark.parametrize("representation", ["iRGB", "CMYK"])
def test_target_output_preserves_subtractive_prefix_and_channel_model(tmp_path, representation):
    value, target = imported_chart(tmp_path, 2)
    text = base64.b64decode(target["source_base64"]).decode()
    text = text.replace('COLOR_REP "RGB"', f'COLOR_REP "{representation}"')
    if representation == "CMYK":
        text = text.replace("NUMBER_OF_FIELDS 8", "NUMBER_OF_FIELDS 9")
        text = text.replace("RGB_R RGB_G RGB_B", "CMYK_C CMYK_M CMYK_Y CMYK_K")
        before, data = text.split("BEGIN_DATA\n")
        rows, after = data.split("END_DATA")
        converted = []
        for line in rows.strip().splitlines():
            tokens = line.split()
            tokens.insert(5, "11.0")
            converted.append(" ".join(tokens))
        text = before + "BEGIN_DATA\n" + "\n".join(converted) + "\nEND_DATA" + after
    path = tmp_path / "other.ti2"
    path.write_text(text)
    value["charts"][0]["target"] = import_ti2_json(str(path))
    new_target = json.loads(value["charts"][0]["target"])
    result = update(value, tmp_path)
    file = next(f for f in result["files"] if f["scope"] == "chart_target")
    parsed = table(file)
    assert parsed.keywords["COLOR_REP"] == representation + "_XYZ"
    assert [parsed.rows[0][parsed.index(f)] for f in new_target["channel_fields"]] == (
        [23, 47, 61, 11] if representation == "CMYK" else [23, 47, 61])


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-link refusal")
def test_target_folder_link_cannot_redirect_publication(tmp_path):
    value, _ = imported_chart(tmp_path, 2)
    result = update(value, tmp_path)
    file = next(f for f in result["files"] if f["scope"] == "chart_target")
    destination = Path(file["path"]).parent
    shutil.rmtree(destination)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    destination.symlink_to(foreign, target_is_directory=True)
    second = update(value, tmp_path, result)
    refused = next(f for f in second["files"] if f["scope"] == "chart_target")
    assert refused["state"] == "stale" and not list(foreign.iterdir())


@pytest.fixture(scope="module")
def pinned_target_reader(argyll_archive, tmp_path_factory):
    """Compile only CGATS support and the exact pinned source's XYZ resume join."""
    import argyll_native_check as native

    directory = tmp_path_factory.mktemp("ti2-resume-reader")
    root = Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(argyll_archive) as archive:
        source = archive.read("Argyll_V3.5.0/spectro/chartread.c")
        lines = source.decode().splitlines(keepends=True)
        assert "/* We'll use the patch location to do this. */" in lines[3306]
        types = "".join(lines[3259:3274])
        join = "".join(lines[3303:3322]) + lines[3335] + "\t\t}\n"
        # Its errors use the standalone numsup support's terminating error().
        template = (root / "tests/probes/ti2_resume_probe.c").read_text()
        assert template.count("/* PINNED_XYZ_TYPES */") == 1
        assert template.count("/* PINNED_LOCATION_JOIN */") == 1
        program = template.replace("/* PINNED_XYZ_TYPES */", types).replace(
            "/* PINNED_LOCATION_JOIN */", join
        )
        for member in archive.namelist():
            name = member.removeprefix("Argyll_V3.5.0/")
            if name.split("/", 1)[0] in ("h", "numlib", "cgats") and not member.endswith("/"):
                destination = directory / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
        (directory / "sa_config.h").write_bytes(archive.read("Argyll_V3.5.0/h/aconfig.h"))
    (directory / "chartread-source.c").write_bytes(source)
    (directory / "source-report.json").write_text(
        json.dumps(
            dict(
                archive_sha256=hashlib.sha256(Path(argyll_archive).read_bytes()).hexdigest(),
                chartread_sha256=hashlib.sha256(source).hexdigest(),
                source_ranges=[[3260, 3274], [3304, 3322], [3336, 3336]],
                scope="CGATS/XYZ location join only; no chartread main or instrument code",
            ),
            indent=2,
        )
    )
    compiler = shutil.which(native.compiler_name())
    assert compiler, "Inert resume qualification requires a C compiler"
    command = [
        compiler,
        "-std=gnu99",
        "-O1",
        "-DSALONEINSTLIB",
        *(["-DNT", "-DEN_LIBUSB0"] if platform.system() == "Windows"
          else ["-DUNIX", "-pthread"]),
        *(f"-I{directory / p}" for p in (".", "h", "numlib", "cgats")),
        str(directory / "probe.c"),
        *(str(directory / p) for p in ("numlib/numsup.c", "cgats/cgats.c", "cgats/pars.c")),
        "-lm",
        "-o",
        str(native.executable_path(directory, "reader")),
    ]
    if platform.system() == "Darwin":
        command.extend(("-framework", "CoreFoundation"))
    elif platform.system() == "Windows":
        command.append("-lwinmm")
    variants = {
        "pinned": program,
        "row_position_mutant": program.replace(
            "strcmp(cols[i].loc, ((char *)rcg->t[0].fdata[k][lix])) == 0", "i == k"
        ),
        "x_value_mutant": program.replace(
            "cols[i].XYZ[0] = *((double *)rcg->t[0].fdata[k][cieix[0]]);", "cols[i].XYZ[0] = 999.0;"
        ),
    }
    readers = {}
    for label, code in variants.items():
        if label != "pinned":
            assert code != program, f"Mutant {label} failed to change the pinned block"
        (directory / "probe.c").write_text(code)
        build = command.copy()
        build[build.index("-o") + 1] = str(native.executable_path(directory, label))
        run = subprocess.run(build, capture_output=True, text=True, timeout=60)
        (directory / f"compile-{label}.log").write_text(run.stdout + run.stderr)
        (directory / f"probe-{label}.c").write_text(code)
        assert run.returncode == 0, run.stderr
        readers[label] = native.executable_path(directory, label)
    return readers


@pytest.mark.parametrize("count", [2, 6])
def test_pinned_resume_joins_selected_actual_values_by_frozen_location(
    tmp_path, pinned_target_reader, count
):
    value, target = imported_chart(tmp_path, count)
    result = update(value, tmp_path)
    file = next(f for f in result["files"] if f["scope"] == "chart_target")
    # Reverse selected output rows too: the real consumer must join by location.
    text = Path(file["path"]).read_text()
    before, data = text.split("BEGIN_DATA\n")
    rows, after = data.split("END_DATA")
    scrambled = Path(file["path"]).with_name("scrambled.ti3")
    scrambled.write_text(
        before
        + "BEGIN_DATA\n"
        + "\n".join(reversed(rows.strip().splitlines()))
        + "\nEND_DATA"
        + after
    )
    observations = {r["id"]: r for r in value["records"]}
    selected = {
        r["sample_loc"]: observations[r["record_id"]]["xyz"]["values"]
        for r in value["charts"][0]["rows"]
    }
    expected = []
    for original in target["rows"]:
        expected.append(
            [
                original["source_row"],
                int(original["sample_loc"] in selected),
                *selected.get(original["sample_loc"], [0, 0, 0]),
                original["sample_id"].encode().hex(),
                original["sample_loc"].encode().hex(),
                *original["device_values"],
            ]
        )

    def read(reader):
        run = subprocess.run(
            [str(reader), file["target_path"], str(scrambled)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert run.returncode == 0, run.stderr
        (tmp_path / f"read-{reader.name}.log").write_text(run.stdout + run.stderr)
        result = []
        for line in run.stdout.splitlines():
            values = line.split()
            result.append([float(v) if i not in (5, 6) else v for i, v in enumerate(values)])
        return result

    assert read(pinned_target_reader["pinned"]) == expected
    for label in ("row_position_mutant", "x_value_mutant"):
        # A clean execution with wrong values proves a semantic failure, not a crash.
        assert read(pinned_target_reader[label]) != expected


@pytest.fixture(scope="module")
def pinned_reader(argyll_archive, tmp_path_factory):
    """Compile pinned reader blocks with the workers' composed spectral support."""
    import argyll_native_check as native

    directory = tmp_path_factory.mktemp("ti3-pinned-reader")
    composed, report = native.prepare(argyll_archive, directory / "composed")
    root = Path(__file__).resolve().parents[1]
    includes = ("h", "numlib", "cgats", "spectro", "xicc", "rspl")
    support = (
        "numlib/numsup.c",
        "spectro/sa_conv.c",
        "cgats/cgats.c",
        "cgats/pars.c",
        "xicc/xspect.c",
        "spectro/disptechs.c",
    )
    with zipfile.ZipFile(argyll_archive) as archive:
        source = archive.read("Argyll_V3.5.0/spectro/spec2cie.c")
        assert hashlib.sha256(source).hexdigest() == (
            "1b57e8de4258c1978ade2a529a922c39bb8de005c16bf1f17eea1cd2bcc88f42"
        )
        text = source.decode()
        start = text.index('\t\tif ((ti = icg->find_kword (icg, 0, "DEVICE_CLASS")) < 0)')
        dispatch = text[start : text.index("\n\t}\n\n\t/* Set defaults */", start)]
        start = text.index('\t\tif ((ii = icg->find_kword (icg, 0, "SPECTRAL_BANDS")) < 0)')
        end = text.index("\n\t\t/* Find the fields for spectral values */", start)
        end = text.index("\n\t\t}", end) + len("\n\t\t}")
        spectral = text[start:end]
        start = text.index("\t\t\telems[oXi].d = XYZ[0] * 100.0;")
        scale = text[start : text.index("\n\t\t\telems[oLi]", start)]
        probe = (root / "tools/native_argyll/ti3_reader_probe.c").read_text()
        for marker, block in (
            ("PINNED_DISPATCH", dispatch),
            ("PINNED_SPECTRAL_READER", spectral),
            ("PINNED_OUTPUT_SCALE", scale),
        ):
            assert probe.count(f"/* {marker} */") == 1
            probe = probe.replace(f"/* {marker} */", block)
        for member in archive.namelist():
            name = member.removeprefix("Argyll_V3.5.0/")
            if name.split("/", 1)[0] in includes and not member.endswith("/"):
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
        (directory / "sa_config.h").write_bytes(archive.read("Argyll_V3.5.0/h/aconfig.h"))
    (directory / "probe.c").write_text(probe)
    for name in ("xicc/xspect.c", "xicc/xspect.h"):
        (directory / name).write_bytes((composed / name).read_bytes())
    (directory / "source-report.json").write_text(json.dumps(report, indent=2))
    compiler = shutil.which(native.compiler_name())
    assert compiler is not None, "Pinned-reader qualification requires a C compiler"
    command = [
        compiler,
        "-std=gnu99",
        "-O1",
        "-DSALONEINSTLIB",
        *(["-DNT", "-DEN_LIBUSB0"] if platform.system() == "Windows"
          else ["-DUNIX", "-pthread"]),
        f"-I{directory}",
        *(f"-I{directory / i}" for i in includes),
        str(directory / "probe.c"),
        *(str(directory / f) for f in support),
        "-lm",
        "-o",
        str(native.executable_path(directory, "reader")),
    ]
    if platform.system() == "Darwin":
        command.extend(("-framework", "CoreFoundation"))
    elif platform.system() == "Windows":
        command.append("-lwinmm")
    repaired = (directory / "xicc/xspect.c").read_text()
    guard = ("\tif (sp->spec_n < 4)\t\t/* Too few points for a cubic */\n"
             "\t\treturn getval_raw_xspec_lin_ex(sp, spec, rv, xw);\n\n")
    assert repaired.count(guard) == 1
    readers = {}
    for variant, text in (("repaired", repaired), ("guard_removed", repaired.replace(guard, ""))):
        (directory / "xicc/xspect.c").write_text(text)
        output = native.executable_path(directory, variant)
        build = command.copy()
        build[build.index("-o") + 1] = str(output)
        run = subprocess.run(
            build, capture_output=True, text=True, timeout=120
        )
        (directory / f"compile-{variant}.log").write_text(run.stdout + run.stderr)
        (directory / f"xspect-{variant}.c").write_text(text)
        assert run.returncode == 0, run.stderr
        readers[variant] = output
    (directory / "xicc/xspect.c").write_text(repaired)
    return readers


@pytest.mark.parametrize("grid", [
    pytest.param([400.5, 404.5], id="narrow-2-bands"),
    pytest.param([400.5, 404.5, 408.5], id="narrow-3-bands"),
    pytest.param([400.5, 410.5], id="coarse-2-bands"),
    pytest.param([400.5, 410.5, 420.5], id="coarse-3-bands"),
    pytest.param([400.5, 410.5, 420.5, 430.5], id="4-bands"),
    pytest.param([300.5 + n for n in range(601)], id="maximum-601-bands"),
])
def test_pinned_consumer_reflective_fractional_grid_real_columns_and_norm(
    tmp_path, pinned_reader, grid
):
    value = row(xyz=False)
    value["spectrum"].update(wavelengths_nm=grid, values=[50] * len(grid))
    (file,) = update(snapshot([value]), tmp_path)["files"]
    parsed = table(file)
    assert parsed.keywords["SPECTRAL_BANDS"] == str(len(grid))
    assert json.loads(parsed.keywords["CM_SPECTRUM_JSON"])["quantity"] == "reflectance"
    assert parsed.keywords["CM_SPECTRAL_UNIT"] == "%"
    for wavelength in grid:
        field = f"SPEC_{int(wavelength + 0.5):03d}"
        assert parsed.field_types[field] == REAL
        assert parsed.reals(field) == (50.0,)
    run = subprocess.run(
        [str(pinned_reader["repaired"]), file["path"]], capture_output=True, text=True, timeout=10
    )
    assert run.returncode == 0, run.stderr
    readback = json.loads(run.stdout)
    assert readback["bands"] == len(grid) and readback["rows"] == 1
    assert readback["norm"] == 100 and readback["emissive"] == 0
    assert readback["values"][0][1] == pytest.approx(50, rel=2e-14)
    if len(grid) >= 4:
        unchanged = subprocess.run(
            [str(pinned_reader["guard_removed"]), file["path"]],
            capture_output=True, text=True, timeout=10
        )
        assert unchanged.returncode == 0, unchanged.stderr
        assert unchanged.stdout == run.stdout


@pytest.mark.parametrize("values,expected", [
    ([20, 80], [20, 35, 50, 65, 80]),
    ([20, 80, 40], [20, 50, 80, 60, 40]),
])
def test_native_small_band_fallback_covers_direct_cubic_and_rejects_removed_guard(
    tmp_path, pinned_reader, values, expected
):
    value = row(xyz=False)
    value["spectrum"].update(
        wavelengths_nm=[400 + 10 * n for n in range(len(values))], values=values
    )
    (file,) = update(snapshot([value]), tmp_path)["files"]
    readings = {}
    for variant, reader in pinned_reader.items():
        readings[variant] = []
        for tail in (0, 1000):
            run = subprocess.run(
                [str(reader), file["path"], str(tail)],
                capture_output=True, text=True, timeout=10
            )
            assert run.returncode == 0, run.stderr
            readings[variant].append(json.loads(run.stdout)["poly"][0])
    assert readings["repaired"] == [expected, expected]
    assert readings["guard_removed"][0] != readings["guard_removed"][1]


@pytest.mark.parametrize("bands", [2, 3, 4])
def test_pinned_consumer_absolute_density_w_to_mw_and_no_y_normalization(
    tmp_path, pinned_reader, bands
):
    value = row(xyz=False)
    value["spectrum"].update(
        wavelengths_nm=[400.5 + 10 * n for n in range(bands)],
        quantity="radiance", unit="W/(m^2 sr nm)", values=[0.001] * bands
    )
    (file,) = update(snapshot([value]), tmp_path)["files"]
    run = subprocess.run(
        [str(pinned_reader["repaired"]), file["path"]], capture_output=True, text=True, timeout=10
    )
    assert run.returncode == 0, run.stderr
    readback = json.loads(run.stdout)
    assert readback["emissive"] == 1 and readback["norm"] == 100
    assert readback["values"][0][1] == pytest.approx(readback["flat_mw_y"], rel=2e-14)
    assert readback["values"][0][1] != pytest.approx(100)  # Absolute density, not white scaling.
    if bands < 4:
        regressed = subprocess.run(
            [str(pinned_reader["guard_removed"]), file["path"]],
            capture_output=True, text=True, timeout=10
        )
        assert regressed.returncode == 0, regressed.stderr
        wrong = json.loads(regressed.stdout)
        assert wrong["values"][0][1] != pytest.approx(readback["flat_mw_y"], rel=2e-14)


def test_public_complete_receipt_validator_is_read_only_and_rejects_null(tmp_path):
    from color_measurement.ti3_exports import validate_result_json

    source = snapshot()
    receipt = update(source, tmp_path)
    before = {Path(item["path"]): Path(item["path"]).read_bytes() for item in receipt["files"]}
    assert (
        validate_result_json(
            json.dumps(receipt),
            owner_id=source["owner_id"],
            source_id=source["source_id"],
            folder=tmp_path,
        )
        == receipt
    )
    assert all(path.read_bytes() == payload for path, payload in before.items())
    with pytest.raises(ValueError, match="cannot be null"):
        validate_result_json(
            "null", owner_id=source["owner_id"], source_id=source["source_id"], folder=tmp_path
        )
