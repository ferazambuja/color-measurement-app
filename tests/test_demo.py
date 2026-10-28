"""Public equipment previews exercise the normal frontend with no native process."""

import hashlib
import json
import string
import threading
import time
from itertools import islice, product
from pathlib import Path

import pytest

from color_measurement import demo, instrument
from color_measurement.instrument_frontend import InstrumentFrontend, InstrumentOperation
from color_measurement.operation_exports import load, save
from color_measurement.operation_records import result_from_json

FIXTURES = Path(__file__).parent / "fixtures/generic_frontend"


@pytest.fixture(autouse=True)
def no_native_transport(monkeypatch):
    monkeypatch.setattr(instrument, "HelperSession",
                        lambda *a, **kw: pytest.fail("Demo attempted native I/O"))


def request(front, mode, workflow="spot", count=1, layout=None, options=None):
    descriptor = json.loads(front.describe_json())
    return json.dumps({"schema_version": 1, "device_id": descriptor["device_id"],
                       "capability_id": descriptor["capability_id"], "mode": mode,
                       "workflow": workflow, "patch_ids": [f"sample-{n}" for n in range(count)],
                       "options": options or {}, "layout": layout or {}})


def drive(front, text, *, cancel_at=None, timeout=10):
    operation = front.prepare(text)
    events = []
    try:
        operation.dispatch()
        deadline = time.monotonic() + timeout
        while not operation.done and time.monotonic() < deadline:
            raw = operation.poll_event_json()
            if raw:
                event = json.loads(raw)
                events.append(event)
                if event["kind"] == "instruction_required":
                    payload = event["payload"]
                    operation.respond(operation.operation_id, payload["request_id"],
                                      "cancel" if payload["kind"] == cancel_at else "accept")
                elif event["kind"] == "presentation_required":
                    payload = event["payload"]
                    time.sleep(payload["minimum_delay_ms"] / 1000.0 + .01)
                    operation.presented(operation.operation_id, payload["request_id"],
                        json.dumps({"phase": payload["phase"],
                            "stimulus_id": payload["stimulus_id"],
                            "display_id": payload["display_id"], "clock_id": "demo-client",
                            "presented_at_seconds": time.monotonic(),
                            "source": "client_draw_completed"}))
            if not raw:
                time.sleep(0.001)
        assert operation.done, "Demo did not terminate"
        # Completion and queue exhaustion are separate facts. Keep the final
        # notifications even when the producer finishes between client polls.
        while raw := operation.poll_event_json():
            events.append(json.loads(raw))
        return json.loads(operation.result_json()), events
    finally:
        operation.close()


def test_packaged_demo_facts_load_once_and_stay_pristine_across_frontends(monkeypatch):
    loader = demo._data
    if hasattr(loader, "cache_clear"):
        loader.cache_clear()
    reads = []
    read_text = Path.read_text

    def counted(path, *args, **kwargs):
        if path.name == "demo.json":
            reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    try:
        original = json.dumps(demo._data(), sort_keys=True)
        first, second = [next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
                         for _ in range(2)]
        descriptor = first.describe_json()
        assert descriptor == second.describe_json() == first.describe_json()
        readings = []
        for front, prompts in ((first, ["calibration_setup", "sample_position"]),
                               (first, []), (second, ["calibration_setup", "sample_position"])):
            result, events = drive(front, request(front, "reflective_spot"))
            assert result["status"] == "completed"
            assert [event["payload"]["kind"] for event in events
                    if event["kind"] == "instruction_required"] == prompts
            readings.append(result["patches"][0]["observation"]["backend_reported"])
        assert readings[0] == readings[2] and readings[0] != readings[1]
        assert json.dumps(demo._data(), sort_keys=True) == original
        assert len(reads) == 1, "Snapshot/setup polling must not reread packaged Demo facts"
    finally:
        if hasattr(loader, "cache_clear"):
            loader.cache_clear()


def test_public_catalog_is_distinct_bounded_and_entirely_synthetic():
    fronts = demo.demo_frontends()
    catalog = json.loads(demo.demo_catalog_json())
    # The picker offers every captured persona: inspecting and testing without
    # hardware needs all of them, so none is held back from the demo.
    captured = json.loads((FIXTURES / "index.json").read_text())["personas"]
    assert len(fronts) == len(catalog) == len(captured)
    assert {row["persona"] for row in catalog} == set(captured)
    # Offered means usable: an instrument whose captures all failed would appear in
    # the picker with nothing to measure.
    for front in fronts:
        modes = json.loads(front.describe_json())["modes"]
        assert any(mode["available"] for mode in modes), front._meter.persona
    assert all(isinstance(front, InstrumentFrontend) for front in fronts)
    descriptors = [json.loads(front.describe_json()) for front in fronts]
    assert len({d["mode_capabilities"] for d in descriptors}) > 4
    assert all(d["synthetic"] and not d["live"] and d["preview"]["unmodeled"]
               and d["device"]["backend"] == "argyll-demo" for d in descriptors)
    assert any(not entry["available"] for d in descriptors for entry in d["modes"])
    assert [{key: value for key, value in row.items()
             if key not in {"brand", "display_model"}} for row in catalog] == [
        {"persona": front._meter.persona, "device_id": descriptor["device_id"],
         "model": descriptor["device"]["model"],
         "label": f"{descriptor['device']['model']} (simulated)",
         "synthetic": True, "live": False, "preview": descriptor["preview"]}
        for front, descriptor in zip(fronts, descriptors, strict=True)]
    for row in catalog:
        assert isinstance(row["brand"], str) and row["brand"].strip() == row["brand"]
        assert row["brand"]
        assert isinstance(row["display_model"], str) and row["display_model"]
        assert row["display_model"].strip() == row["display_model"]
        if row["model"].startswith("GretagMacbeth "):
            assert row["brand"] == "X-Rite (GretagMacbeth)"
            assert row["display_model"] == row["model"].removeprefix("GretagMacbeth ")
        elif row["model"] == "SwatchMate Cube":
            assert (row["brand"], row["display_model"]) == ("Palette (SwatchMate)", "Cube")
        else:
            assert (row["brand"] + " " + row["display_model"] == row["model"]
                    or (row["brand"], row["display_model"], row["model"])
                    in {("HCFR", "Colorimeter", "Colorimtre HCFR"),
                        ("X-Rite", "ColorMunki Smile", "ColorMunki Smile")})


@pytest.mark.parametrize("persona, brand, display_model", [
    ("cr100", "Colorimetry Research", "CR-100"),
    ("cr250", "Colorimetry Research", "CR-250"),
    ("cr300", "Colorimetry Research", "CR-300"),
    ("pr655", "Photo Research", "PR-655"),
    ("pr670", "Photo Research", "PR-670"),
    ("ex1", "Image Engineering", "EX1"),
    ("hcfr", "HCFR", "Colorimeter"),
    ("colormunki_smile", "X-Rite", "ColorMunki Smile"),
    ("smcube", "Palette (SwatchMate)", "Cube"),
    ("i1pro", "X-Rite (GretagMacbeth)", "i1 Pro"),
    ("i1pro3", "X-Rite", "i1 Pro 3"),
    ("spyder2024", "Datacolor", "Spyder 2024"),
    ("i1_display_3", "X-Rite", "i1 DisplayPro, ColorMunki Display"),
])
def test_demo_catalog_picker_names_follow_canonical_identity(persona, brand, display_model):
    row = next(row for row in json.loads(demo.demo_catalog_json())
               if row["persona"] == persona)
    assert row["brand"] == brand
    assert row["display_model"] == display_model


def test_demo_acceptance_retains_recorded_simulated_facts_without_claiming_live_confirmation():
    for front in demo.demo_frontends():
        source = json.loads((FIXTURES / front._meter.persona / "describe.json").read_text())
        descriptor = json.loads(front.describe_json())
        assert descriptor["provenance"] == "simulated" and descriptor["live"] is False
        assert descriptor["baseline_mode_checks"] == source["baseline_mode_checks"]
        modes = {entry["mode"]: entry["baseline_native_mode"] for entry in source["modes"]}
        assert {entry["mode"]: entry["baseline_native_mode"]
                for entry in descriptor["modes"]} == modes


@pytest.mark.parametrize("front", demo.demo_frontends(), ids=lambda f: f._meter.persona)
def test_every_offered_canonical_route_runs_through_generic_operation(front):
    descriptor = json.loads(front.describe_json())
    for mode in descriptor["modes"]:
        if not mode["canonical"] or not mode["available"]:
            continue
        for workflow in mode["workflows"]:
            if not workflow["available"]:
                continue
            kind = workflow["workflow"]
            count = (0 if kind in {"calibrate", "mode_snapshot", "stored_refresh"}
                     else 1 if kind in {"spot", "refresh_frequency", "display_timing"} else 5)
            layout = descriptor["preview"]["example_layouts"][mode["mode"]].copy()
            if kind in {"calibrate", "mode_snapshot", "stored_refresh"}:
                layout = {}
            if kind == "refresh_frequency":
                layout = {"stimulus_id": "DEMO-WHITE",
                          "stimulus_description": "Synthetic steady display"}
            if kind == "display_timing":
                layout = {"stimulus_id": "DEMO-BLACK-WHITE", "display_id": "DEMO-DISPLAY",
                          "stimulus_description": "Controlled synthetic black/white exchange"}
            if kind == "xy":
                layout = {"passes": 1, "steps": count}
            if kind == "chart":
                layout = {"passes": 1, "steps": count, "sheets": [1], "chart_id": 99}
            result, _ = drive(front, request(front, mode["mode"], kind, count, layout))
            assert result["status"] == "completed", (mode["mode"], kind, result["error"])
            assert result["provenance"] == "simulated"
            assert result["device"]["serial"] == f"DEMO-{front._meter.persona}"
            assert result["device"]["serial_encoding"] == "utf8"
            assert len(result["patches"]) == count
            assert [p["patch_id"] for p in result["patches"]] == [
                f"sample-{n}" for n in range(count)]
            for patch in result["patches"]:
                observation = patch["observation"]
                assert observation["provenance"] == "simulated"
                assert observation["device"]["serial"] == result["device"]["serial"]
                assert observation["device"]["serial_encoding"] == "utf8"
                if kind == "refresh_frequency":
                    assert observation["record_kind"] == "scalar"
                    assert observation["backend_reported"]["unit"] == "Hz"
                    assert "spectrum" not in observation["backend_reported"]
                    continue
                if kind == "display_timing":
                    assert observation["record_kind"] == "timing"
                    assert observation["backend_reported"]["display_update_delay_ms"] == 37
                    assert observation["backend_reported"]["instrument_reaction_ms"] == -230
                    assert "spectrum" not in observation["backend_reported"]
                    continue
                assert (observation["backend_reported"]["spectrum"] is not None) == descriptor[
                    "spectral"]


@pytest.mark.parametrize("persona", ["i1pro2", "i1pro3", "colormunki"])
def test_repeated_demo_spot_only_returns_to_sample_after_calibration_setup(persona):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == persona)
    text = request(front, "reflective_spot")
    for expected in (["calibration_setup", "sample_position"], []):
        result, events = drive(front, text)
        assert result["status"] == "completed" and len(result["patches"]) == 1
        assert [e["payload"]["kind"] for e in events
                if e["kind"] == "instruction_required"] == expected


def test_demo_placed_colorimeter_reads_without_readiness_confirmation():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1_display_3")
    result, events = drive(front, request(front, "emissive_spot"))
    assert result["status"] == "completed" and len(result["patches"]) == 1
    assert not any(e["kind"] == "instruction_required" for e in events)


def test_demo_returns_to_sample_after_a_recorded_calibration_position_only(monkeypatch):
    """Exercise the generic control capture without adding a device-specific demo path."""
    data = json.loads(json.dumps(demo._data()))
    persona = data["personas"]["i1pro3"]
    capture = json.loads((FIXTURES / "i1pro3/scenario.calibration_position_only.json").read_text())
    raw = json.loads(capture["result"]["patches"][0]["observation"]["raw_backend_output"])["events"]
    route = dict(persona["routes"]["reflective_spot"])
    route["setup"] = [e for e in raw if e["event"] not in {
        "connected", "display_type", "result", "terminal"}]
    persona["misplaced"]["reflective_spot"] = route
    monkeypatch.setattr(demo, "_data", lambda: data)
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    result, events = drive(front, request(front, "reflective_spot",
                                         options={"demo_sensor_misplaced": True}))
    assert result["status"] == "completed" and len(result["patches"]) == 1
    assert [e["payload"]["kind"] for e in events
            if e["kind"] == "instruction_required"] == ["sensor_position", "sample_position"]


def test_distinct_calibration_trigger_cancel_and_repeated_reading():
    fronts = {f._meter.persona: f for f in demo.demo_frontends()}
    reflective = fronts["i1pro3"]
    text = request(reflective, "reflective_spot")
    cancelled, events = drive(reflective, text, cancel_at="calibration_setup")
    assert cancelled["status"] == "cancelled" and not cancelled["patches"]
    assert next(e["payload"]["calibration"]["action"] for e in events
                if e["kind"] == "instruction_required") == "present_reference"
    first, _ = drive(reflective, text)
    second, _ = drive(reflective, text)
    assert first["status"] == second["status"] == "completed"
    assert first["operation_id"] != second["operation_id"]
    result, events = drive(fronts["i1_display_3"], request(fronts["i1_display_3"],
                          "emissive_spot", "calibrate", 0))
    assert result["status"] == "completed"
    assert sum(e["kind"] == "instruction_required"
               and e["payload"]["calibration"]["action"] == "display_patch" for e in events) == 3
    front = fronts["dtp51"]
    layout = json.loads(front.describe_json())["preview"]["example_layouts"]["reflective_strip"]
    result, events = drive(front, request(front, "reflective_strip", "strip", 7, layout),
                           cancel_at="device_trigger")
    assert result["status"] == "cancelled" and not result["patches"]
    assert any(e["kind"] == "instruction_required" and e["payload"]["kind"] == "device_trigger"
               for e in events)


def test_controls_validate_and_record_simulated_setup_without_claiming_numerical_effects():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    for options in ({"highres": True}, {"averages": 2}):
        with pytest.raises(ValueError, match="does not advertise"):
            front.prepare(request(front, "reflective_spot", options=options))
    result, _ = drive(front, request(front, "reflective_spot", options={"filter": "m2"}))
    assert result["status"] == "completed"
    raw = json.loads(result["patches"][0]["observation"]["raw_backend_output"])
    assert next(e["backend_reported"]["mcond"] for e in raw["events"]
                if e["event"] == "result") == 3
    for name, mode, options in (
            ("i1pro2", "emissive_spot", {"highres": True}),
            ("i1pro2", "reflective_spot", {"xcalstd": "xrga"}),
            ("i1_display_3", "emissive_spot", {"display_type": "resolve-named-choice"})):
        front = next(f for f in demo.demo_frontends() if f._meter.persona == name)
        if "display_type" in options:
            options = {"display_type": front._display_types["entries"][1]["id"]}
        result, _ = drive(front, request(front, mode, options=options))
        assert result["status"] == "completed", result["error"]
        raw = json.loads(result["patches"][0]["observation"]["raw_backend_output"])
        connected = next(e for e in raw["events"] if e["event"] == "connected")
        assert connected["demo_numerical_effects_modeled"] is False
        flags = instrument.Configuration.from_options(
            options, display_types=front._display_types).flags(mode)
        assert connected["demo_configuration"] | flags == connected["demo_configuration"]
    with pytest.raises(ValueError, match="only to reflective"):
        front.prepare(request(front, "emissive_spot", options={"xcalstd": "xrga"}))


def test_a_mode_that_needs_a_correction_says_so_where_corrections_are_not_modeled():
    """The K-10's ambient reading needs an illuminance-scaled matrix, and the demo
    models no correction: its fixture was taken through a synthetic one, and
    replaying that without one would claim a corrected reading nobody asked for."""
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "klein_k10")
    modes = {entry["mode"]: entry for entry in json.loads(front.describe_json())["modes"]}
    ambient = modes["ambient_spot"]
    assert ambient["correction_required"] is True
    assert not ambient["available"] and "correction" in ambient["unavailable_reason"]
    assert modes["emissive_spot"]["available"]
    assert modes["emissive_spot"]["correction_matrix_diagonal_mean"] == {"at_most": 5.0}


def test_reverse_order_and_persistence_use_the_common_records(tmp_path):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    result, _ = drive(front, request(front, "reflective_strip", "strip", 7,
                                    {"direction": "reverse"}))
    assert result["status"] == "completed", result["error"]
    config = dict(result["patches"][0]["observation"]["configuration"])
    assert config["native_patch_index"] == "6"
    assert result["patches"][0]["patch_id"] == "sample-0"
    retained = result_from_json(json.dumps(result))
    path = save(retained, tmp_path / "demo.json").path
    assert load(path) == retained
    assert all(patch.observation.provenance == "simulated" for patch in retained.patches)


def test_large_demo_batch_does_not_spend_the_deadline_on_artificial_pacing(monkeypatch):
    # Check the pacing invariant directly. Coarsening empty-poll waits also
    # delays legitimate producer progress on a loaded runner. The deadline alone
    # admits one millisecond of pacing per event, so the Demo's own delivery is
    # watched on whichever thread runs it and checked after the run.
    sleep = time.sleep
    poll = InstrumentOperation.poll_event_json
    receive = demo._DemoSession.receive_message
    consumer = threading.get_ident()
    available, active_events = False, 0
    delivering, delivered, paced = threading.local(), [], []

    def observed_receive(session, *, timeout):
        delivering.active = True
        try:
            return receive(session, timeout=timeout)
        finally:
            delivering.active = False
            delivered.append(True)

    def observed_poll(operation):
        nonlocal available, active_events
        raw = poll(operation)
        if threading.get_ident() == consumer:
            available = bool(raw)
            active_events += bool(raw) and not operation.done
        return raw

    def unpaced_sleep(seconds):
        if getattr(delivering, "active", False):
            paced.append(seconds)
        if threading.get_ident() == consumer:
            assert not available, "Available Demo events must be drained without artificial pacing"
        sleep(seconds)

    monkeypatch.setattr(InstrumentOperation, "poll_event_json", observed_poll)
    monkeypatch.setattr(demo._DemoSession, "receive_message", observed_receive)
    monkeypatch.setattr(time, "sleep", unpaced_sleep)
    # DTP51's native command has a two-digit count, so a 3000-patch batch never
    # belonged to that persona. Use an assessed scanner with the application bound.
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro2")
    # Short, unique IDs keep this valid batch within the request byte limit.
    ids = ["".join(pair) for pair in islice(
        product(string.ascii_letters + string.digits, repeat=2), 3000)]
    payload = json.loads(request(front, "reflective_strip", "strip", count=0))
    payload["patch_ids"] = ids
    # This is a hang bound, not the pacing oracle above. A busy target runner
    # needs headroom to construct and validate all 3000 retained observations.
    result, events = drive(front, json.dumps(payload, separators=(",", ":")), timeout=60)
    assert active_events, "The pacing guard must observe events before producer completion"
    assert len(delivered) > 3000 and not paced, f"Demo delivery paced itself: {paced[:3]}"
    assert result["status"] == "completed", result["error"]
    assert [patch["patch_id"] for patch in result["patches"]] == ids
    assert result["provenance"] == "simulated"
    assert [event["sequence"] for event in events] == list(range(len(events)))
    assert all(len(json.dumps(event, ensure_ascii=True).encode()) <= 16384 for event in events)
    references = [event["payload"] for event in events if event["kind"] == "patch_reference"]
    assert [row["index"] for row in references] == list(range(3000))
    assert [row["patch_id"] for row in references] == ids
    # A reference names the retained observation it stands for; a client that
    # follows it must land on that record and no other.
    assert [row["observation_id"] for row in references] == [
        patch["observation"]["observation_id"] for patch in result["patches"]]
    assert len(set(row["observation_id"] for row in references)) == 3000
    assert events[-1]["kind"] == "terminal"
    assert events[-1]["payload"]["completed_patches"] == 3000
    assert "patches" not in events[-1]["payload"]


@pytest.mark.parametrize("passes,steps,locations", [(1, 5, 2), (2, 3, 3)])
def test_xy_pending_prompts_and_retained_locations_keep_fiducial_numbers(passes, steps, locations):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "spectroscan")
    result, events = drive(front, request(front, "reflective_xy", "xy", passes * steps,
                                          {"passes": passes, "steps": steps}))
    assert result["status"] == "completed", result["error"]
    prompts = [e["payload"]["native_event"] for e in events
               if e["kind"] == "instruction_required" and e["payload"]["kind"] == "xy_locate"]
    assert [(p["location_index"], p["location_count"]) for p in prompts] == [
        (i, locations) for i in range(locations)]
    raw = json.loads(result["patches"][0]["observation"]["raw_backend_output"])
    assert [e["index"] for e in raw["events"] if e["event"] == "xy_location"] == list(
        range(locations))


def test_device_button_pending_payload_preserves_unacknowledged_reference_context():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "dtp22")
    result, events = drive(front, request(front, "reflective_spot", "calibrate", 0))
    assert result["status"] == "completed", result["error"]
    notified = next(e["payload"] for e in events if e["kind"] == "instruction_notified")
    pending = next(e["payload"] for e in events if e["kind"] == "instruction_required")
    assert notified["calibration"]["acknowledged"] is False
    assert pending["kind"] == "device_trigger"
    assert pending["calibration"] == notified["calibration"]
    assert pending["calibration"]["reference_id"] == "22"
    assert pending["calibration"]["action"] == "click_reference"
    setup = pending["calibration"]["setup"]
    assert setup is not None
    assert "calibration holder" in setup["text"]
    assert setup["location"] == (
        "https://www.xrite.com/service-support/using_hyperterminal_to_calibrate_a_dtp22")
    assert pending["allowed_responses"] == ["accept", "cancel"]


def test_dtp20_remote_white_is_acknowledged_instead_of_clicked():
    """dtp20.c:1174-1187 takes acknowledgement before CR; DTP22 uses a switch.

    The old DTP20 calibration capture borrowed DTP22's condition, which made the
    demo ask for the wrong operator action while its model still said DTP20.
    """
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "dtp20")
    result, events = drive(front, request(front, "reflective_spot", "calibrate", 0))
    assert result["status"] == "completed"
    assert not any(e["kind"] == "instruction_notified" for e in events)
    prompts = [e["payload"] for e in events if e["kind"] == "instruction_required"]
    assert len(prompts) == 1
    assert prompts[0]["kind"] == "calibration_setup"
    assert prompts[0]["calibration"]["condition"] == "man_ref_white"
    assert prompts[0]["calibration"]["action"] == "present_reference"
    assert prompts[0]["calibration"]["acknowledged"] is True


def test_xyz_only_in_the_demo_leaves_the_recorded_spectrum_out():
    """The demo honours the XYZ-only option as the helper does: bound without the
    spectral bit and no spectrum in the reading, the same synthetic XYZ."""
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "cr250")
    descriptor = json.loads(front.describe_json())
    assert next(o for o in descriptor["options"] if o["id"] == "xyz_only")["available"]
    layout = descriptor["preview"]["example_layouts"]["emissive_spot"].copy()
    result, _ = drive(front, request(front, "emissive_spot", layout=layout,
                                     options={"xyz_only": True}))
    assert result["status"] == "completed"
    reported = result["patches"][0]["observation"]["backend_reported"]
    other = next(f for f in demo.demo_frontends() if f._meter.persona == "cr250")
    spectral, _ = drive(other, request(other, "emissive_spot", layout=layout))
    assert reported["spectrum"] is None
    assert reported["xyz"] == spectral["patches"][0]["observation"]["backend_reported"]["xyz"]


def test_a_measurement_calibrates_first_where_the_driver_does_at_startup():
    """No calibration requested: an i1Pro or ColorMunki still calibrates before its
    first reading, because its driver asks at every startup (chart_stub.c
    `startup`); a DTP41 asks nothing. What this catches: a demo that measures
    straight away on an instrument whose real driver would stop to calibrate."""
    fronts = {f._meter.persona: f for f in demo.demo_frontends()}

    def actions(persona, mode="reflective_spot"):
        result, events = drive(fronts[persona], request(fronts[persona], mode))
        assert result["status"] == "completed" and len(result["patches"]) == 1
        return [e["payload"]["calibration"]["action"] for e in events
                if e["kind"] == "instruction_required" and e["payload"].get("calibration")]

    assert actions("i1pro2") == ["present_reference"]       # the white tile
    assert actions("colormunki") == ["set_sensor_position"]  # the dial
    assert actions("dtp41") == []
    # The i1 Display at its default non-refresh display type has no refresh-rate
    # calibration at all (i1d3.c:3346-3355, :4030-4039), so no 80% patch.
    assert actions("i1_display_3", "emissive_spot") == []
    # Non-adaptive emission also sets its integration time on a full-white patch,
    # after the dial (munki_imp.c:1743-1749); adaptive emission does not.
    assert actions("colormunki", "emissive_spot_nonadaptive") == [
        "set_sensor_position", "display_patch"]
    assert actions("colormunki", "emissive_spot") == ["set_sensor_position"]
    # Its projector and ambient positions, which munki_convert_mode serves
    # (munki.c:694-730): the dial for the dark, and the white patch only where
    # the reading is non-adaptive.
    assert actions("colormunki", "ambient_spot") == ["set_sensor_position"]
    assert actions("colormunki", "emissive_tele_nonadaptive") == [
        "set_sensor_position", "display_patch"]
    setup = {m["mode"]: m["operator_setup"]
             for m in json.loads(fronts["colormunki"].describe_json())["modes"]}
    assert "position A" in setup["ambient_spot"]["text"]
    assert "position B" in setup["emissive_tele"]["text"]


def test_a_calibrated_mode_is_kept_where_its_driver_keeps_one():
    """A reading asks the driver to keep a calibration it restored (session.c
    cm_bind), so once calibrated an i1Pro 3 measures without its tile -- until its
    own limits expire it, which the demo does not model. What its driver still
    asks for is asked again, and the SpectroScan family, whose driver is not asked,
    calibrates at every reading. What this catches: a demo that asks for the tile
    before every reading, and one that stops asking where the driver would not."""
    def conditions(front, mode="reflective_spot", workflow="spot"):
        result, events = drive(front, request(front, mode, workflow,
                                              0 if workflow == "calibrate" else 1))
        assert result["status"] == "completed"
        return [e["payload"]["calibration"]["condition"] for e in events
                if e["kind"] == "instruction_required" and e["payload"].get("calibration")], result

    fronts = {f._meter.persona: f for f in demo.demo_frontends()}
    i1pro3 = fronts["i1pro3"]
    assert conditions(i1pro3)[0] == ["man_ref_white"]
    kept, result = conditions(i1pro3)
    assert kept == [] and result["calibration"]["status"] == "valid"
    assert result["calibration"]["detail"].startswith(
        "No calibration was performed in this operation. The driver was asked to keep")
    # Per mode, as the driver's state is: a mode not yet calibrated still asks.
    assert conditions(i1pro3, "emissive_spot")[0] == ["man_ref_white"]
    # A transmission white belongs to the setup, so only the tile goes.
    assert conditions(i1pro3, "transmission_spot")[0] == ["man_ref_white", "man_trans_white"]
    assert conditions(i1pro3, "transmission_spot")[0] == ["man_trans_white"]
    # Calibration on its own counts, and another preview starts afresh.
    other = {f._meter.persona: f for f in demo.demo_frontends()}["i1pro3"]
    assert conditions(other, "reflective_spot", "calibrate")[0]
    assert conditions(other)[0] == []
    spectrolino = fronts["spectrolino"]
    assert conditions(spectrolino)[0] == conditions(spectrolino)[0] == ["man_ref_white"]


def test_demo_spectra_are_varied_plausible_readings():
    """A strip shows different colours, each XYZ derived from its own curve: a
    reflective factor from 5 to 95 % plus bounded demo gain jitter."""
    fronts = {f._meter.persona: f for f in demo.demo_frontends()}
    front = fronts["i1pro2"]
    layout = json.loads(front.describe_json())["preview"]["example_layouts"]["reflective_strip"]
    result, _ = drive(front, request(front, "reflective_strip", "strip", 4, layout))
    assert result["status"] == "completed"
    readings = [p["observation"]["backend_reported"] for p in result["patches"]]
    curves = [tuple(r["spectrum"]["values"]) for r in readings]
    assert len(set(curves)) == len(curves) == 4
    for reading, curve in zip(readings, curves):
        assert 5 * .995 <= min(curve) and max(curve) <= 95 * 1.005
        peak = 380 + 10 * curve.index(max(curve))
        assert 0 < reading["xyz"]["values"][1] <= 100, "not a reflective reading"
        # A slope of i / 7 would peak at the grid's end, 730 nm, for every patch.
        assert peak != 730


@pytest.mark.parametrize("persona,mode", [
    ("i1pro3", "reflective_spot"), ("cr250", "emissive_spot"),
    ("cr100", "emissive_spot"), ("cr300", "emissive_spot"),
    ("pr655", "emissive_spot"), ("pr670", "emissive_spot"),
    ("specbos", "emissive_tele")])
def test_separate_spot_operations_advance_the_shared_palette_and_keep_bounded_jitter(persona, mode):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == persona)
    template = demo._data()["personas"][persona]["routes"][mode]["patches"][0]
    spectrum = template["backend_reported"]["spectrum"]
    grid = ([spectrum[k] for k in ("spec_n", "wavelength_start_nm", "wavelength_end_nm", "norm")]
            if spectrum else [36, 380, 730, 1])
    colours = demo._palette()["palettes"][":".join(str(int(v)) for v in grid)]
    readings = []
    for index in range(13):
        result, _ = drive(front, request(front, mode))
        assert result["status"] == "completed"
        observation = result["patches"][0]["observation"]
        assert observation["provenance"] == "simulated"
        assert "native_facts_json" not in dict(observation["configuration"])
        reported = observation["backend_reported"]
        xyz = reported["xyz"]["values"]
        base = colours[index % 12]
        gain = xyz[1] / base["XYZ"][1]
        assert .995 <= gain <= 1.005
        assert xyz == pytest.approx([v * gain for v in base["XYZ"]])
        if spectrum:
            actual = reported["spectrum"]
            assert len(actual["values"]) == spectrum["spec_n"]
            assert actual["wavelengths_nm"][0] == spectrum["wavelength_start_nm"]
            assert actual["wavelengths_nm"][-1] == spectrum["wavelength_end_nm"]
            assert actual["values"] == pytest.approx([v * gain for v in base["samples"]])
            assert min(actual["values"]) >= 0
        else:
            assert reported["spectrum"] is None
        readings.append(xyz)
    assert len({tuple(xyz) for xyz in readings}) == 13
    # Same shape on the next cycle, different small gain, not a frozen replay.
    assert readings[12] != readings[0]


def test_demo_sample_counter_skips_calibration_queries_and_cancelled_readings():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    cancelled, _ = drive(front, request(front, "reflective_spot"), cancel_at="calibration_setup")
    assert cancelled["status"] == "cancelled" and front._meter.samples.index == 0
    for workflow in ("calibrate", "mode_snapshot"):
        result, _ = drive(front, request(front, "reflective_spot", workflow, 0))
        assert result["status"] == "completed" and front._meter.samples.index == 0
    result, _ = drive(front, request(front, "reflective_spot"))
    assert result["status"] == "completed" and front._meter.samples.index == 1
    other = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    baseline, _ = drive(other, request(other, "reflective_spot"))
    assert result["patches"][0]["observation"]["backend_reported"] == (
        baseline["patches"][0]["observation"]["backend_reported"])


@pytest.mark.parametrize("persona,mode", [
    ("i1pro3", "reflective_spot"), ("i1pro2", "reflective_spot"),
    ("colormunki", "reflective_spot"), ("smcube", "reflective_spot"),
    ("cr250", "emissive_spot")])
def test_guided_demo_sampling_keeps_the_cell_profile_and_explicit_attempt(persona, mode):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == persona)
    rows = []
    for cell, attempt in ((1, 1), (8, 1), (1, 2), (8, 1)):
        context = {"cell_index": cell, "attempt": attempt}
        payload = json.loads(request(front, mode, options={"demo_chart_sample": context}))
        payload["patch_ids"] = ["A1 renamed by the operator"]
        result, _ = drive(front, json.dumps(payload))
        assert result["status"] == "completed", result["error"]
        assert result["patches"][0]["patch_id"] == payload["patch_ids"][0]
        assert json.loads(dict(result["configuration"])["requested_options_json"]) == {
            "demo_chart_sample": context}
        observation = result["patches"][0]["observation"]
        assert observation["provenance"] == "simulated"
        raw = json.loads(observation["raw_backend_output"])
        connected = next(event for event in raw["events"] if event["event"] == "connected")
        assert "demo_chart_sample" not in connected["demo_configuration"]
        sample = next(event["demo_sample"] for event in raw["events"] if "demo_sample" in event)
        assert sample["chart_sample"] == context
        assert sample["profile"] == demo._palette()["names"][cell - 1]
        assert type(sample["seed"]) is int and sample["seed"] > 0
        assert .995 <= sample["gain"] <= 1.005
        reported = observation["backend_reported"]
        spectrum = reported["spectrum"]
        template = demo._data()["personas"][persona]["routes"][mode]["patches"][0]
        grid = template["backend_reported"]["spectrum"]
        key = (f'{grid["spec_n"]}:{grid["wavelength_start_nm"]}:'
               f'{grid["wavelength_end_nm"]}:{grid["norm"]}') if grid else (
                   "36:380:730:1" if mode.startswith("emissive") else "36:380:730:100")
        base = demo._palette()["palettes"][key][cell - 1]
        assert reported["xyz"]["values"] == pytest.approx(
            [value * sample["gain"] for value in base["XYZ"]])
        if spectrum:
            assert spectrum["values"] == pytest.approx(
                [value * sample["gain"] for value in base["samples"]])
        rows.append((sample, reported))
    assert rows[0][0]["profile"] == rows[2][0]["profile"]
    assert rows[0][0]["gain"] != rows[2][0]["gain"]
    assert rows[1] == rows[3]  # Same explicit input, regardless of read order.
    assert front._meter.samples.index == 0  # Guided reads do not consume free samples.
    for expected in range(2):
        result, _ = drive(front, request(front, mode))
        observation = result["patches"][0]["observation"]
        raw = json.loads(observation["raw_backend_output"])
        sample = next(event["demo_sample"] for event in raw["events"] if "demo_sample" in event)
        assert sample["profile"] == demo._palette()["names"][expected]
        assert "chart_sample" not in sample
    assert front._meter.samples.index == 2


@pytest.mark.parametrize("sample", [
    False, True, "A1", [], {}, {"cell_index": 1},
    {"cell_index": 1, "attempt": 1, "extra": 0},
    {"cell_index": 0, "attempt": 1}, {"cell_index": 141, "attempt": 1},
    {"cell_index": True, "attempt": 1}, {"cell_index": 1.0, "attempt": 1},
    {"cell_index": 1, "attempt": 0}, {"cell_index": 1, "attempt": -1},
    {"cell_index": 1, "attempt": True}, {"cell_index": 1, "attempt": 1.5},
    {"cell_index": 1, "attempt": float("inf")},
    {"cell_index": 1, "attempt": 2**53},
    {"cell_index": 1, "attempt": 10**4299},
])
def test_bad_guided_demo_context_is_refused_before_opening(sample, monkeypatch):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    monkeypatch.setattr(demo._DemoInstrument, "_open_session",
                        lambda *a, **kw: pytest.fail("Invalid context opened a demo session"))
    with pytest.raises(ValueError, match="integer|Nonfinite"):
        front.prepare(request(front, "reflective_spot", options={"demo_chart_sample": sample}))
    assert front._meter.samples.index == 0


@pytest.mark.parametrize("workflow,count", [("calibrate", 0), ("mode_snapshot", 0), ("spot", 2)])
def test_guided_demo_context_requires_one_spot_reading(workflow, count):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    with pytest.raises(ValueError, match="one-reading spot|1 to 1 unique patch IDs"):
        front.prepare(request(front, "reflective_spot", workflow, count,
                            options={"demo_chart_sample": {"cell_index": 1, "attempt": 1}}))


def test_guided_demo_context_accepts_last_cell_and_retains_canonical_records(tmp_path):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    context = {"cell_index": 140, "attempt": 3}
    option = next(o for o in json.loads(front.describe_json())["options"]
                  if o["id"] == "demo_chart_sample")
    assert option["type"] == "object" and option["default"] is None
    assert option["available"] and "reflective_spot" in option["applies_to"]
    result, _ = drive(front, request(front, "reflective_spot",
                                    options={"demo_chart_sample": context}))
    retained = result_from_json(json.dumps(result))
    saved = save(retained, tmp_path / "guided-demo.json")
    assert load(saved.path) == retained
    assert json.loads(retained.request_json)["options"]["demo_chart_sample"] == context
    raw = json.loads(retained.patches[0].observation.raw_backend_output)
    sample = next(e["demo_sample"] for e in raw["events"] if "demo_sample" in e)
    assert sample["profile"] == demo._palette()["names"][7]
    assert sample["chart_sample"] == context and sample["seed"] == 420


def test_guided_demo_context_accepts_largest_exact_client_attempt():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    context = {"cell_index": 140, "attempt": 2**53 - 1}
    result, _ = drive(front, request(front, "reflective_spot",
                                    options={"demo_chart_sample": context}))
    assert result["status"] == "completed", result["error"]
    assert json.loads(result["request_json"])["options"]["demo_chart_sample"] == context
    raw = json.loads(result["patches"][0]["observation"]["raw_backend_output"])
    sample = next(e["demo_sample"] for e in raw["events"] if "demo_sample" in e)
    assert sample["chart_sample"] == context
    assert sample["seed"] == 140 * context["attempt"]
    assert .995 <= sample["gain"] <= 1.005


def test_demo_option_removal_preserves_original_json_boundaries():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    text = request(front, "reflective_spot", options={
        "demo_chart_sample": {"cell_index": 1, "attempt": 1}})
    with pytest.raises(ValueError, match="size limit"):
        front.prepare(text + " " * 16384)
    with pytest.raises(ValueError, match="Duplicate"):
        front.prepare(text.replace('"cell_index": 1', '"cell_index": 1, "cell_index": 2'))


def test_guided_sample_option_is_demo_only_and_null_keeps_free_samples():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    ordinary = InstrumentFrontend(front._meter, **demo._data()["personas"]["i1pro3"][
        "capabilities"], provenance="simulated")
    with pytest.raises(ValueError, match="Unknown option"):
        ordinary.prepare(request(ordinary, "reflective_spot", options={
            "demo_chart_sample": {"cell_index": 1, "attempt": 1}}))
    result, _ = drive(front, request(front, "reflective_spot",
                                    options={"demo_chart_sample": None}))
    assert result["status"] == "completed" and front._meter.samples.index == 1
    raw = json.loads(result["patches"][0]["observation"]["raw_backend_output"])
    sample = next(e["demo_sample"] for e in raw["events"] if "demo_sample" in e)
    assert "chart_sample" not in sample and sample["profile"] == demo._palette()["names"][0]


@pytest.mark.parametrize("options", [
    {}, {"demo_chart_sample": None}, {"demo_sensor_misplaced": False},
    {"demo_chart_sample": None, "demo_sensor_misplaced": False},
])
def test_inert_demo_defaults_retain_mode_snapshot_on_original_frontend(options):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    before = json.loads(front.describe_json())
    result, _ = drive(front, request(front, "emissive_spot", "mode_snapshot", 0,
                                    options=options))
    assert result["status"] == "completed", result["error"]
    after = json.loads(front.describe_json())
    facts = json.loads(dict(result["configuration"])["mode_snapshot_json"])
    assert facts["capability_id"] == after["capability_id"] != before["capability_id"]
    mode = next(entry for entry in after["modes"] if entry["mode"] == "emissive_spot")
    assert mode["capability_snapshot"]["source"] == "mode_snapshot"
    assert facts["requested_options"] == options
    assert front._meter.samples.index == 0
    # The retained query identifier must be usable by the next ordinary read.
    result, _ = drive(front, request(front, "emissive_spot"))
    assert result["status"] == "completed" and front._meter.samples.index == 1


def test_packaged_palette_binds_the_existing_calculator_and_captured_colours():
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/native_argyll/chart_stub.c").read_text()
    start = source.index("static const double shapes[12][3][3]")
    calculator = source[start:source.index("\nstatic void fill(", start)]
    palette = demo._palette()
    assert hashlib.sha256(calculator.encode()).hexdigest() == palette["calculator_sha256"]
    assert len(palette["names"]) == 12
    for colours in palette["palettes"].values():
        assert len(colours) == 12
    # Independently retained native captures already cover all twelve standard
    # grid colours; generated base XYZ must agree with those same curves.
    matched = set()
    for persona in demo._data()["personas"].values():
        for route in persona["routes"].values():
            for patch in route["patches"]:
                reported = patch["backend_reported"]
                spectrum = reported["spectrum"]
                if not spectrum or spectrum["spec_n"] != 36 or spectrum["norm"] != 1:
                    continue
                for index, colour in enumerate(palette["palettes"]["36:380:730:1"]):
                    if spectrum["samples"] == pytest.approx(colour["samples"]):
                        assert reported["XYZ"] == pytest.approx(colour["XYZ"])
                        matched.add(index)
                        break
    assert matched == set(range(12))


def test_the_packaged_demo_data_is_derived_from_the_committed_fixtures():
    """demo.json ships in the package and is derived from the published fixtures.
    Fixtures regenerated without re-deriving it would ship a stale demo that no
    other check reads: the fixture --check stops at tests/fixtures. Pure Python,
    no worker, so it runs in every gate."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    checked = subprocess.run([sys.executable, str(root / "tools/generate_demo_data.py"), "--check"],
                             cwd=root, capture_output=True, text=True, timeout=120)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_a_recorded_wrong_position_is_replayed_where_one_was_recorded():
    """The demo's own option replays a run recorded with the substituted
    instrument's sensor, dial or adapter in the wrong place, so the new prompt can
    be seen without hardware. It is offered only on the modes a run was recorded
    for, and nothing is invented for any other: what this catches is a demo that
    never shows the prompt, or one that makes up a position for a route never run."""
    fronts = {f._meter.persona: f for f in demo.demo_frontends()}

    def option(persona):
        return next(o for o in json.loads(fronts[persona].describe_json())["options"]
                    if o["id"] == "demo_sensor_misplaced")

    assert option("colormunki")["applies_to"] == ["emissive_tele", "transmission_spot"]
    assert option("i1_display_3")["applies_to"] == ["ambient_spot"]
    assert option("i1pro3")["applies_to"] == ["ambient_spot", "reflective_spot"]
    assert not option("dtp41")["available"] and option("dtp41")["applies_to"] is None

    def positions(persona, mode, misplaced):
        front = fronts[persona]
        result, events = drive(front, request(
            front, mode, options={"demo_sensor_misplaced": True} if misplaced else None))
        assert result["status"] == "completed" and len(result["patches"]) == 1
        return result, [(e["payload"]["position"]["needed"], e["payload"]["position"]["stage"])
                        for e in events if e["kind"] == "instruction_required"
                        and e["payload"]["kind"] == "sensor_position"]

    result, asked = positions("colormunki", "emissive_tele", True)
    assert asked == [("projector", "read_sample")]
    assert json.loads(dict(result["configuration"])["requested_options_json"]) == {
        "demo_sensor_misplaced": True}
    assert positions("colormunki", "transmission_spot", True)[1] == [("surface", "calibrate")]
    assert positions("i1pro3", "ambient_spot", True)[1] == [
        ("calibration", "calibrate"), ("ambient", "read_sample")]
    assert positions("i1_display_3", "ambient_spot", True)[1] == [("ambient", "read_sample")]
    assert positions("colormunki", "emissive_tele", False)[1] == []   # off by default

    # applies_to names modes, so the option holds for calibration on its own too:
    # it replays the calibration's recorded wrong position where the run met one
    # there, and calibrates as usual where the run met it only while reading.
    def calibrating(persona, mode):
        front = fronts[persona]
        result, events = drive(front, request(front, mode, "calibrate", 0,
                                              options={"demo_sensor_misplaced": True}))
        assert result["status"] == "completed" and result["patches"] == []
        return [(e["payload"]["position"]["needed"], e["payload"]["position"]["stage"])
                for e in events if e["kind"] == "instruction_required"
                and e["payload"]["kind"] == "sensor_position"]

    assert calibrating("colormunki", "transmission_spot") == [("surface", "calibrate")]
    assert calibrating("i1pro3", "reflective_spot") == [("calibration", "calibrate")]
    assert calibrating("i1pro3", "ambient_spot") == [("calibration", "calibrate")]
    assert calibrating("colormunki", "emissive_tele") == []
    for persona, mode, value in (("colormunki", "reflective_spot", True),   # not recorded
                                 ("dtp41", "reflective_spot", True),        # no run at all
                                 ("colormunki", "emissive_tele", None),
                                 ("colormunki", "emissive_tele", "yes")):
        with pytest.raises(ValueError):
            fronts[persona].prepare(request(fronts[persona], mode,
                                          options={"demo_sensor_misplaced": value}))
