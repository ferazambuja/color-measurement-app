"""Current generic form metadata and admission; no device or native worker."""

import json

import pytest

from color_measurement.demo import demo_frontends
from color_measurement.instrument import Instrument
from color_measurement.instrument_frontend import _validate_layout
from color_measurement.models import PresentationStamp
from color_measurement.strip_layout import _validate_value


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    monkeypatch.setattr(Instrument, "_open_session",
                        lambda *args, **kwargs: pytest.fail("Native transport"))


def test_every_published_workflow_has_one_complete_field_shape_and_matching_keys():
    seen = set()
    base = {"key", "label", "type", "required", "default", "help"}
    for front in demo_frontends():
        descriptor = json.loads(front.describe_json())
        for mode in descriptor["modes"]:
            for workflow in mode["workflows"]:
                seen.add(workflow["workflow"])
                fields = workflow["layout_fields"]
                assert sorted(field["key"] for field in fields) == workflow["layout_keys"]
                assert sorted(field["key"] for field in fields if field["required"]) == \
                    workflow["required_layout_keys"]
                for field in fields:
                    assert base <= field.keys() and "id" not in field
                    assert type(field["required"]) is bool and field["label"] and field["help"]
                    assert not field["required"] or field["default"] is None
                    if field["default"] is not None:
                        _validate_value(field, field["default"])
    assert {"spot", "strip", "xy", "chart", "calibrate", "mode_snapshot", "stored_refresh",
            "refresh_frequency", "display_timing"} <= seen


@pytest.mark.parametrize("workflow,layout,count", [
    ("xy", dict(passes=True, steps=2), 1),
    ("xy", dict(passes=1, steps=0), 1),
    ("xy", dict(passes=1, steps=1, pass_name="LONG"), 1),
    ("xy", dict(passes=1, steps=1, step_name="\n1"), 1),
    ("xy", dict(passes=1, steps=1, hexagonal=1), 1),
    ("chart", dict(passes=1, steps=1, sheets=[]), 1),
    ("chart", dict(passes=1, steps=1, sheets=[True]), 1),
    ("chart", dict(passes=1, steps=1, sheets=[1.0]), 1),
    ("chart", dict(passes=65, steps=1, sheets=[1] * 65), 65),
    ("chart", dict(passes=2, steps=2, sheets=[1]), 4),
    ("chart", dict(passes=1, steps=1, sheets=[1], chart_id=2147483648), 1),
    ("refresh_frequency", dict(stimulus_id="é" * 65, stimulus_description="White"), 1),
    ("display_timing", dict(stimulus_id="White", stimulus_description="  ", display_id="S"), 1),
    ("stored_refresh", dict(calibrate="yes"), 0),
    ("xy", dict(passes=1, steps=1, unconsumed=1), 1),
])
def test_invalid_generic_operands_refuse_before_a_native_worker(workflow, layout, count):
    with pytest.raises(ValueError):
        _validate_layout(workflow, layout, count)


def test_layout_validation_preserves_omission_and_native_geometry_rules():
    for workflow, layout, count in [
        ("xy", dict(passes=1, steps=1), 1),
        ("xy", dict(passes=2, steps=2, pass_name=" A", step_name=" 1", hexagonal=False), 3),
        ("chart", dict(passes=2, steps=2, sheets=[1, 1], chart_id=0), 4),
        ("refresh_frequency", dict(stimulus_id="é" * 64, stimulus_description="White"), 1),
        ("stored_refresh", {}, 0),
    ]:
        before = json.dumps(layout, sort_keys=True)
        _validate_layout(workflow, layout, count)
        assert json.dumps(layout, sort_keys=True) == before


def test_presentation_reply_metadata_matches_typed_stamp_and_keeps_draw_owner():
    descriptor = next(json.loads(front.describe_json()) for front in demo_frontends()
                      if any(workflow["workflow"] == "display_timing"
                             for mode in json.loads(front.describe_json())["modes"]
                             for workflow in mode["workflows"]))
    workflow = next(workflow for mode in descriptor["modes"] for workflow in mode["workflows"]
                    if workflow["workflow"] == "display_timing")
    assert workflow["presentation_reply_owner"] == "client_draw_completion"
    fields = {field["key"]: field for field in workflow["presentation_reply_fields"]}
    values = dict(phase="black", stimulus_id="White", display_id="Screen", clock_id="Client",
                  presented_at_seconds=0, source="client_draw_completed")
    assert fields.keys() == values.keys()
    for key, value in values.items():
        _validate_value(fields[key], value)
    assert PresentationStamp(**values).presented_at_seconds == 0
    for key, bad in (("clock_id", "é" * 65), ("phase", "gray"),
                     ("presented_at_seconds", -1), ("source", "optical_onset")):
        with pytest.raises(ValueError):
            _validate_value(fields[key], bad)
        with pytest.raises(ValueError):
            PresentationStamp(**(values | {key: bad}))
