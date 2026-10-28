"""TI2 semantics, source retention and source-location association refusals."""

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, replace
from importlib import resources
from pathlib import Path
from uuid import UUID

import pytest

from color_measurement import chart_context, chart_science, chart_state, demo, instrument
from color_measurement.chart_targets import (
    MAX_SOURCE_BYTES,
    expected_reference_json,
    import_ti2_json,
    validate_target_json,
)
from color_measurement.illumination import illuminant_white
from color_measurement.instrument_frontend import capability_identity
from color_measurement.models import (
    XYZ,
    CalibrationContext,
    ComputedValues,
    DeviceIdentity,
    ExternalStimulus,
    MeasuredScalar,
    Observation,
    ReportedValues,
    ScalarObservation,
    Spectrum,
)
from color_measurement.operation_records import OperationResult, PatchResult, result_from_json
from color_measurement.presentation import RELATIVE_SCALE

FIXTURE = Path(__file__).parent / "fixtures/targets/asymmetric-six-patch.ti2"
SOURCE = FIXTURE.read_text()
LAB_FIXTURE = FIXTURE.with_name("explicit-lab-six-patch.ti2")
LAB_CONTEXT = json.loads(LAB_FIXTURE.with_suffix(".context.json").read_text())


def imported(tmp_path, text=SOURCE):
    path = tmp_path / "target.ti2"
    path.write_bytes(text.encode("utf-8"))
    return json.loads(import_ti2_json(str(path)))


def test_committed_target_fixtures_match_the_current_producer():
    """The ordinary suite checks raw, interpreted and unavailable fixture bytes."""
    root = Path(__file__).resolve().parents[1]
    checked = subprocess.run(
        [sys.executable, str(root / "tools/generate_target_fixtures.py"), "--check"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("filename", [
    "asymmetric-six-patch.target.json", "explicit-lab-six-patch.target.json",
    "explicit-lab-six-patch.expected.json", "explicit-lab-six-patch.unavailable.json",
])
def test_target_fixture_currency_check_refuses_stale_output_without_repairing_it(
    tmp_path, filename
):
    root = Path(__file__).resolve().parents[1]
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "generate_target_fixtures.py"
    shutil.copy2(root / "tools/generate_target_fixtures.py", script)
    fixtures = tmp_path / "tests/fixtures/targets"
    shutil.copytree(FIXTURE.parent, fixtures)
    command = [sys.executable, str(script), "--check"]
    valid = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=120)
    assert valid.returncode == 0, valid.stdout + valid.stderr
    path = fixtures / filename
    value = json.loads(path.read_text())
    if filename.endswith(".target.json"):
        value["filename"] = "planted-stale-target.ti2"
    elif filename.endswith(".expected.json"):
        value["cells"][0]["XYZ"][0] += 1
    else:
        value["cells"][0]["reason"] += "; planted stale reason"
    path.write_text(json.dumps(value) + "\n")
    before = {p.name: p.read_bytes() for p in fixtures.iterdir() if p.is_file()}
    stale = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=120)
    assert stale.returncode != 0
    assert f"Target fixture differs: {path}" in stale.stdout + stale.stderr
    assert {p.name: p.read_bytes() for p in fixtures.iterdir() if p.is_file()} == before


def test_original_asymmetric_target_retains_bytes_and_source_order(tmp_path):
    result = imported(tmp_path)
    assert (
        result["source_sha256"]
        == "9ee845f455c97c5b7f16031dd259fb8de1dde3a1b3b661a61900ebdb00a2d57b"
    )
    assert base64.b64decode(result["source_base64"]) == FIXTURE.read_bytes()
    assert (result["strips"], result["steps"], result["color_rep"]) == (2, 3, "RGB")
    assert [r["sample_id"] for r in result["rows"]] == [
        "blk-41",
        "007",
        "=fixture()",
        "patch-X",
        "run-99",
        "patch-R",
    ]
    assert [(r["strip"], r["step"]) for r in result["rows"]] == [
        (2, 2),
        (1, 1),
        (1, 3),
        (2, 1),
        (2, 3),
        (1, 2),
    ]
    assert [r["source_row"] for r in result["rows"]] == list(range(1, 7))
    assert result["rows"][1]["raw_values"][0] == '"007"'
    assert result["rows"][0]["values"][-3:] == [999.0, 888.0, 777.0]
    assert result["rows"][0]["device_values"] == result["rows"][3]["device_values"]
    assert result["reference"]["available"] is False and result["reference"]["reason"]
    (tmp_path / "target.ti2").unlink()
    assert json.loads(validate_target_json(json.dumps(result))) == result


def test_expected_xyz_retains_declared_context_original_values_and_identity(tmp_path):
    target = imported(tmp_path)
    context = dict(LAB_CONTEXT, sourceFields=["XYZ_X", "XYZ_Y", "XYZ_Z"])
    before = copy.deepcopy(target)
    result = json.loads(expected_reference_json(json.dumps(target), json.dumps(context)))
    assert result["schema_version"] == 1 and result["source_sha256"] == target["source_sha256"]
    assert result["context"] == context
    assert [
        (cell["source_row"], cell["sample_id"], cell["sample_loc"]) for cell in result["cells"]
    ] == [(row["source_row"], row["sample_id"], row["sample_loc"]) for row in target["rows"]]
    for cell, row in zip(result["cells"], target["rows"]):
        assert cell["available"] and cell["reason"] == ""
        assert cell["XYZ"] == cell["originalValues"] == row["values"][-3:]
        assert cell["originalFields"] == context["sourceFields"]
    assert result["cells"][0]["XYZ"] == [999.0, 888.0, 777.0]  # No clipping or RGB conversion.
    assert target == before and base64.b64decode(target["source_base64"]) == FIXTURE.read_bytes()


def test_expected_lab_uses_existing_inverse_and_analytical_branch_values():
    target_json = import_ti2_json(str(LAB_FIXTURE))
    before = LAB_FIXTURE.read_bytes()
    result = json.loads(expected_reference_json(target_json, json.dumps(LAB_CONTEXT)))
    assert all(cell["available"] for cell in result["cells"])
    expected = (
        (100, 100, 100),
        (0, 0, 0),
        (12.5, 12.5, 12.5),
        (100 * (6 / 29) ** 3,) * 3,
        (-100 * (4 / 29) / (841 / 108),) * 3,
        (100 * 0.6**3, 12.5, 100 * 0.4**3),
    )
    for cell, xyz in zip(result["cells"], expected):
        assert cell["XYZ"] == pytest.approx(xyz, rel=2e-14, abs=2e-12)
        assert cell["originalFields"] == ["LAB_L", "LAB_A", "LAB_B"]
    assert result["cells"][2]["originalValues"] == [42, 0, 0]
    assert LAB_FIXTURE.read_bytes() == before


def test_expected_lab_overflow_withholds_only_its_source_row(tmp_path):
    target = imported(tmp_path, LAB_FIXTURE.read_text().replace("42 50 20", "1e308 50 20"))
    result = json.loads(expected_reference_json(json.dumps(target), json.dumps(LAB_CONTEXT)))
    assert [cell["available"] for cell in result["cells"]] == [True] * 5 + [False]
    cell = result["cells"][-1]
    assert cell["XYZ"] == [] and cell["originalValues"] == [1e308, 50, 20]
    assert "overflows" in cell["reason"]


def test_expected_reference_never_infers_context_or_uses_assertion_flags(tmp_path):
    target = imported(
        tmp_path,
        SOURCE.replace('COLOR_REP "RGB"', 'COLOR_REP "RGB"\nACCURATE_EXPECTED_VALUES "true"'),
    )
    result = json.loads(expected_reference_json(json.dumps(target)))
    assert result["context"] == {} and len(result["cells"]) == len(target["rows"])
    assert all(not cell["available"] and cell["XYZ"] == [] for cell in result["cells"])
    assert all("explicit" in cell["reason"] for cell in result["cells"])


@pytest.mark.parametrize(
    "change",
    [
        {"observerId": "CIE1964_10deg"},
        {"xyzUnits": "cd/m^2"},
        {"xyzScale": "absolute; Y is luminance"},
        {"whiteXYZ": [1, 1, 1]},
    ],
)
def test_expected_unsupported_context_is_explicitly_unavailable(change):
    context = dict(LAB_CONTEXT, **change)
    result = json.loads(
        expected_reference_json(import_ti2_json(str(LAB_FIXTURE)), json.dumps(context))
    )
    assert result["context"] == context
    assert all(not cell["available"] and cell["XYZ"] == [] for cell in result["cells"])


@pytest.mark.parametrize("field", [
    "observerId", "xyzUnits", "xyzScale", "illuminant", "interpretation", "meaning",
])
@pytest.mark.parametrize("text,accepted", [
    ("x" * 2048, True), ("x" * 2049, False),
    ("\U0001f680" * 1024, True), ("\U0001f680" * 1024 + "x", False),
], ids=["ascii-inclusive", "ascii-over", "supplementary-inclusive", "supplementary-over"])
def test_expected_context_text_uses_the_consumers_utf16_unit_bound(field, text, accepted):
    target_json = import_ti2_json(str(LAB_FIXTURE))
    source_before = LAB_FIXTURE.read_bytes()
    context = dict(LAB_CONTEXT, **{field: text})
    context_json = json.dumps(context)
    assert len(context_json) < 16384  # The decoded field bound is the discriminating refusal.
    assert len(text.encode("utf-16-le")) // 2 == (2048 if accepted else 2049)
    if accepted:
        result = json.loads(expected_reference_json(target_json, context_json))
        assert result["context"] == context
        # Long unknown identifiers are a semantic unavailability, not a text
        # boundary error; free descriptions retain the qualified Lab projection.
        available = field in {"illuminant", "interpretation", "meaning"}
        assert all(cell["available"] is available for cell in result["cells"])
    else:
        with pytest.raises(ValueError, match=rf"{field}.*2048 UTF-16 code units"):
            expected_reference_json(target_json, context_json)
    assert LAB_FIXTURE.read_bytes() == source_before
    assert import_ti2_json(str(LAB_FIXTURE)) == target_json


@pytest.mark.parametrize(
    "change",
    [
        {"sourceFields": ["RGB_R", "RGB_G", "RGB_B"]},
        {"sourceFields": ["LAB_A", "LAB_L", "LAB_B"]},
        {"whiteXYZ": [100, 0, 100]},
        {"whiteXYZ": [True, 100, 100]},
        {"whiteXYZ": "100 100 100"},
        {"meaning": " "},
        {"observerId": True},
        {"unexpected": "field"},
    ],
)
def test_expected_context_refuses_malformed_declarations(change):
    with pytest.raises(ValueError):
        expected_reference_json(
            import_ti2_json(str(LAB_FIXTURE)), json.dumps(dict(LAB_CONTEXT, **change))
        )


@pytest.mark.parametrize(
    "context",
    [
        "[]",
        "{",
        '{"whiteXYZ":NaN}',
        json.dumps(LAB_CONTEXT).replace('"observerId":', '"observerId":"duplicate","observerId":'),
        "[" * 10000 + "0" + "]" * 10000,
        " " * 16385,
    ],
)
def test_expected_context_json_trust_boundary(context):
    with pytest.raises(ValueError):
        expected_reference_json(import_ti2_json(str(LAB_FIXTURE)), context)


def test_expected_reference_selects_declared_columns_and_preserves_other_source_fields(tmp_path):
    text = SOURCE.replace("NUMBER_OF_FIELDS 8", "NUMBER_OF_FIELDS 11").replace(
        "XYZ_X XYZ_Y XYZ_Z", "XYZ_X XYZ_Y XYZ_Z LAB_L LAB_A LAB_B"
    )
    for line in SOURCE.splitlines():
        if line.startswith('"'):
            text = text.replace(line, line + " 100 0 0")
    target = imported(tmp_path, text)
    xyz = json.loads(
        expected_reference_json(
            json.dumps(target),
            json.dumps(dict(LAB_CONTEXT, sourceFields=["XYZ_X", "XYZ_Y", "XYZ_Z"])),
        )
    )
    lab = json.loads(expected_reference_json(json.dumps(target), json.dumps(LAB_CONTEXT)))
    assert xyz["cells"][0]["XYZ"] == [999, 888, 777]
    assert lab["cells"][0]["XYZ"] == [100, 100, 100]
    assert target["rows"][0]["values"][-6:] == [999, 888, 777, 100, 0, 0]


@pytest.mark.parametrize("lab_columns", [0, 2])
def test_expected_missing_selected_triplet_never_falls_back_to_another_representation(
    tmp_path, lab_columns
):
    text = SOURCE
    if lab_columns:
        text = text.replace("NUMBER_OF_FIELDS 8", "NUMBER_OF_FIELDS 10").replace(
            "XYZ_X XYZ_Y XYZ_Z", "XYZ_X XYZ_Y XYZ_Z LAB_L LAB_A"
        )
        for line in SOURCE.splitlines():
            if line.startswith('"'):
                text = text.replace(line, line + " 100 0")
    target = imported(tmp_path, text)
    result = json.loads(expected_reference_json(json.dumps(target), json.dumps(LAB_CONTEXT)))
    assert all(
        not cell["available"] and "missing or incomplete" in cell["reason"]
        for cell in result["cells"]
    )
    assert all(
        cell["originalFields"] == ["LAB_L", "LAB_A"][:lab_columns] for cell in result["cells"]
    )


def test_expected_reference_revalidates_the_complete_frozen_packet(tmp_path):
    target = imported(tmp_path)
    target["rows"][0]["values"][-1] += 1
    with pytest.raises(ValueError, match="projection changed"):
        expected_reference_json(json.dumps(target), json.dumps(LAB_CONTEXT))


@pytest.mark.parametrize("site", ["root", "field"])
def test_deep_target_json_is_a_typed_decode_refusal(site):
    nested = "[" * 100000 + "0" + "]" * 100000
    text = nested if site == "root" else '{"keywords":' + nested + "}"
    # Python 3.14 can decode this depth; the retained-target schema still refuses it.
    schema_reason = ("Retained TI2 target must be an object" if site == "root" else
                     "Retained target filename")
    with pytest.raises(ValueError, match=f"Retained TI2 target JSON nesting|{schema_reason}"):
        validate_target_json(text)


def test_target_json_decoder_recursion_is_a_typed_refusal(monkeypatch):
    def recurse(*args, **kwargs):
        raise RecursionError("Synthetic decoder recursion")

    monkeypatch.setattr(json, "loads", recurse)
    with pytest.raises(ValueError, match="Retained TI2 target JSON nesting"):
        validate_target_json("{}")


@pytest.mark.parametrize("descriptor", ["", "   "])
def test_blank_optional_descriptor_is_retained_without_disabling_import(tmp_path, descriptor):
    target = imported(
        tmp_path,
        SOURCE.replace(
            'DESCRIPTOR "Synthetic asymmetric six-patch target"', f'DESCRIPTOR "{descriptor}"'
        ),
    )
    assert target["keywords"]["DESCRIPTOR"] == descriptor
    assert len(target["rows"]) == 6
    assert json.loads(validate_target_json(json.dumps(target))) == target


BAD_INPUTS = [
    ("NUMBER_OF_FIELDS 8", "NUMBER_OF_FIELDS 9", "fields"),
    ("NUMBER_OF_SETS 6", "NUMBER_OF_SETS 5", "sets"),
    ("NUMBER_OF_FIELDS 8\n", "", "requires NUMBER"),
    ("STEPS_IN_PASS 3", "STEPS_IN_PASS 3.0", "positive integer"),
    ("STEPS_IN_PASS 3", "STEPS_IN_PASS 2\nSTEPS_IN_PASS 3", "repeated"),
    ("NUMBER_OF_SETS 6", "NUMBER_OF_SETS 0\nNUMBER_OF_SETS 6", "repeated"),
    ('COLOR_REP "RGB"', 'COLOR_REP "CMYK"\nCOLOR_REP "RGB"', "repeated"),
    ("PASSES_IN_STRIPS2 2", 'PASSES_IN_STRIPS2 "2,2"', "positive integer"),
    ("PASSES_IN_STRIPS2 2", "PASSES_IN_STRIPS 2", "Old"),
    ("STEPS_IN_PASS 3", "STEPS_IN_PASS 4", "rectangular"),
    ('STRIP_INDEX_PATTERN "A-Z, A-Z"', 'STRIP_INDEX_PATTERN "A-Z"', "STRIP_INDEX"),
    ('PATCH_INDEX_PATTERN "@-9,0-9;01-99"', 'PATCH_INDEX_PATTERN "0-9"', "PATCH_INDEX"),
    ('INDEX_ORDER "PATCH_THEN_STRIP"', 'INDEX_ORDER "UNKNOWN"', "INDEX_ORDER"),
    ('COLOR_REP "RGB"', 'COLOR_REP "GRAY"', "COLOR_REP"),
    ('COLOR_REP "RGB"', 'DEVICE_CLASS "DISPLAY"\nCOLOR_REP "RGB"', "reflective"),
    ('COLOR_REP "RGB"', 'HEXAGON_PATCHES "False"\nCOLOR_REP "RGB"', "hexagonal"),
    ('"patch-X"', '"007"', "duplicate"),
    ('"patch-X"', '"0"', "padding"),
    ('"patch-X"', '""', "ASCII"),
    ('"patch-X"', '"non-ASCII ☀"', "ASCII"),
    ('"patch-X"', '"' + "x" * 129 + '"', "ASCII"),
    ('"01B"', '"01A"', "collide"),
    ('"01B"', '"00B"', "range"),
    ('"01B"', '"04B"', "rectangle"),
    ('"01B"', '"01C"', "rectangle"),
    ('"01B"', '"1B"', "two-digit"),
    ('"01B"', '"B01"', "pattern"),
    ("23.0 47.0 61.0", "-1.0 47.0 61.0", "0..100"),
    ("23.0 47.0 61.0", "101.0 47.0 61.0", "0..100"),
    ("23.0 47.0 61.0", "nan 47.0 61.0", "unexpected type"),
    ("23.0 47.0 61.0", "1e999 47.0 61.0", "not finite"),
    ("23.0 47.0 61.0", "1e-999 47.0 61.0", "underflows"),
    ("RGB_G", "OTHER_G", "channels"),
    ("SAMPLE_LOC", "OTHER_LOC", "SAMPLE_LOC"),
    ('"02B"', "02B", ""),
]


def test_padding_id_refusal_is_distinct_from_duplicate_real_sample(tmp_path):
    with pytest.raises(ValueError, match="printtarg padding ID 0 is unsupported"):
        imported(tmp_path, SOURCE.replace('"patch-X"', '"0"'))
    with pytest.raises(ValueError, match="TI2 SAMPLE_ID is duplicate$"):
        imported(tmp_path, SOURCE.replace('"patch-X"', '"007"'))


@pytest.mark.parametrize("old,new,reason", BAD_INPUTS)
def test_specific_refusal_preserves_input(tmp_path, old, new, reason):
    if old == '"02B"':
        # Column inference is quoted if any other row stays quoted.
        changed = SOURCE
        for strip in ("A", "B"):
            for step in range(1, 4):
                changed = changed.replace(f'"0{step}{strip}"', f"0{step}{strip}")
        reason = "must be quoted"
    else:
        changed = SOURCE.replace(old, new)
    assert changed != SOURCE
    path = tmp_path / "input.ti2"
    path.write_text(changed)
    before = path.read_bytes()
    with pytest.raises(ValueError, match=reason):
        import_ti2_json(str(path))
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["input.ti2"]


def test_extra_table_size_limit_and_empty_inputs(tmp_path):
    for text in (SOURCE + SOURCE, SOURCE + "CAL\n", "", "CTI2\n"):
        with pytest.raises(ValueError):
            imported(tmp_path, text)
    path = tmp_path / "large.ti2"
    path.write_bytes(b"#" * (MAX_SOURCE_BYTES + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        import_ti2_json(str(path))
    assert path.stat().st_size == MAX_SOURCE_BYTES + 1


def test_normalized_location_collision_defaults_and_both_orders(tmp_path):
    text = SOURCE.replace('PATCH_INDEX_PATTERN "@-9,0-9;01-99"\n', "")
    text = text.replace('"01B"', '"1A"')
    with pytest.raises(ValueError, match="collide"):
        imported(tmp_path, text)
    text = SOURCE.replace('STRIP_INDEX_PATTERN "A-Z, A-Z"\n', "")
    text = text.replace('PATCH_INDEX_PATTERN "@-9,0-9;01-99"\n', "")
    text = text.replace('INDEX_ORDER "PATCH_THEN_STRIP"\n', "")
    for strip in ("A", "B"):
        for step in range(1, 4):
            text = text.replace(f'"0{step}{strip}"', f'"{strip}{step}"')
    target = imported(tmp_path, text)
    assert target["rows"][0]["sample_loc"] == "B2"
    assert (target["rows"][0]["strip"], target["rows"][0]["step"]) == (2, 2)


@pytest.mark.parametrize("representation", ["RGB", "iRGB", "CMYK"])
def test_channels_preserve_declared_semantics(tmp_path, representation):
    text = SOURCE.replace('COLOR_REP "RGB"', f'COLOR_REP "{representation}"')
    if representation == "CMYK":
        text = text.replace("NUMBER_OF_FIELDS 8", "NUMBER_OF_FIELDS 9")
        text = text.replace("RGB_R RGB_G RGB_B", "CMYK_C CMYK_M CMYK_Y CMYK_K")
        begin, data = text.split("BEGIN_DATA\n")
        lines = data.splitlines()
        for index in range(6):
            lines[index] += " 11.0"
        # Add K after the existing three channel values, preserving expected XYZ.
        for index in range(6):
            tokens = lines[index].rsplit(" ", 4)
            lines[index] = tokens[0] + " 11.0 " + " ".join(tokens[1:4])
        text = begin + "BEGIN_DATA\n" + "\n".join(lines) + "\n"
    result = imported(tmp_path, text)
    assert result["color_rep"] == representation
    assert result["rows"][0]["device_values"][:3] == [23, 47, 61]
    assert len(result["channel_fields"]) == (4 if representation == "CMYK" else 3)
    if representation == "CMYK":
        assert result["rows"][0]["device_values"][3] == 11


@pytest.mark.parametrize("count", [140, 141])
def test_supported_limit_counts_before_constructing_cells(tmp_path, count):
    text = (
        'CTI2\nCOLOR_REP "RGB"\nSTEPS_IN_PASS '
        + str(count)
        + "\nPASSES_IN_STRIPS2 1\nNUMBER_OF_FIELDS 5\nBEGIN_DATA_FORMAT\n"
        "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B\nEND_DATA_FORMAT\nNUMBER_OF_SETS "
        + str(count)
        + "\nBEGIN_DATA\n"
        + "\n".join(f'id-{i} "A{i}" 0 0 0' for i in range(1, count + 1))
        + "\nEND_DATA\n"
    )
    if count == 140:
        assert len(imported(tmp_path, text)["rows"]) == 140
    else:
        with pytest.raises(ValueError, match="141 patches; supported count is 1..140"):
            imported(tmp_path, text)


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "importer_revision",
        "source_sha256",
        "strips",
        "steps",
        "color_rep",
        "keywords",
        "fields",
        "field_types",
        "channel_fields",
        "rows",
        "reference",
    ],
)
def test_retained_projection_mutations_are_refused(tmp_path, field):
    target = imported(tmp_path)
    changed = copy.deepcopy(target)
    changed[field] = True if isinstance(target[field], int) else "changed"
    with pytest.raises(ValueError, match="projection changed"):
        validate_target_json(json.dumps(changed))


def test_digest_alone_does_not_authenticate_edited_identity_or_channels(tmp_path):
    target = imported(tmp_path)
    for name in (
        "source_row",
        "sample_id",
        "sample_loc",
        "strip",
        "step",
        "device_values",
        "values",
        "raw_values",
    ):
        changed = copy.deepcopy(target)
        changed["rows"][-1][name] = "changed"
        with pytest.raises(ValueError, match="projection changed"):
            validate_target_json(json.dumps(changed))
    changed = copy.deepcopy(target)
    changed["source_base64"] = base64.b64encode(b"CTI2\n").decode()
    changed["source_sha256"] = hashlib.sha256(b"CTI2\n").hexdigest()
    with pytest.raises(ValueError):
        validate_target_json(json.dumps(changed))
    with pytest.raises(ValueError, match="Duplicate JSON"):
        validate_target_json(
            json.dumps(target).replace(
                '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
            )
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO admission")
def test_nonregular_source_is_refused_without_blocking(tmp_path):
    path = tmp_path / "pipe.ti2"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="ordinary file"):
        import_ti2_json(str(path))


def test_nonregular_path_is_refused_before_open(tmp_path, monkeypatch):
    calls = []

    def opened(*args):
        calls.append(args)
        raise AssertionError("Nonregular input must never be opened")

    monkeypatch.setattr(os, "open", opened)
    with pytest.raises(ValueError, match="before opening"):
        import_ti2_json(str(tmp_path))
    assert calls == []


def test_descriptive_filename_controls_refused_and_unicode_retained(tmp_path):
    path = tmp_path / "printed-測定.ti2"
    path.write_text(SOURCE)
    target = json.loads(import_ti2_json(str(path)))
    assert target["filename"] == path.name
    for name in ("line\nbreak.ti2", "tab\t.ti2", "control\x7f.ti2"):
        changed = copy.deepcopy(target)
        changed["filename"] = name
        with pytest.raises(ValueError, match="without controls"):
            validate_target_json(json.dumps(changed))


@pytest.mark.parametrize("guard", ["channels", "locations", "projection"])
def test_deliberately_removed_import_guards_fail_their_semantic_oracle(guard):
    import color_measurement.chart_targets as targets

    source = Path(targets.__file__).read_text()
    edits = {
        "channels": ("not 0 <= value <= 100", "False"),
        "locations": ("if position in positions:", "if False:"),
        "projection": ("if not _same(value, expected):", "if False:"),
    }
    old, new = edits[guard]
    assert source.count(old) == 1
    changed = source.replace(old, new)
    assert changed != source
    namespace = {"__name__": "color_measurement._target_mutant", "__package__": "color_measurement"}
    exec(compile(changed, f"<removed-{guard}-guard>", "exec"), namespace)
    if guard == "projection":
        target = targets._parse(SOURCE.encode(), "target.ti2")
        target["rows"][-1]["device_values"][0] += 1
        with pytest.raises(ValueError, match="projection changed"):
            targets.validate_target_json(json.dumps(target))
        actual = json.loads(namespace["validate_target_json"](json.dumps(target)))
        # The mutant returns a clean success for a forged retained projection.
        with pytest.raises(AssertionError):
            assert actual == target, "Changed projection was silently accepted"
    else:
        bad = (
            SOURCE.replace("23.0 47.0 61.0", "101.0 47.0 61.0")
            if guard == "channels"
            else (SOURCE.replace('"01B"', '"01A"'))
        )
        assert bad != SOURCE
        with pytest.raises(ValueError):
            targets._parse(bad.encode(), "target.ti2")
        actual = namespace["_parse"](bad.encode(), "target.ti2")
        with pytest.raises(AssertionError):
            if guard == "channels":
                assert all(
                    0 <= value <= 100 for row in actual["rows"] for value in row["device_values"]
                ), "Out-of-range channels accepted"
            else:
                assert len({(row["strip"], row["step"]) for row in actual["rows"]}) == 6, (
                    "Duplicate location accepted"
                )


# Chart scientific consumer cases use original synthetic native records. No
# instrument, MATLAB, renderer or external color-library calculation is invoked.
CHART_OBSERVER = (
    resources.files("color_measurement")
    .joinpath("desktop_assets", "resources", "CIE_xyz_1931_2deg.csv")
    .read_bytes()
)
CHART_QUALIFICATION = dict(
    specimen_id="synthetic-print-1",
    edition="1",
    geometry="45/0",
    backing="white",
    lighting="declared M0 setup",
    declarer="fixture",
    matched_conditions_confirmed=True,
)
CHART_CRITERIA = dict(
    maximum_delta_e=2,
    reference_bound=1,
    measurement_bound=1,
    basis="Synthetic deterministic Lab-vector bounds",
    coverage="Declared example",
)


def chart_source(
    index=1, *, values=(20, 25, 10), native=None, mode="reflective_spot", backend="argyll-demo"
):
    xyz = native or XYZ(tuple(values), "1", RELATIVE_SCALE, "CIE1931_2", "D50")
    observation = Observation(
        1,
        str(UUID(int=100 + index)),
        str(UUID(int=200 + index)),
        f"native-{index:03d}",
        "2026-10-13T10:00:00+00:00",
        "simulated",
        DeviceIdentity("synthetic-chart", "synthetic", backend, "1"),
        mode,
        mode,
        (),
        CalibrationContext("not_applicable", "synthetic"),
        ReportedValues(xyz=xyz),
        ComputedValues(),
        '{"synthetic":true}',
        ("SIMULATED",),
    )
    material = {
        key: dict(value=value, certainty="readback", source="Original observation")
        for key, value in dict(
            xyz_unit=xyz.unit,
            xyz_scale=xyz.scale,
            observer=xyz.observer,
            illuminant=xyz.illuminant,
            actual_mode=mode,
        ).items()
    }
    material["geometry"] = dict(value="45/0", certainty="operator-declared", source="Fixture")
    # Ownership and unused spectral details do not invent colorimetric requirements.
    for key in ("serial", "origin", "spectral_grid_nm"):
        material[key] = dict(value=None, certainty="unknown", source="not reported")
    context = dict(
        phase="accepted",
        material=material,
        required_material=list(material),
        provenance=dict(observation_provenance="simulated"),
    )
    reading = dict(
        segment_id=str(UUID(int=1)),
        operation_id=observation.operation_id,
        observation_id=observation.observation_id,
        sample_id=observation.sample_id,
    )
    return dict(
        cell_index=index, observation=observation, reading_ref=reading, accepted_context=context
    )


def chart_native_lab(index, lab):
    white = illuminant_white(
        "D50",
        observer="CIE1931_2",
        reference_y=100,
        unit="1",
        scale=RELATIVE_SCALE,
        observer_csv=CHART_OBSERVER,
    ).xyz
    # Independent elementary cube-branch inverse for the chosen positive values.
    fy = (lab[0] + 16) / 116
    fx, fz = fy + lab[1] / 500, fy - lab[2] / 200
    return chart_source(index, values=tuple(w * f**3 for w, f in zip(white, (fx, fy, fz))))


def measured_reference(selected, *, criteria=None, columns=4):
    definition = chart_state.definition("custom", name="synthetic", rows=1, columns=columns)
    packet = chart_science.prepare_position_reference(
        definition,
        selected,
        qualification=CHART_QUALIFICATION,
        criteria=criteria or CHART_CRITERIA,
        observer_csv=CHART_OBSERVER,
    )
    return definition, packet


def test_chart_science_retains_exact_native_source_common_white_and_independent_cell_binding():
    wrapper = chart_source(2, values=(0, 0, 0))
    original = copy.deepcopy(wrapper)
    result = chart_science.chart_reading_colors(wrapper, observer_csv=CHART_OBSERVER)
    assert result["available"] and result["lab"] == [0, 0, 0]
    assert result["color"]["encoding"]["display_rgb"] == [0, 0, 0]
    assert result["color"]["encoding"]["common_reference_y"] == 100
    assert result["cell_index"] == 2
    assert result["source"]["reading_ref"]["sample_id"] == "native-002"
    assert result["source"]["observation"] == json.loads(json.dumps(asdict(wrapper["observation"])))
    assert wrapper == original


@pytest.mark.parametrize(
    "mode", ["reflective_strip_m2", "reflective_xy", "reflective_chart", "saved_chart"]
)
def test_chart_science_uses_existing_live_and_saved_reflective_mode_grammar(mode):
    result = chart_science.chart_reading_colors(
        chart_source(mode=mode), observer_csv=CHART_OBSERVER
    )
    assert result["available"]
    assert result["source"]["observation"]["actual_mode"] == mode


@pytest.mark.parametrize(
    "change",
    [
        "unit",
        "scale",
        "observer",
        "illuminant",
        "negative",
        "missing_xyz",
        "mode",
        "backend",
        "native_fact",
        "requested_fact",
        "unknown_fact",
        "provenance",
        "identity",
        "phase",
        "observer_bytes",
    ],
)
def test_chart_science_unqualified_native_or_context_stays_unavailable(change):
    native_changes = {
        "unit": "%",
        "scale": "relative",
        "observer": "CIE1964_10",
        "illuminant": "custom",
    }
    base = XYZ((20, 25, 10), "1", RELATIVE_SCALE, "CIE1931_2", "D50")
    wrapper = chart_source(
        native=replace(base, **{change: native_changes[change]})
        if change in native_changes
        else base,
        mode="emissive_spot" if change == "mode" else "reflective_spot",
        backend="arbitrary" if change == "backend" else "argyll-demo",
    )
    context = wrapper["accepted_context"]
    if change == "negative":
        wrapper["observation"] = replace(
            wrapper["observation"],
            backend_reported=ReportedValues(xyz=replace(base, values=(-1, 2, 3))),
        )
    elif change == "missing_xyz":
        wrapper["observation"] = replace(
            wrapper["observation"],
            backend_reported=ReportedValues(
                spectrum=Spectrum((400, 500), (1, 2), "reflectance", "%")
            ),
        )
    elif change in {"native_fact", "requested_fact", "unknown_fact"}:
        fact = context["material"]["observer"]
        if change == "native_fact":
            fact["value"] = "CIE1964_10"
        else:
            fact["certainty"] = "requested" if change == "requested_fact" else "unknown"
    elif change == "provenance":
        context["provenance"]["observation_provenance"] = "physical"
    elif change == "identity":
        wrapper["reading_ref"]["sample_id"] = "B1"
    elif change == "phase":
        context["phase"] = "intended"
    original = copy.deepcopy(wrapper)
    result = chart_science.chart_reading_colors(
        wrapper, observer_csv=b"changed" if change == "observer_bytes" else CHART_OBSERVER
    )
    assert not result["available"] and result["reason"]
    assert result["xyz"] is None and result["color"]["encoding"] is None
    assert wrapper == original


@pytest.mark.parametrize("geometry", ["requested", "unknown", "mismatched"])
def test_chart_science_swatch_does_not_establish_measured_reference_conditions(geometry):
    wrapper = chart_source()
    fact = wrapper["accepted_context"]["material"]["geometry"]
    if geometry == "mismatched":
        fact["value"] = "d/8"
    else:
        fact["certainty"] = geometry
    assert chart_science.chart_reading_colors(wrapper, observer_csv=CHART_OBSERVER)["available"]
    definition, reference = measured_reference([wrapper])
    assert not reference["qualified"] and reference["coverage"] == 0
    assert reference["cells"][0]["source"]["reading_ref"] == wrapper["reading_ref"]
    result = chart_science.chart_position_check(
        reference, definition, chart_source(), observer_csv=CHART_OBSERVER
    )
    assert not result["available"] and result["reason"]


@pytest.mark.parametrize("strips_as", ["rows", "columns"])
def test_chart_science_expected_mapping_preserves_original_ids_locations_and_signed_values(
    strips_as,
):
    target = import_ti2_json(str(LAB_FIXTURE))
    definition = chart_state.definition(
        "ti2", target_json=target, strips_as=strips_as, mapping_confirmed=True
    )
    result = chart_science.chart_expected_colors(definition, json.dumps(LAB_CONTEXT))
    original = json.loads(expected_reference_json(target, json.dumps(LAB_CONTEXT)))
    for mapped in result["cells"]:
        row = original["cells"][mapped["source_row"] - 1]
        assert mapped["expected"] == row
        assert (mapped["source_id"], mapped["source_location"]) == (
            row["sample_id"],
            row["sample_loc"],
        )
    white = next(cell for cell in result["cells"] if cell["source_id"] == "white")
    assert white["color"]["encoding"]["display_rgb"] == pytest.approx([1, 1, 1], abs=4e-15)
    assert chart_science.chart_expected_colors(definition, "null")["available"] is False
    xyz_target = import_ti2_json(str(FIXTURE))
    value = json.loads(xyz_target)
    payload = base64.b64decode(value["source_base64"]).decode()
    # Create a new legitimate signed source, never edit its retained projections.
    payload = payload.replace("999.0", "-999.0")
    # The parser is already tested separately; this original-input fixture uses its public importer.
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        path = Path(directory) / "signed.ti2"
        path.write_text(payload)
        signed = chart_state.definition(
            "ti2",
            target_json=import_ti2_json(str(path)),
            strips_as=strips_as,
            mapping_confirmed=True,
        )
    signed_colors = chart_science.chart_expected_colors(
        signed, json.dumps(dict(LAB_CONTEXT, sourceFields=["XYZ_X", "XYZ_Y", "XYZ_Z"]))
    )
    row = next(item for item in signed_colors["cells"] if item["source_id"] == "blk-41")
    assert row["expected"]["XYZ"][0] == -999
    assert not row["color"]["available"] and "negative" in row["color"]["reason"]


def test_chart_science_reference_preserves_holes_originals_and_rejects_projection_tampering():
    source = chart_native_lab(2, (50, 3, 4))
    definition, reference = measured_reference([None, source])
    assert reference["qualified"] and reference["coverage"] == 1
    assert reference["cells"][0]["source"] is None
    assert reference["cells"][1]["source"]["reading_ref"] == source["reading_ref"]
    assert reference["criteria"]["maximum_delta_e"] == [2] * 4
    assert (
        chart_science.validate_position_reference(
            reference, definition, observer_csv=CHART_OBSERVER
        )
        == reference
    )
    tampered = copy.deepcopy(reference)
    tampered["cells"][1]["values"]["xyz"][0] += 1
    # Rehashing a forged convenience projection must not authenticate it.
    tampered["digest"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in tampered.items() if key != "digest"},
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    with pytest.raises(ValueError, match="projection changed"):
        chart_science.validate_position_reference(tampered, definition, observer_csv=CHART_OBSERVER)
    with pytest.raises(ValueError, match="Duplicate selected"):
        measured_reference([source, source])
    with pytest.raises(ValueError, match="multiple reference cells"):
        measured_reference([source, dict(source, cell_index=3)])
    duplicate_segment = dict(
        source, cell_index=3, reading_ref=dict(source["reading_ref"], segment_id=str(UUID(int=2)))
    )
    with pytest.raises(ValueError, match="multiple reference cells"):
        measured_reference([source, duplicate_segment])
    changed = chart_state.definition("custom", name="another specimen", rows=1, columns=4)
    with pytest.raises(ValueError, match="mapping.*changed"):
        chart_science.validate_position_reference(reference, changed, observer_csv=CHART_OBSERVER)


@pytest.mark.parametrize("value", [-1, True, float("inf"), 10**1000, [1, 2], [1, 2, 3, -1]])
def test_chart_science_explicit_policy_refuses_invalid_limits(value):
    with pytest.raises(ValueError):
        measured_reference([chart_source()], criteria=dict(CHART_CRITERIA, maximum_delta_e=value))


def test_chart_science_requires_explicit_matched_qualification_and_finite_total():
    definition = chart_state.definition("custom", name="synthetic", rows=1, columns=1)
    with pytest.raises(ValueError, match="Explicit specimen"):
        chart_science.prepare_position_reference(
            definition,
            [chart_source()],
            qualification=dict(CHART_QUALIFICATION, matched_conditions_confirmed=False),
            criteria=CHART_CRITERIA,
            observer_csv=CHART_OBSERVER,
        )
    with pytest.raises(ValueError, match="Combined chart"):
        measured_reference(
            [chart_source()],
            criteria=dict(CHART_CRITERIA, maximum_delta_e=1e308, reference_bound=1e308),
        )


def test_chart_science_position_uses_unclipped_cie76_and_nearest_overlap_before_display():
    sources = [
        chart_native_lab(1, (50, 3, 4)),
        chart_native_lab(2, (55, 3, 4)),
        chart_native_lab(3, (52, 3, 4)),
        chart_native_lab(4, (90, 3, 4)),
    ]
    definition, reference = measured_reference(sources)
    current = chart_native_lab(1, (50, 6, 8))
    original = copy.deepcopy((reference, current))
    result = chart_science.chart_position_check(
        reference, definition, current, observer_csv=CHART_OBSERVER
    )
    assert result["available"] and result["warning"]
    assert result["distance"] == pytest.approx(5, abs=8e-14)
    assert result["limit"] == 4 and result["overlapping_cell"] == 3
    assert result["nearest_reference_separation"] == pytest.approx(2, abs=2e-14)
    assert "ambiguous" in result["alternative_reason"]
    assert result["reference_digest"] == reference["digest"]
    assert (reference, current) == original


def test_chart_science_boundary_equality_does_not_warn_and_separation_is_not_placement():
    white = illuminant_white(
        "D50",
        observer="CIE1931_2",
        reference_y=100,
        unit="1",
        scale=RELATIVE_SCALE,
        observer_csv=CHART_OBSERVER,
    ).xyz
    definition, reference = measured_reference(
        [chart_source(values=(0, 0, 0))],
        criteria=dict(CHART_CRITERIA, maximum_delta_e=100, reference_bound=0, measurement_bound=0),
    )
    check = chart_science.chart_position_check(
        reference, definition, chart_source(values=white), observer_csv=CHART_OBSERVER
    )
    assert check["distance"] == check["limit"] == 100
    assert check["available"] and not check["warning"]
    assert "not verified" in check["reason"]
    definition, separated = measured_reference(
        [chart_native_lab(1, (20, 0, 0)), chart_native_lab(2, (90, 0, 0))]
    )
    check = chart_science.chart_position_check(
        separated, definition, chart_native_lab(1, (20, 0, 0)), observer_csv=CHART_OBSERVER
    )
    assert check["overlapping_cell"] is None and "separated" in check["alternative_reason"]
    assert "unverified" in check["alternative_reason"]


def test_chart_science_position_context_mismatch_and_absent_cell_remain_unavailable():
    definition, reference = measured_reference([chart_source()])
    current = chart_source()
    current["accepted_context"]["material"]["geometry"]["value"] = "d/8"
    result = chart_science.chart_position_check(
        reference, definition, current, observer_csv=CHART_OBSERVER
    )
    assert not result["available"] and "mismatched" in result["reason"]
    result = chart_science.chart_position_check(
        reference, definition, chart_source(2), observer_csv=CHART_OBSERVER
    )
    assert not result["available"] and "No selected" in result["reason"]
    result = chart_science.chart_position_check(
        None, definition, current, observer_csv=CHART_OBSERVER
    )
    assert not result["available"] and result["reference_digest"] is None
    # A different instrument identity does not itself change measured color conditions.
    current = chart_source()
    current["observation"] = replace(
        current["observation"],
        device=DeviceIdentity("other", "other model", "argyll-native", "2", "serial-123", "utf8"),
    )
    current["accepted_context"]["material"]["serial"].update(
        value="serial-123", certainty="readback"
    )
    assert chart_science.chart_position_check(
        reference, definition, current, observer_csv=CHART_OBSERVER
    )["available"]


def test_chart_science_position_requires_actual_mode_even_if_caller_omits_required_keys():
    source = chart_source(mode="reflective_spot_m0")
    source["accepted_context"]["required_material"] = []
    definition, reference = measured_reference([source])
    current = chart_source(mode="reflective_spot_m1")
    current["accepted_context"]["required_material"] = []
    result = chart_science.chart_position_check(
        reference, definition, current, observer_csv=CHART_OBSERVER
    )
    assert not result["available"] and "mismatched" in result["reason"]


@pytest.fixture(scope="module")
def chart_context_demo():
    calls = []

    def refuse_native(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Chart context Demo attempted native transport")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(instrument, "HelperSession", refuse_native)
        front = next(front for front in demo.demo_frontends() if front._meter.persona == "i1pro3")
        descriptor = json.loads(front.describe_json())
        request = dict(
            schema_version=1,
            device_id=descriptor["device_id"],
            capability_id=descriptor["capability_id"],
            mode="reflective_spot",
            workflow="spot",
            patch_ids=["chart-context-fixture"],
            options={},
            layout={},
            event_contract="retained_patch_v1",
        )
        operation = front.prepare(json.dumps(request))
        try:
            operation.dispatch()
            deadline = time.monotonic() + 10
            while not operation.done:
                while raw := operation.poll_event_json():
                    event = json.loads(raw)
                    if event["kind"] == "instruction_required":
                        operation.respond(
                            operation.operation_id, event["payload"]["request_id"], "accept"
                        )
                assert time.monotonic() < deadline, "Chart context Demo did not stop"
                time.sleep(0.001)
            result = result_from_json(operation.result_json())
            assert result.status == "completed" and len(result.patches) == 1
        finally:
            operation.close()
        yield descriptor, request, result
        assert not calls, "Chart context Demo attempted native transport"


def test_chart_context_actual_demo_enriches_intent_without_certifying_missing_facts(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    intended = chart_context.intended(descriptor, request)
    accepted = chart_context.accepted(
        descriptor, request, result, result.patches[0].observation.observation_id
    )
    assert chart_state.validate_context(intended) == intended
    assert chart_state.validate_context(accepted) == accepted
    assert chart_state.context_difference(intended, accepted) == []
    observation = result.patches[0].observation
    for key, value in dict(
        xyz_unit="1",
        xyz_scale=RELATIVE_SCALE,
        observer="CIE1931_2",
        illuminant="D50",
        actual_mode=observation.actual_mode,
    ).items():
        assert accepted["material"][key]["value"] == value
        assert accepted["material"][key]["certainty"] == "readback"
        assert intended["material"][key]["certainty"] == "unknown"
    assert accepted["material"]["spectral_grid_nm"]["value"] == list(
        observation.backend_reported.spectrum.wavelengths_nm
    )
    assert accepted["material"]["spectrum_norm"]["value"] == float(
        dict(observation.configuration)["spectrum_norm"]
    )
    assert accepted["material"]["factory_calibration"]["certainty"] == "unknown"
    assert accepted["material"]["geometry"]["certainty"] == "unknown"
    assert intended["material"]["serial"]["value"] == descriptor["device"]["serial"]
    assert intended["material"]["serial"]["certainty"] == "acknowledged"
    assert accepted["material"]["serial"]["certainty"] == "readback"
    wrapper = dict(
        cell_index=1,
        observation=observation,
        reading_ref=dict(
            segment_id=str(UUID(int=1)),
            operation_id=observation.operation_id,
            observation_id=observation.observation_id,
            sample_id=observation.sample_id,
        ),
        accepted_context=accepted,
    )
    assert chart_science.chart_reading_colors(wrapper, observer_csv=CHART_OBSERVER)["available"]


def test_chart_context_acceptance_refuses_different_request_descriptor_or_observation(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    with pytest.raises(ValueError, match="absent"):
        chart_context.accepted(descriptor, request, result, "missing-observation")
    with pytest.raises(ValueError, match="exact chart request"):
        chart_context.accepted(descriptor, dict(request, options={"averages": 2}), result)
    tampered = copy.deepcopy(descriptor)
    tampered["device"]["model"] = "another"
    with pytest.raises(ValueError, match="capability identity"):
        chart_context.intended(tampered, request)


def test_chart_context_requested_options_never_become_readback_by_record_presence(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    request = dict(request, options={"averages": 2})
    result = replace(result, request_json=json.dumps(request))
    context = chart_context.accepted(descriptor, request, result)
    assert context["material"]["requested_option:averages"]["value"] == 2
    assert context["material"]["requested_option:averages"]["certainty"] == "requested"
    assert context["material"]["effective_option:averages"]["certainty"] == "unknown"
    assert "effective_option:averages" in context["required_material"]


def test_chart_context_native_settings_are_flattened_and_unknowns_retained(chart_context_demo):
    descriptor, request, result = chart_context_demo
    observation = result.patches[0].observation
    facts = dict(
        driver="fixture-driver",
        factory_calibration="unknown",
        identity=dict(verified=True),
        settings=[
            dict(key="Accessory", value="Standard", presence="present", driver_set=False),
            dict(key="Backing", value=None, presence="unknown", driver_set=False),
        ],
    )
    configuration = dict(observation.configuration, native_facts_json=json.dumps(facts))
    observation = replace(observation, configuration=tuple(sorted(configuration.items())))
    result = replace(result, patches=(replace(result.patches[0], observation=observation),))
    context = chart_context.accepted(descriptor, request, result)
    fact = context["material"]["native_setting:Accessory"]
    assert fact["certainty"] == "readback"
    assert fact["value"] == {key: facts["settings"][0][key] for key in ("value", "presence")}
    assert context["provenance"]["native_setting_driver_set"] == {
        "Accessory": False,
        "Backing": False,
    }
    writes_changed = copy.deepcopy(context)
    writes_changed["provenance"]["native_setting_driver_set"]["Accessory"] = True
    assert chart_state.context_difference(context, writes_changed) == []
    assert context["material"]["native_setting:Backing"]["certainty"] == "unknown"
    enriched = copy.deepcopy(context)
    enriched["material"]["native_setting:Backing"] = dict(
        value={"value": "white", "presence": "present", "driver_set": False},
        certainty="readback",
        source="Later original record",
    )
    assert chart_state.context_difference(context, enriched) == []
    lost = copy.deepcopy(enriched)
    lost["material"]["native_setting:Accessory"] = dict(
        value=None, certainty="unknown", source="Not reported"
    )
    assert any(item["kind"] == "lost" for item in chart_state.context_difference(enriched, lost))
    changed = copy.deepcopy(enriched)
    changed["material"]["native_setting:Accessory"]["value"]["value"] = "Other"
    assert any(
        item["kind"] == "changed" for item in chart_state.context_difference(enriched, changed)
    )


def test_chart_context_failed_no_reading_and_colorimeter_absences_remain_distinct(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    failed = replace(result, status="failed", patches=(), error="Synthetic stopped receipt")
    context = chart_context.accepted(descriptor, request, failed)
    assert context["provenance"]["observation_id"] is None
    assert context["material"]["xyz_unit"]["certainty"] == "unknown"
    assert context["material"]["actual_mode"]["certainty"] == "unknown"
    observation = result.patches[0].observation
    observation = replace(
        observation, backend_reported=ReportedValues(xyz=observation.backend_reported.xyz)
    )
    original = replace(result, patches=(replace(result.patches[0], observation=observation),))
    context = chart_context.accepted(descriptor, request, original)
    assert context["material"]["xyz_unit"]["certainty"] == "readback"
    assert context["material"]["spectral_grid_nm"]["certainty"] == "unknown"
    assert "spectral_grid_nm" not in context["required_material"]


def test_chart_context_continuation_declarations_are_explicit_and_identity_stays_current(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    source = chart_context.accepted(descriptor, request, result)
    declared = chart_context.declared_continuation(
        descriptor, request, source, ["mode", "workflow", "xyz_unit"]
    )
    assert declared["material"]["xyz_unit"]["certainty"] == "operator-declared"
    assert declared["material"]["observer"]["certainty"] == "unknown"
    assert (
        declared["material"]["serial"]
        == chart_context.intended(descriptor, request)["material"]["serial"]
    )
    assert declared["provenance"]["acknowledged_material"] == ["mode", "workflow", "xyz_unit"]
    with pytest.raises(ValueError, match="identity cannot"):
        chart_context.declared_continuation(descriptor, request, source, ["serial"])
    with pytest.raises(ValueError, match="cannot be certified"):
        chart_context.declared_continuation(descriptor, request, source, ["geometry"])
    with pytest.raises(ValueError, match="conflicts"):
        chart_context.declared_continuation(
            descriptor, dict(request, mode="reflective_strip", workflow="strip"), source, ["mode"]
        )


def test_chart_context_selected_earlier_unknown_fact_cannot_be_certified_by_pass_enrichment(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    context = chart_context.accepted(descriptor, request, result)
    original = copy.deepcopy(context)
    original["material"]["serial"] = dict(
        value=None, certainty="unknown", source="Earlier source absence"
    )
    definition = chart_state.definition("custom", name="synthetic", rows=1, columns=1)
    state = chart_state.new_state(
        definition,
        original,
        run_id=str(UUID(int=11)),
        segment_id=str(UUID(int=12)),
        pass_id=str(UUID(int=13)),
    )
    state = chart_state.begin_attempt(
        state,
        attempt_id=str(UUID(int=14)),
        command_id=str(UUID(int=15)),
        operation_id=result.operation_id,
        request=request,
        intended_context=chart_context.intended(descriptor, request),
    )
    observation = result.patches[0].observation
    state = chart_state.complete_attempt(
        state,
        str(UUID(int=14)),
        status="completed",
        accepted_context=original,
        reading_refs=[
            dict(
                segment_id=state["segment_id"],
                operation_id=result.operation_id,
                observation_id=observation.observation_id,
                sample_id=observation.sample_id,
            )
        ],
    )
    # Later evidence can enrich the pass display, but never its earlier retained attempt.
    state["passes"][0]["context"] = context
    payload = json.dumps(dict(batch_id=state["segment_id"], chart=state)).encode()
    with pytest.raises(ValueError, match="established on both sides"):
        chart_state.continue_state(
            state,
            context,
            segment_id=str(UUID(int=16)),
            source_id="synthetic-manifest",
            source_bytes=payload,
            source_sha256=hashlib.sha256(payload).hexdigest(),
        )


def test_chart_context_explicit_complete_declarations_preserve_inherited_demo_coverage(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    source = chart_context.accepted(descriptor, request, result)
    declarations = [
        key
        for key in source["required_material"]
        if key
        not in {
            "route",
            "origin",
            "device_id",
            "model",
            "serial",
            "serial_encoding",
            "backend",
            "backend_version",
            "driver",
        }
    ]
    target = chart_context.declared_continuation(descriptor, request, source, declarations)
    definition = chart_state.definition("custom", name="synthetic", rows=1, columns=1)
    state = chart_state.new_state(
        definition,
        source,
        run_id=str(UUID(int=21)),
        segment_id=str(UUID(int=22)),
        pass_id=str(UUID(int=23)),
    )
    state = chart_state.begin_attempt(
        state,
        attempt_id=str(UUID(int=24)),
        command_id=str(UUID(int=25)),
        operation_id=result.operation_id,
        request=request,
        intended_context=chart_context.intended(descriptor, request),
    )
    observation = result.patches[0].observation
    state = chart_state.complete_attempt(
        state,
        str(UUID(int=24)),
        status="completed",
        accepted_context=source,
        reading_refs=[
            dict(
                segment_id=state["segment_id"],
                operation_id=result.operation_id,
                observation_id=observation.observation_id,
                sample_id=observation.sample_id,
            )
        ],
    )
    payload = json.dumps(dict(batch_id=state["segment_id"], chart=state)).encode()
    continued = chart_state.continue_state(
        state,
        target,
        segment_id=str(UUID(int=26)),
        source_id="synthetic-manifest",
        source_bytes=payload,
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert chart_state.view(continued)["coverage"] == 1
    assert continued["passes"][0]["attempts"][0]["accepted_context"] == source
    assert (
        continued["passes"][0]["context"]["material"]["observer"]["certainty"]
        == "operator-declared"
    )


@pytest.fixture
def chart_current_binding(monkeypatch):
    from test_native_controls import controls_request, native_frontend, native_readback_seam

    # Reuse the current verified binding and typed Measurement seam. No native
    # process runs, and the observation keeps its actual simulated CR quantities.
    front = native_frontend("cr250", serial="CR-SOFTWARE")
    native_readback_seam(monkeypatch, front)
    descriptor = json.loads(front.describe_json())
    request = json.loads(controls_request(front, workflow="spot"))
    operation = front.prepare(json.dumps(request), timeout=400)
    try:
        operation.dispatch()
        deadline = time.monotonic() + 3
        while not operation.done:
            assert time.monotonic() < deadline, "Chart identity fixture did not stop"
            time.sleep(0.001)
        result = result_from_json(operation.result_json())
        assert result.status == "completed" and len(result.patches) == 1
    finally:
        operation.close()
    request = json.loads(result.request_json)
    observation = result.patches[0].observation
    context = chart_context.accepted(descriptor, request, result, observation.observation_id)
    context["material"]["geometry"] = dict(
        value="Synthetic declared geometry", certainty="operator-declared",
        source="Synthetic matched setup",
    )
    context["required_material"] = sorted(
        set(context["required_material"]) | {"geometry", "driver"}
        | {key for key in context["material"] if key.startswith("native_identity:")}
    )
    source = dict(
        cell_index=1, observation=observation, accepted_context=context, result=result,
        reading_ref=dict(
            segment_id=str(UUID(int=6103)), operation_id=observation.operation_id,
            observation_id=observation.observation_id, sample_id=observation.sample_id,
        ),
    )
    # Instrument-kind numbers and initialized Argyll enums are separate domains.
    descriptor["current_binding_identity"]["initialized_type"] = 123
    descriptor["capability_id"] = capability_identity(
        {key: value for key, value in descriptor.items() if key != "capability_id"},
    )
    request = dict(request, capability_id=descriptor["capability_id"])
    return descriptor, request, source


def test_chart_context_current_native_packet_establishes_identity_only(chart_current_binding):
    descriptor, request, source = chart_current_binding
    before = copy.deepcopy((descriptor, request))
    intended = chart_context.intended(descriptor, request)
    facts = descriptor["current_binding_identity"]["native_identity"]
    assert intended["material"]["driver"]["value"] == facts["driver"]
    assert intended["material"]["driver"]["certainty"] == "readback"
    for key, value in facts["identity"].items():
        fact = intended["material"]["native_identity:" + key]
        assert fact["value"] == value and fact["certainty"] == "readback"
    for key in ("calibration", "observer", "xyz_unit", "native_mcond", "factory_calibration"):
        assert intended["material"][key]["certainty"] == "unknown"
    assert intended["provenance"]["current_binding_identity"] == \
        descriptor["current_binding_identity"]
    assert {"driver", *["native_identity:" + key for key in facts["identity"]]} <= \
        set(intended["required_material"])
    assert (descriptor, request) == before
    assert {"driver", "device_id"} <= chart_context.source_identity_keys(
        source["observation"], source["accepted_context"],
    )


def test_chart_context_current_identity_and_explicit_science_preserve_continue(
    chart_current_binding,
):
    descriptor, request, source = chart_current_binding
    accepted = source["accepted_context"]
    current = chart_context.intended(descriptor, request)
    declarations = [
        key for key in accepted["required_material"]
        if current["material"].get(key, {}).get("certainty") in {None, "unknown", "requested"}
    ]
    target = chart_context.declared_continuation(descriptor, request, accepted, declarations)
    grid = chart_state.definition("custom", name="Synthetic binding", rows=1, columns=1)
    state = chart_state.new_state(grid, accepted, run_id=str(UUID(int=7101)),
                                  segment_id=str(UUID(int=7102)), pass_id=str(UUID(int=7103)))
    operation_id = source["observation"].operation_id
    state = chart_state.begin_attempt(
        state, attempt_id=str(UUID(int=7104)), command_id=str(UUID(int=7105)),
        operation_id=operation_id, request=request,
        intended_context=current,
    )
    state = chart_state.complete_attempt(
        state, str(UUID(int=7104)), status="completed", accepted_context=accepted,
        reading_refs=[dict(source["reading_ref"], segment_id=state["segment_id"])],
    )
    payload = json.dumps(dict(batch_id=state["segment_id"], chart=state)).encode()
    continued = chart_state.continue_state(
        state, target, segment_id=str(UUID(int=7106)), source_id="synthetic-current-binding",
        source_bytes=payload, source_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert chart_state.view(continued)["coverage"] == 1
    assert continued["passes"][0]["attempts"] == state["passes"][0]["attempts"]
    changed = copy.deepcopy(target)
    changed["material"]["native_identity:firmware"]["value"] = "different current firmware"
    with pytest.raises(ValueError, match="continuity is unresolved"):
        chart_state.continue_state(
            state, changed, segment_id=str(UUID(int=7107)), source_id="synthetic-current-binding",
            source_bytes=payload, source_sha256=hashlib.sha256(payload).hexdigest(),
        )


def test_chart_context_native_instrument_kind_is_not_initialized_argyll_type(chart_current_binding):
    descriptor, request, _ = chart_current_binding
    descriptor["current_binding_identity"]["native_identity"]["identity"]["instrument_type"] = 0
    descriptor["capability_id"] = capability_identity(
        {key: value for key, value in descriptor.items() if key != "capability_id"},
    )
    context = chart_context.intended(
        descriptor, dict(request, capability_id=descriptor["capability_id"]),
    )
    assert context["material"]["native_identity:instrument_type"]["value"] == 0
    assert context["material"]["native_identity:instrument_type"]["certainty"] == "readback"
    assert context["provenance"]["current_binding_identity"]["initialized_type"] == 123


def test_chart_context_later_current_driver_cannot_certify_earlier_selected_unknown_identity(
    chart_current_binding,
):
    descriptor, request, known = chart_current_binding
    source_descriptor = copy.deepcopy(descriptor)
    source_descriptor["current_binding_identity"]["native_identity"] = None
    source_descriptor["capability_id"] = capability_identity(
        {key: value for key, value in source_descriptor.items() if key != "capability_id"},
    )
    source_request = dict(request, capability_id=source_descriptor["capability_id"])
    observation = known["observation"]
    configuration = dict(observation.configuration)
    configuration.pop("native_facts_json")
    observation = replace(observation, operation_id=str(UUID(int=9101)),
                          observation_id=str(UUID(int=9102)),
                          configuration=tuple(sorted(configuration.items())))
    result = replace(known["result"], operation_id=observation.operation_id,
                     request_json=json.dumps(source_request),
                     patches=(replace(known["result"].patches[0], observation=observation),))
    earlier = chart_context.accepted(source_descriptor, source_request, result)
    assert earlier["material"]["driver"]["certainty"] == "unknown"
    current = chart_context.intended(descriptor, request)
    grid = chart_state.definition("custom", name="Synthetic identity history", rows=1, columns=2)
    state = chart_state.new_state(grid, earlier, run_id=str(UUID(int=8101)),
                                  segment_id=str(UUID(int=8102)), pass_id=str(UUID(int=8103)))
    for number, observation, accepted in (
        (8104, result.patches[0].observation, earlier),
        (8106, known["observation"], known["accepted_context"]),
    ):
        state = chart_state.begin_attempt(
            state, attempt_id=str(UUID(int=number)), command_id=str(UUID(int=number + 1)),
            operation_id=observation.operation_id, request=source_request,
            intended_context=chart_context.intended(source_descriptor, source_request),
        )
        state = chart_state.complete_attempt(
            state, str(UUID(int=number)), status="completed", accepted_context=accepted,
            reading_refs=[dict(
                segment_id=state["segment_id"], operation_id=observation.operation_id,
                observation_id=observation.observation_id, sample_id=observation.sample_id,
            )],
        )
    accepted = state["passes"][0]["context"]
    declarations = [
        key for key in accepted["required_material"]
        if current["material"].get(key, {}).get("certainty") in {None, "unknown", "requested"}
    ]
    target = chart_context.declared_continuation(descriptor, request, accepted, declarations)
    payload = json.dumps(dict(batch_id=state["segment_id"], chart=state)).encode()
    with pytest.raises(ValueError, match="driver is unestablished in selected attempt"):
        chart_state.continue_state(
            state, target, segment_id=str(UUID(int=8108)), source_id="synthetic-identity-history",
            source_bytes=payload, source_sha256=hashlib.sha256(payload).hexdigest(),
        )


def test_chart_context_binding_receipts_are_provenance_and_null_adapter_is_unknown(
    chart_current_binding,
):
    descriptor, request, _ = chart_current_binding
    original = chart_context.intended(descriptor, request)
    changed = copy.deepcopy(descriptor)
    changed["current_binding_identity"]["provenance"].update(
        description_helper_sha256="d" * 64, session_helper_sha256="e" * 64,
        build_report_sha256="f" * 64,
    )
    changed["capability_id"] = capability_identity(
        {key: value for key, value in changed.items() if key != "capability_id"},
    )
    context = chart_context.intended(changed, dict(request, capability_id=changed["capability_id"]))
    assert context["material"] == original["material"]
    assert chart_state.context_difference(original, context) == []
    assert context["provenance"]["current_binding_identity"] != \
        original["provenance"]["current_binding_identity"]
    changed["current_binding_identity"]["native_identity"] = None
    changed["capability_id"] = capability_identity(
        {key: value for key, value in changed.items() if key != "capability_id"},
    )
    unavailable = chart_context.intended(
        changed, dict(request, capability_id=changed["capability_id"]),
    )
    assert unavailable["material"]["driver"]["certainty"] == "unknown"
    assert not any(key.startswith("native_identity:") for key in unavailable["material"])


@pytest.mark.parametrize(
    "receipt,value,accepted",
    [
        ("build_report_sha256", None, True),
        ("description_helper_sha256", None, False),
        ("session_helper_sha256", None, False),
        ("build_report_sha256", "invalid digest", False),
    ],
)
def test_chart_context_optional_build_report_receipt_keeps_worker_receipts_strict(
    chart_current_binding, receipt, value, accepted,
):
    descriptor, request, _ = chart_current_binding
    original = chart_context.intended(descriptor, request)
    descriptor["current_binding_identity"]["provenance"][receipt] = value
    descriptor["capability_id"] = capability_identity(
        {key: value for key, value in descriptor.items() if key != "capability_id"},
    )
    request = dict(request, capability_id=descriptor["capability_id"])
    if not accepted:
        with pytest.raises(ValueError, match="Malformed chart current binding provenance"):
            chart_context.intended(descriptor, request)
        return
    context = chart_context.intended(descriptor, request)
    assert context["material"] == original["material"]
    assert chart_state.context_difference(original, context) == []
    assert context["provenance"]["current_binding_identity"]["provenance"][receipt] is None
    assert context["material"]["calibration"]["certainty"] == "unknown"


@pytest.mark.parametrize(
    "fault", ["device", "type", "version", "receipt", "provenance_shape", "extra", "native_shape"],
)
def test_chart_context_refuses_malformed_or_other_current_binding(chart_current_binding, fault):
    descriptor, request, _ = chart_current_binding
    packet = descriptor["current_binding_identity"]
    if fault == "device":
        packet["device_id"] = "another current binding"
    elif fault == "type":
        packet["initialized_type"] = True
    elif fault == "version":
        packet["provenance"]["backend_version"] = "another backend"
    elif fault == "receipt":
        packet["provenance"]["session_helper_sha256"] = "invalid digest"
    elif fault == "provenance_shape":
        del packet["provenance"]["build_report_sha256"]
    elif fault == "extra":
        packet["capture"] = {"xyz": [0, 0, 0]}
    else:
        packet["native_identity"]["settings"] = {"calibration": "ready"}
    descriptor["capability_id"] = capability_identity(
        {key: value for key, value in descriptor.items() if key != "capability_id"},
    )
    refusal = "initialized native identity" if fault == "native_shape" else "current binding"
    with pytest.raises(ValueError, match=refusal):
        chart_context.intended(descriptor, dict(request, capability_id=descriptor["capability_id"]))


def test_chart_context_direct_identity_uses_current_frontend_without_certifying_calibration():
    from test_serial_instruments import meter, startup
    from test_serial_settings import cr_report

    from color_measurement.serial_frontend import DirectSerialFrontend

    connected, port = meter("CR-100", startup("CR-100"))
    connected.connect()
    frontend = DirectSerialFrontend(connected, cr_report())
    try:
        descriptor = json.loads(frontend.describe_json())
        request = dict(device_id=descriptor["device_id"], capability_id=descriptor["capability_id"],
                       mode="emissive_spot", workflow="spot", options={})
        current = chart_context.intended(descriptor, request)
        assert current["material"]["driver"]["value"] == "direct_serial"
        for key in ("model", "serial", "firmware"):
            assert current["material"]["native_identity:" + key]["value"] == \
                descriptor["identity_details"][key]
        assert current["material"]["calibration"]["certainty"] == "unknown"
        unknown = dict(current, phase="accepted")
        with pytest.raises(ValueError, match="cannot be certified"):
            chart_context.declared_continuation(descriptor, request, unknown, ["calibration"])
        descriptor["identity_details"]["serial"] = "another current serial"
        descriptor["capability_id"] = capability_identity(
            {key: value for key, value in descriptor.items() if key != "capability_id"},
        )
        with pytest.raises(ValueError, match="current binding"):
            chart_context.intended(
                descriptor, dict(request, capability_id=descriptor["capability_id"]),
            )
    finally:
        assert frontend.close() and not port.script


def test_chart_context_missing_current_serial_remains_unknown_and_cannot_be_declared(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    source = chart_context.accepted(descriptor, request, result)
    descriptor = copy.deepcopy(descriptor)
    descriptor["device"].update(serial=None, serial_encoding=None)
    descriptor["capability_id"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in descriptor.items() if key != "capability_id"},
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    request = dict(request, capability_id=descriptor["capability_id"])
    context = chart_context.declared_continuation(descriptor, request, source, ["mode"])
    assert context["material"]["serial"]["certainty"] == "unknown"
    with pytest.raises(ValueError, match="identity cannot"):
        chart_context.declared_continuation(descriptor, request, source, ["serial"])


def test_chart_context_typed_scalar_does_not_fabricate_photometric_context(chart_context_demo):
    from color_measurement.argyll_capability import CAPABILITY2, mode_word
    from color_measurement.instrument_frontend import InstrumentFrontend

    frontend = InstrumentFrontend(
        instrument.Instrument("/must-not-open", "instI1Disp3"),
        mode_capabilities=mode_word("emissive_spot"),
        trigger_capabilities=CAPABILITY2["emis_refr_meas"],
        filter_capabilities=0,
        provenance="simulated",
    )
    descriptor = json.loads(frontend.describe_json())
    request = dict(
        schema_version=1,
        device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"],
        mode="emissive_spot",
        workflow="refresh_frequency",
        patch_ids=["RATE-1"],
        options={},
        layout={
            "stimulus_id": "WHITE-60",
            "stimulus_description": "Externally presented synthetic white",
        },
        event_contract="retained_patch_v1",
    )
    identity = DeviceIdentity(**descriptor["device"])
    calibration = CalibrationContext("unknown", "Synthetic scalar fixture; no assessment")
    observation = ScalarObservation(
        1,
        str(UUID(int=32)),
        str(UUID(int=31)),
        "RATE-1",
        "2026-10-13T10:00:00+00:00",
        "simulated",
        identity,
        "emissive_spot",
        "emissive_spot",
        (),
        calibration,
        MeasuredScalar(59.94),
        ExternalStimulus("WHITE-60", request["layout"]["stimulus_description"]),
        '{"synthetic":true}',
    )
    result = OperationResult(
        1,
        observation.operation_id,
        identity.device_id,
        identity,
        "simulated",
        "emissive_spot",
        "refresh_frequency",
        "completed",
        ("RATE-1",),
        (PatchResult(0, "RATE-1", observation),),
        json.dumps(request),
        (),
        calibration,
        None,
        True,
        "not_applicable",
    )
    context = chart_context.accepted(descriptor, request, result)
    assert context["material"]["actual_mode"]["value"] == "emissive_spot"
    assert context["material"]["xyz_unit"]["certainty"] == "unknown"
    assert context["material"]["spectral_grid_nm"]["certainty"] == "unknown"
    assert "xyz_unit" not in context["required_material"]
    assert "spectral_grid_nm" not in context["required_material"]


def test_chart_context_historical_timestamp_cannot_be_declared_as_current_setup(chart_context_demo):
    descriptor, request, result = chart_context_demo
    source = chart_context.accepted(descriptor, request, result)
    source["material"]["acquired_at"] = dict(
        value=result.patches[0].observation.acquired_at,
        certainty="readback",
        source="Original observation timestamp",
    )
    with pytest.raises(ValueError, match="Only optical/setup"):
        chart_context.declared_continuation(descriptor, request, source, ["acquired_at"])


def test_chart_context_request_binding_distinguishes_json_boolean_from_numeric(chart_context_demo):
    descriptor, request, result = chart_context_demo
    retained = dict(request, options={"highres": True})
    result = replace(result, request_json=json.dumps(retained))
    with pytest.raises(ValueError, match="exact chart request"):
        chart_context.accepted(descriptor, dict(retained, options={"highres": 1}), result)


def chart_adapter_bound_source(descriptor, request, result, *, device_id, number, driver):
    """Fresh synthetic typed binding records over the unchanged actual Demo quantities."""
    descriptor = copy.deepcopy(descriptor)
    observation = result.patches[0].observation
    device = replace(observation.device, device_id=device_id)
    configuration = dict(observation.configuration)
    configuration.pop("native_facts_json", None)
    if driver is not None:
        configuration["native_facts_json"] = json.dumps(
            dict(
                driver=driver,
                identity=dict(
                    model=device.model,
                    serial=device.serial,
                    firmware=f"synthetic-{number}",
                    verified=False,
                ),
                factory_calibration="unknown",
                capture=dict(reply_count=0, setting_count=0),
                replies=[],
                settings=[],
            )
        )
    observation = replace(
        observation,
        device=device,
        operation_id=str(UUID(int=number)),
        observation_id=str(UUID(int=number + 1)),
        configuration=tuple(sorted(configuration.items())),
    )
    descriptor["device_id"], descriptor["device"] = device_id, asdict(device)
    descriptor["capability_id"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in descriptor.items() if key != "capability_id"},
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    request = dict(request, device_id=device_id, capability_id=descriptor["capability_id"])
    result = replace(
        result,
        device_id=device_id,
        device=device,
        operation_id=observation.operation_id,
        request_json=json.dumps(request),
        patches=(replace(result.patches[0], observation=observation),),
    )
    context = chart_context.accepted(descriptor, request, result, observation.observation_id)
    context["material"]["geometry"] = dict(
        value="45/0", certainty="operator-declared", source="Synthetic matched setup"
    )
    context["required_material"] = sorted(
        set(context["required_material"])
        | {"geometry", "driver"}
        | {key for key in context["material"] if key.startswith("native_identity:")}
    )
    return dict(
        cell_index=1,
        observation=observation,
        accepted_context=context,
        reading_ref=dict(
            segment_id=str(UUID(int=number + 2)),
            operation_id=observation.operation_id,
            observation_id=observation.observation_id,
            sample_id=observation.sample_id,
        ),
    )


@pytest.mark.parametrize("drivers", [("synthetic-driver-A", "synthetic-driver-B"), (None, None)])
def test_chart_science_real_adapter_compares_source_bound_identity_as_provenance(
    chart_context_demo, drivers
):
    descriptor, request, result = chart_context_demo
    reference_source = chart_adapter_bound_source(
        descriptor, request, result, device_id="synthetic-binding-A", number=1001, driver=drivers[0]
    )
    current = chart_adapter_bound_source(
        descriptor, request, result, device_id="synthetic-binding-B", number=2001, driver=drivers[1]
    )
    definition, reference = measured_reference(
        [reference_source, dict(current, cell_index=2)], columns=2
    )
    assert reference["qualified"]
    check = chart_science.chart_position_check(
        reference, definition, current, observer_csv=CHART_OBSERVER
    )
    assert check["available"] and not check["warning"] and check["distance"] == 0
    assert check["nearest_reference_separation"] == 0 and check["overlapping_cell"] == 2
    assert (
        reference["cells"][0]["source"]["observation"]["device"]["device_id"]
        == "synthetic-binding-A"
    )
    assert current["observation"].device.device_id == "synthetic-binding-B"
    if drivers[0] is None:
        assert reference_source["accepted_context"]["material"]["driver"]["certainty"] == "unknown"
    else:
        assert (
            reference_source["accepted_context"]["material"]["native_identity:firmware"]["value"]
            != current["accepted_context"]["material"]["native_identity:firmware"]["value"]
        )
    # Actual optical/calibration/correction fields retain their scientific fence.
    for key, value in (
        ("geometry", "d/8"),
        ("optical_mode", "transmission"),
        ("native_mcond", 3),
        ("correction_applied", "matrix"),
        ("calibration", "required"),
    ):
        changed = copy.deepcopy(current)
        changed["accepted_context"]["material"][key]["value"] = value
        refused = chart_science.chart_position_check(
            reference, definition, changed, observer_csv=CHART_OBSERVER
        )
        assert not refused["available"] and "mismatched" in refused["reason"]


def test_chart_science_identity_shaped_optical_fact_requires_original_identity_source(
    chart_context_demo,
):
    descriptor, request, result = chart_context_demo
    reference_source = chart_adapter_bound_source(
        descriptor, request, result, device_id="synthetic-binding-A", number=3001, driver=None
    )
    current = chart_adapter_bound_source(
        descriptor, request, result, device_id="synthetic-binding-B", number=4001, driver=None
    )
    for source, value in ((reference_source, "m0"), (current, "m2")):
        context = source["accepted_context"]
        context["material"]["native_identity:optical_filter"] = dict(
            value=value,
            certainty="operator-declared",
            source="Optical filter declaration; no native identity fact",
        )
        context["required_material"].append("native_identity:optical_filter")
    definition, reference = measured_reference([reference_source], columns=1)
    assert reference["qualified"]
    check = chart_science.chart_position_check(
        reference, definition, current, observer_csv=CHART_OBSERVER
    )
    assert not check["available"] and "mismatched" in check["reason"]
