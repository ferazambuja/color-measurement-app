"""Configured-mode queries scope modifiers/filters without a global capability union."""

import json
import time

import pytest
from conftest import native_display_unsupported

from color_measurement.argyll_capability import CAPABILITY3, MODE_FLAGS, mode_word
from color_measurement.instrument import Instrument, Measurement
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.operation_records import result_from_json


def request(front, mode, *, workflow="spot", options=None):
    descriptor = json.loads(front.describe_json())
    return json.dumps(dict(
        schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"], mode=mode, workflow=workflow,
        patch_ids=[] if workflow == "mode_snapshot" else ["software-only"],
        options=options or {}, layout={},
    ))


def observed_query(front, meter, monkeypatch, mode, *, primary, tertiary):
    queries = []

    def recorded(self, mode, *, configuration):
        assert self.helper == meter.helper and self.instrument == meter.instrument
        queries.append((mode, configuration))
        return Measurement(
            instrument=meter.instrument, serial=None, mode=mode, workflow="mode_snapshot",
            provenance="simulated", spectral=True, confirmed_routes=(),
            mode_snapshot=dict(mode_capabilities=primary, trigger_capabilities=0,
                               filter_capabilities=tertiary,
                               display_types=native_display_unsupported() | {"entries": []}),
        )

    monkeypatch.setattr(Instrument, "mode_snapshot", recorded)
    operation = front.prepare(request(front, mode, workflow="mode_snapshot"))
    operation.dispatch()
    try:
        deadline = time.monotonic() + 3
        while not operation.done:
            assert time.monotonic() < deadline
            time.sleep(0.002)
        result = result_from_json(operation.result_json())
        assert result.status == "completed" and result.patches == (), result.error
        assert len(queries) == 1
        assert "mode_snapshot_json" in dict(result.configuration)
    finally:
        operation.close()
    return json.loads(front.describe_json())


def test_mode_query_exposes_highres_only_for_its_configured_word_and_equivalent_names(monkeypatch):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *a, **k: pytest.fail("Native transport"),
    )
    meter = Instrument("/never-open", "instI1Pro3")
    primary = (mode_word("emissive_spot", spectral=True)
               | mode_word("reflective_spot", spectral=True))
    front = InstrumentFrontend(meter, mode_capabilities=primary, trigger_capabilities=0,
                               filter_capabilities=0, provenance="simulated")
    before = json.loads(front.describe_json())
    old = next(row for row in before["options"] if row["id"] == "highres")
    assert old["available"] is False
    with pytest.raises(ValueError):
        front.prepare(request(front, "emissive_spot", options={"highres": True}))
    descriptor = observed_query(front, meter, monkeypatch, "emissive_spot",
                                primary=primary | MODE_FLAGS["highres"], tertiary=0)
    highres = next(row for row in descriptor["options"] if row["id"] == "highres")
    assert descriptor["mode_capabilities"] == primary  # No global capability promotion.
    assert "highres" not in descriptor["mode_flags"]
    assert highres["available"] and "reflective_spot" not in highres["applies_to"]
    for entry in descriptor["modes"]:
        if entry["equivalent_to"] == "emissive_spot":
            assert highres["by_mode"][entry["mode"]]["available"] is True
    prepared = front.prepare(request(front, "emissive_spot", options={"highres": True}))
    assert json.loads(prepared.request_json)["options"]["highres"] is True
    prepared.cancel(prepared.operation_id)
    with pytest.raises(ValueError):
        front.prepare(request(front, "reflective_spot", options={"highres": True}))
    assert descriptor["capability_id"] != before["capability_id"]


def test_configured_tertiary_controls_averaging_without_enabling_other_modes(monkeypatch):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *a, **k: pytest.fail("Native transport"),
    )
    meter = Instrument("/never-open", "instI1Pro3")
    primary = (mode_word("emissive_spot", spectral=True)
               | mode_word("reflective_spot", spectral=True))
    front = InstrumentFrontend(meter, mode_capabilities=primary, trigger_capabilities=0,
                               filter_capabilities=0, provenance="simulated")
    descriptor = observed_query(front, meter, monkeypatch, "emissive_spot",
                                primary=primary, tertiary=CAPABILITY3["average"])
    averages = next(row for row in descriptor["options"] if row["id"] == "averages")
    assert descriptor["filter_capabilities"] == 0
    assert averages["by_mode"]["emissive_spot"]["available"] is True
    assert averages["by_mode"]["reflective_spot"]["available"] is False
    prepared = front.prepare(request(front, "emissive_spot", options={"averages": 2}))
    prepared.cancel(prepared.operation_id)
    with pytest.raises(ValueError):
        front.prepare(request(front, "reflective_spot", options={"averages": 2}))


def test_configured_filter_choices_use_the_selected_tertiary_word(monkeypatch):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *a, **k: pytest.fail("Native transport"),
    )
    meter = Instrument("/never-open", "instI1Pro3")
    primary = (mode_word("emissive_spot", spectral=True)
               | mode_word("reflective_spot", spectral=True))
    front = InstrumentFrontend(meter, mode_capabilities=primary, trigger_capabilities=0,
                               filter_capabilities=CAPABILITY3["filter_none"],
                               provenance="simulated")
    with pytest.raises(ValueError):
        front.prepare(request(front, "reflective_spot", options={"filter": "m1"}))
    descriptor = observed_query(
        front, meter, monkeypatch, "reflective_spot", primary=primary,
        tertiary=CAPABILITY3["filter_none"] | CAPABILITY3["filter_D50"],
    )
    option = next(row for row in descriptor["options"] if row["id"] == "filter")
    assert "m1" in option["by_mode"]["reflective_spot"]["choices"]
    assert option["by_mode"]["emissive_spot"]["available"] is False
    prepared = front.prepare(request(front, "reflective_spot", options={"filter": "m1"}))
    prepared.cancel(prepared.operation_id)
    assert descriptor["filter_capabilities"] == CAPABILITY3["filter_none"]


def test_configured_modifier_withdrawal_does_not_change_another_modes_candidate(monkeypatch):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *a, **k: pytest.fail("Native transport"),
    )
    meter = Instrument("/never-open", "instI1Pro3")
    primary = (mode_word("emissive_spot", spectral=True)
               | mode_word("reflective_spot", spectral=True))
    front = InstrumentFrontend(meter, mode_capabilities=primary, trigger_capabilities=0,
                               filter_capabilities=CAPABILITY3["average"], provenance="simulated")
    descriptor = observed_query(front, meter, monkeypatch, "emissive_spot",
                                primary=primary, tertiary=0)
    option = next(row for row in descriptor["options"] if row["id"] == "averages")
    assert option["by_mode"]["emissive_spot"]["available"] is False
    assert option["by_mode"]["reflective_spot"]["available"] is True
    assert option["by_mode"]["reflective_spot"]["capability_source"] == \
        "initialized_before_mode_configuration"
    with pytest.raises(ValueError):
        front.prepare(request(front, "emissive_spot", options={"averages": 2}))
