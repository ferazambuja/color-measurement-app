"""Actual Demo intents, retained chart outcomes and portable source admission."""

import base64
import hashlib
import json
import threading
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_application import (
    _command,
    _configure_chart,
    _demo_batch,
    _wait,
)
from test_application import (
    app as _app_fixture,
)
from test_application import no_native_transport as no_native_transport

from color_measurement import application, chart_context, chart_state, chart_targets, ti3_exports
from color_measurement.application import BatchApplication, review_saved

app = _app_fixture


def _configure(owner, columns=2):
    outcome = _configure_chart(owner, chart_state.definition(
        "custom", name="Software-only chart", rows=1, columns=columns,
    ))
    assert outcome["admission"] == "admitted", outcome


def _measure(owner, identity=None):
    state = owner.snapshot()
    outcome = _command(owner, "measure_chart", dict(
        context_id=state["context_id"], draft_id=state["draft"]["id"],
        draft_version=state["draft"]["version"], chart_revision=state["chart"]["revision"],
    ), identity=identity)
    assert outcome["admission"] == "admitted", outcome
    _wait(owner, lambda state: state["phase"] == "idle", respond=True)
    return owner.snapshot()


def _edit(owner, action, **operands):
    return _command(owner, "chart_edit", dict(
        chart_revision=owner.snapshot()["chart"]["revision"], action=action, operands=operands,
    ))


def _stage(owner, definition, **overrides):
    snapshot = owner.snapshot()
    binding = {key: snapshot[key] for key in ("batch_id", "generation", "context_id")}
    return owner.stage_chart_definition(definition, **dict(binding, **overrides))


def _staged_payload(staged):
    return {key: staged[key] for key in ("definition_token", "mapping_confirmation")}


def _continuation_acknowledgements(context):
    return [key for key, fact in context["material"].items()
            if (key in chart_context._DECLARABLE
                or key.startswith(("effective_option:", "native_setting:")))
            and fact["certainty"] not in {"unknown", "requested"}]


def test_actual_demo_continue_preserves_original_selected_reading(app, tmp_path):
    _configure(app)
    _measure(app)
    _wait(app, lambda state: not state["companions"]["saving"])
    source = review_saved(app._folder)
    source_pass = source["manifest"]["chart"]["passes"][0]
    original = (app._folder / "batch.json").read_bytes()
    owner = _demo_batch(tmp_path / "actual-demo-continue")
    owner.bind()
    try:
        outcome = _command(owner, "continue_chart", dict(
            folder=str(app._folder), source_sha256=source["manifest_sha256"], action="continue",
            source_pass_id=source_pass["id"],
            acknowledged_material=_continuation_acknowledgements(source_pass["context"]),
        ))
        assert outcome["admission"] == "admitted", outcome
        assert owner.snapshot()["chart"]["coverage"] == 1
        assert owner.snapshot()["chart"]["pass_count"] == 1
        assert owner._data["chart"]["passes"][0]["selected_attempts"] == \
            source_pass["selected_attempts"]
        reading, = owner.chart_readings()
        assert reading["reading_ref"]["segment_id"] == source["manifest"]["batch_id"]
        assert (app._folder / "batch.json").read_bytes() == original
    finally:
        _wait(owner, lambda state: not state["companions"]["saving"])
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("changed_firmware", [False, True])
def test_current_description_binding_and_original_native_facts_drive_actual_continue(
    tmp_path, monkeypatch, changed_firmware,
):
    from test_discovery import current_cr_build, current_cr_identity

    from color_measurement.argyll_capability import baseline_mode_words, mode_word
    from color_measurement.instrument import Instrument, Measurement, Patch

    # Reuse the existing typed Measurement substitution seam. Description/binding
    # and operation/observation persistence remain their actual owners. No worker runs.
    def measured(meter, mode, **kwargs):
        identity = current_cr_identity()
        return Measurement(
            instrument=meter.instrument, serial="CR-0001", serial_encoding="utf8", mode=mode,
            workflow="spot", provenance="simulated", spectral=False,
            confirmed_routes=(mode,), patches=(Patch(1, 0, (20.0, 21.0, 22.0), None, 0.0),),
            calibration=dict(remaining_calibrations=0, performed=False, status_source="driver"),
            native_facts=dict(driver=identity["driver"], identity=identity["identity"],
                              capture=dict(valid=True, xyz=[20.0, 21.0, 22.0], spectrum=None)),
            worker_stop="observed_exit",
        )

    monkeypatch.setattr(Instrument, "measure", measured)
    owners = []
    try:
        for name, change in (("native-source", False), ("native-current", changed_firmware)):
            build, client = current_cr_build()
            primary = mode_word("emissive_spot", spectral=False)
            client._describe["capabilities"]["primary"] = primary
            client._describe["baseline_mode_checks"] = dict(
                scope="initialized_before_mode_configuration", mode_capabilities=primary,
                checks=[dict(mode_word=word, native_code=0)
                        for word in baseline_mode_words(primary)],
            )
            if change:
                client._describe["native_identity"]["identity"].update(
                    firmware="1.27", firmware_components=[1, 27],
                )
            description = build.describe(build.attached()[0])
            frontend = build.bind(description)
            owner = BatchApplication(tmp_path / name, frontend=frontend, mode="emissive_spot",
                                     options={})
            owner.bind()
            owners.append(owner)
            assert client.enumerations == len(client.describes) == 1
            packet = owner.snapshot()["descriptor"]["current_binding_identity"]
            assert packet["native_identity"] == description.native_identity
            if name == "native-source":
                _configure(owner)
                _measure(owner)
                _wait(owner, lambda state: not state["companions"]["saving"])
        parent, current = owners
        source = review_saved(parent._folder)
        source_pass = source["manifest"]["chart"]["passes"][0]
        result = application.result_from_json(application._json(
            source["operations"][0]["result"],
        ))
        assert result.patches, result
        observation = result.patches[0].observation
        native = dict(observation.configuration)["native_facts_json"]
        assert json.loads(native)["identity"]["firmware"] == "1.26"
        before = (current._folder / "batch.json").read_bytes()
        payload = dict(folder=str(parent._folder), source_sha256=source["manifest_sha256"],
                       action="continue", source_pass_id=source_pass["id"],
                       acknowledged_material=_continuation_acknowledgements(source_pass["context"]))
        outcome = _command(current, "continue_chart", payload)
        assert outcome["admission"] == ("refused" if changed_firmware else "admitted"), outcome
        if changed_firmware:
            assert current.snapshot()["chart"] is None
            assert (current._folder / "batch.json").read_bytes() == before
        else:
            assert current.snapshot()["chart"]["coverage"] == 1
            reading, = current.chart_readings()
            assert dict(reading["observation"].configuration)["native_facts_json"] == native
    finally:
        for owner in owners:
            _wait(owner, lambda state: not state["companions"]["saving"])
            _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("field,target", [("apertureId", 2), ("cycles", 4)])
def test_original_pr_capture_setup_changes_guard_chart_continue_and_reference(
    tmp_path, monkeypatch, field, target,
):
    from test_native_controls import native_frontend, native_readback_seam

    from color_measurement.chart_science import _material_compatible

    front = native_frontend()
    native = native_readback_seam(monkeypatch, front)
    owner = BatchApplication(tmp_path / field, frontend=front, mode="emissive_spot", options={})
    try:
        owner.bind()
        _configure(owner)
        _measure(owner)
        _wait(owner, lambda state: not state["companions"]["saving"])
        source_bytes = (owner._folder / "batch.json").read_bytes()
        original = deepcopy(owner._data["chart"])
        first, = owner.chart_readings()
        before = dict(first["observation"].configuration)["native_facts_json"]
        assert _material_compatible(first, first)
        native["current"][field] = target
        _measure(owner)
        _wait(owner, lambda state: not state["companions"]["saving"])
        changed = owner._data["chart"]
        assert changed["pending_context"] is not None
        key = "aperture" if field == "apertureId" else "cycles"
        differences = changed["pending_context"]["differences"]
        assert any(row["key"] == f"native_setting:setup_after:{key}" for row in differences)
        assert owner.snapshot()["context_current"]
        # The changed attempt remains retained outside the earlier compatible selection.
        last = changed["passes"][0]["attempts"][-1]
        current = owner._chart_wrapper(last)
        assert not _material_compatible(first, current)
        current_context = dict(current["accepted_context"], phase="intended")
        with pytest.raises(ValueError, match="context|material"):
            chart_state.continue_state(
                original, current_context, segment_id=str(uuid4()), source_id=str(owner._folder),
                source_bytes=source_bytes, source_sha256=hashlib.sha256(source_bytes).hexdigest(),
                action="continue", pass_id=original["active_pass"],
            )
        assert dict(first["observation"].configuration)["native_facts_json"] == before
        assert owner.chart_readings()[0]["observation"] == first["observation"]
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("field,target", [(None, None), ("apertureId", 2), ("cycles", 4)])
def test_actual_continue_checks_current_pr_readback_before_another_exposure(
    tmp_path, monkeypatch, field, target,
):
    from test_native_controls import controls_request, native_frontend, native_readback_seam

    source_front = native_frontend()
    native_readback_seam(monkeypatch, source_front)
    source_owner = BatchApplication(tmp_path / "source", frontend=source_front,
                                    mode="emissive_spot", options={})
    current = None
    try:
        source_owner.bind()
        _configure(source_owner)
        _measure(source_owner)
        _wait(source_owner, lambda state: not state["companions"]["saving"])
        source = review_saved(source_owner._folder)
        source_pass = source["manifest"]["chart"]["passes"][0]
        source_bytes = (source_owner._folder / "batch.json").read_bytes()
        front = native_frontend()
        native = native_readback_seam(monkeypatch, front)
        if field is not None:
            native["current"][field] = target
        operation = front.prepare(controls_request(front))
        operation.dispatch()
        # Use the real held frontend operation before creating the required empty batch.
        deadline = time.monotonic() + 3
        while not operation.done:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert json.loads(operation.result_json())["status"] == "completed"
        current = BatchApplication(tmp_path / "current", frontend=front,
                                   mode="emissive_spot", options={})
        current.bind()
        before = (current._folder / "batch.json").read_bytes()
        outcome = _command(current, "continue_chart", dict(
            folder=str(source_owner._folder), source_sha256=source["manifest_sha256"],
            action="continue", source_pass_id=source_pass["id"],
            acknowledged_material=_continuation_acknowledgements(source_pass["context"]),
        ))
        assert outcome["admission"] == ("admitted" if field is None else "refused"), outcome
        assert native["measurements"] == 0
        if field is not None:
            assert "conflicts" in outcome["reason"]
            assert current.snapshot()["chart"] is None
            assert (current._folder / "batch.json").read_bytes() == before
        assert (source_owner._folder / "batch.json").read_bytes() == source_bytes
    finally:
        for owner in (current, source_owner):
            if owner is not None:
                _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def test_accepted_pr_context_uses_original_readback_and_never_later_cache(tmp_path, monkeypatch):
    from dataclasses import replace

    from test_native_controls import controls_request, native_frontend, native_readback_seam

    front = native_frontend()
    native = native_readback_seam(monkeypatch, front)
    owner = BatchApplication(tmp_path / "original", frontend=front,
                             mode="emissive_spot", options={})
    try:
        owner.bind()
        _configure(owner)
        _measure(owner)
        _wait(owner, lambda state: not state["companions"]["saving"])
        attempt = owner._data["chart"]["passes"][0]["attempts"][0]
        result = owner._retained[attempt["command_id"]]
        observation = result.patches[0].observation
        original = dict(observation.configuration)["native_facts_json"]
        native["current"]["cycles"] = 9
        query = front.prepare(controls_request(front))
        query.dispatch()
        deadline = time.monotonic() + 3
        while not query.done:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        descriptor = json.loads(front.describe_json())
        request = json.loads(result.request_json)
        intent = chart_context.intended(descriptor, request)
        key = "native_setting:setup_after:cycles"
        assert intent["material"][key]["value"]["value"] == 9
        acquired = chart_context.accepted(descriptor, request, result)
        assert acquired["material"][key]["value"]["value"] == 1
        for missing in ("setup_after", "native_facts_json"):
            configuration = dict(observation.configuration)
            if missing == "native_facts_json":
                del configuration[missing]
            else:
                facts = json.loads(original)
                del facts["capture"][missing]
                configuration["native_facts_json"] = json.dumps(facts)
            changed = replace(observation, configuration=tuple(sorted(configuration.items())))
            retained = replace(result, patches=(replace(result.patches[0], observation=changed),))
            absent = chart_context.accepted(descriptor, request, retained)
            assert key not in absent["material"]
        assert dict(observation.configuration)["native_facts_json"] == original
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def test_direct_unknown_calibration_refuses_continue_and_allows_new_pass(tmp_path):
    from test_desktop_direct import connect
    from test_serial_instruments import ScriptedPort, acquisition_script, settings_script, startup

    from color_measurement.desktop_session import DesktopSession

    session = DesktopSession([])
    port = ScriptedPort(startup("CR-100") + settings_script("CR-100") * 2
                        + acquisition_script("CR-100"))
    connected, _ = connect(session, port)
    assert connected["state"] == "completed", connected
    frontend = session._selections[connected["result"]["selection_id"]]["frontend"]
    owners = []
    try:
        for name in ("direct-source", "direct-current"):
            owner = BatchApplication(tmp_path / name, frontend=frontend, mode="emissive_spot",
                                     options=dict(automatic=False))
            owner.bind()
            owners.append(owner)
            if name == "direct-source":
                _configure(owner)
                _measure(owner)
                _wait(owner, lambda state: not state["companions"]["saving"])
        parent, current = owners
        source = review_saved(parent._folder)
        source_pass = source["manifest"]["chart"]["passes"][0]
        assert source_pass["context"]["material"]["calibration"]["certainty"] == "unknown"
        before = (current._folder / "batch.json").read_bytes()
        payload = dict(folder=str(parent._folder), source_sha256=source["manifest_sha256"],
                       action="continue", source_pass_id=source_pass["id"],
                       acknowledged_material=_continuation_acknowledgements(source_pass["context"]))
        refused = _command(current, "continue_chart", payload)
        assert refused["admission"] == "refused" and "calibration" in refused["reason"], refused
        assert "new" in refused["reason"].lower() and "pass" in refused["reason"].lower()
        assert current.snapshot()["chart"] is None
        assert (current._folder / "batch.json").read_bytes() == before
        admitted = _command(current, "continue_chart", dict(
            payload, action="new-pass", acknowledged_material=[],
        ))
        assert admitted["admission"] == "admitted", admitted
        assert current.snapshot()["chart"]["pass_count"] == 2
        reading, = current.chart_readings(source_pass["id"])
        assert reading["observation"].calibration.status == "unknown"
    finally:
        for owner in owners:
            _wait(owner, lambda state: not state["companions"]["saving"])
            _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))
        assert not session.close_direct_sources()["failures"]
        assert not port.is_open and not port.script


def test_chart_staging_preserves_exact_one_mib_ti2_outside_command_envelope(app, tmp_path):
    fixture = Path(__file__).parent / "fixtures/targets/asymmetric-six-patch.ti2"
    original = fixture.read_bytes()
    padding = chart_targets.MAX_SOURCE_BYTES - len(original)
    original += b"#" + b"x" * (padding - 2) + b"\n"
    path = tmp_path / "maximum.ti2"
    path.write_bytes(original)
    target = chart_targets.import_ti2_json(str(path))
    definition = chart_state.definition("ti2", target_json=target, strips_as="columns",
                                        mapping_confirmed=True)
    assert len(original) == 1024 * 1024
    assert len(application._json(definition).encode("ascii")) > application._MAX_COMMAND_BYTES
    staged = _stage(app, definition)
    assert staged["source"] == dict(filename=path.name, byte_count=len(original),
                                    sha256=hashlib.sha256(original).hexdigest())
    assert (staged["preview"]["rows"], staged["preview"]["columns"]) == (3, 2)
    assert staged["preview"]["cells"][0]["source_id"] == "007"
    assert staged["preview"]["cells"][0]["source_location"] == "01A"
    for packet in (staged, _staged_payload(staged)):
        encoded = application._json(packet).encode("ascii")
        assert len(encoded) < application._MAX_COMMAND_BYTES
        assert b"target_json" not in encoded and b"source_base64" not in encoded
    assert _command(app, "configure_chart", _staged_payload(staged))["admission"] == "admitted"
    stored = review_saved(app._folder)["manifest"]["chart"]["definition"]
    assert stored == definition
    retained = json.loads(stored["target_json"])
    assert retained["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert base64.b64decode(retained["source_base64"]) == path.read_bytes() == original


def test_chart_staging_is_immutable_and_keeps_existing_cell_bound(app):
    definition = chart_state.definition("custom", name="x" * 128, rows=10, columns=14)
    original = deepcopy(definition)
    staged = _stage(app, definition)
    assert len(staged["preview"]["cells"]) == 140
    assert len(application._json(staged).encode("ascii")) < application._MAX_COMMAND_BYTES
    payload = _staged_payload(staged)
    definition["cells"][0]["source_location"] = "changed input"
    staged["preview"]["cells"][0]["source_location"] = "changed preview"
    staged["preview"]["name"] = "changed preview name"
    invalid = dict(original, rows=11)
    with pytest.raises(ValueError, match="140 cells"):
        _stage(app, invalid)
    assert app.snapshot()["chart"] is None
    assert _command(app, "configure_chart", payload)["admission"] == "admitted"
    for invalid in (False, 0, ""):
        with pytest.raises(ValueError):
            app.chart_readings(invalid)
    assert app.chart_readings(None) == app.chart_readings() == []
    assert app._data["chart"]["definition"] == original


def test_chart_staging_replaces_one_token_and_refuses_stale_bindings_before_effect(
    app, monkeypatch,
):
    definition = chart_state.definition("classic")
    before = (app._folder / "batch.json").read_bytes()
    prepared = []
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: prepared.append(args))
    first, replacement = _stage(app, definition), _stage(app, definition)
    assert first["definition_token"] != replacement["definition_token"]
    refused = _command(app, "configure_chart", _staged_payload(first))
    assert refused["admission"] == "refused" and "replaced" in refused["reason"]
    previous_binding = {key: replacement[key] for key in ("batch_id", "generation", "context_id")}
    app.bind()
    with pytest.raises(ValueError, match="Stale chart definition binding"):
        app.stage_chart_definition(definition, **previous_binding)
    for override in (dict(batch_id=str(uuid4())), dict(context_id=str(uuid4())),
                     dict(generation=True)):
        with pytest.raises(ValueError, match="Stale chart definition binding"):
            _stage(app, definition, **override)
    assert _command(app, "configure_chart", _staged_payload(replacement))["admission"] == "refused"
    assert prepared == [] and app.snapshot()["chart"] is None
    assert (app._folder / "batch.json").read_bytes() == before


def test_chart_staging_validation_releases_mutex_and_rechecks_binding(app, monkeypatch):
    definition = chart_state.definition("classic")
    before = (app._folder / "batch.json").read_bytes()
    entered, release, rebound = threading.Event(), threading.Event(), threading.Event()
    validate = chart_state.validate_definition
    errors = []

    def blocked(value):
        entered.set()
        assert release.wait(5), "Validation race was not released"
        return validate(value)

    def stage():
        try:
            _stage(app, definition)
        except BaseException as error:
            errors.append(error)

    def bind():
        app.bind()
        rebound.set()

    monkeypatch.setattr(chart_state, "validate_definition", blocked)
    worker, binder = threading.Thread(target=stage), threading.Thread(target=bind)
    worker.start()
    try:
        assert entered.wait(5), "Staging did not reach validation"
        binder.start()
        assert rebound.wait(5), "Definition validation held the application mutex"
    finally:
        release.set()
        worker.join(5)
        if binder.ident is not None:
            binder.join(5)
    assert not worker.is_alive() and not binder.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ValueError)
    assert str(errors[0]) == "Stale chart definition binding"
    assert app._staged_chart_definition is None and app.snapshot()["chart"] is None
    assert (app._folder / "batch.json").read_bytes() == before


def test_chart_staging_requires_exact_orientation_and_preserves_command_uuid_replay(
    app, monkeypatch, tmp_path,
):
    path = tmp_path / "one-cell.ti2"
    path.write_text('CTI2\nCOLOR_REP "RGB"\nPASSES_IN_STRIPS2 1\nSTEPS_IN_PASS 1\n'
                    'NUMBER_OF_FIELDS 5\nBEGIN_DATA_FORMAT\n'
                    'SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B\nEND_DATA_FORMAT\n'
                    'NUMBER_OF_SETS 1\nBEGIN_DATA\n"original" "A1" 1 2 3\nEND_DATA\n')
    definition = chart_state.definition(
        "ti2", target_json=chart_targets.import_ti2_json(str(path)), strips_as="rows",
        mapping_confirmed=True,
    )
    staged = _stage(app, definition)
    payload = _staged_payload(staged)
    confirmation = payload["mapping_confirmation"]
    assert confirmation == dict(definition_id=definition["id"], strips_as="rows", rows=1, columns=1)
    before = (app._folder / "batch.json").read_bytes()
    prepared = []
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: prepared.append(args))
    for wrong in (None, dict(confirmation, strips_as="columns"),
                  dict(confirmation, rows=True), dict(confirmation, columns=1.0),
                  dict(confirmation, definition_id="0" * 64)):
        refused = _command(app, "configure_chart", dict(payload, mapping_confirmation=wrong))
        assert refused["admission"] == "refused" and "exact mapping" in refused["reason"]
    assert prepared == [] and (app._folder / "batch.json").read_bytes() == before
    identity = str(uuid4())
    admitted = _command(app, "configure_chart", payload, identity=identity)
    assert admitted["admission"] == "admitted" and app._staged_chart_definition is None
    saved = (app._folder / "batch.json").read_bytes()
    assert _command(app, "configure_chart", payload, identity=identity) == admitted
    app.bind()
    assert _command(app, "configure_chart", payload, identity=identity) == admitted
    assert (app._folder / "batch.json").read_bytes() == saved and prepared == []


def test_chart_real_dispatch_intent_duplicate_and_explicit_finish(app):
    _configure(app)
    identity = str(uuid4())
    before = app.snapshot()
    payload = dict(
        context_id=before["context_id"], draft_id=before["draft"]["id"],
        draft_version=before["draft"]["version"], chart_revision=before["chart"]["revision"],
    )
    admitted = _command(app, "measure_chart", payload, identity=identity)
    assert admitted["admission"] == "admitted"
    reserved = json.loads((app._folder / "batch.json").read_text())
    attempt = reserved["chart"]["passes"][0]["attempts"][0]
    assert attempt["operation_id"] == reserved["operations"][0]["operation_id"]
    assert attempt["cell"] == 1 and attempt["request"] == reserved["operations"][0]["request"]
    assert _edit(app, "rotate", degrees=90)["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle", respond=True)
    assert _command(app, "measure_chart", payload, identity=identity)["state"] == "completed"
    current = app.snapshot()
    assert current["chart"]["coverage"] == 1 and current["chart"]["current_cell"] == 2
    assert current["accepted_count"] == 1 and not current["can_measure"]
    assert _command(app, "finish")["admission"] == "refused"
    refused = _command(app, "finish_chart", dict(
        chart_revision=current["chart"]["revision"], allow_incomplete=False,
    ))
    assert refused["admission"] == "refused"
    assert _command(app, "finish_chart", dict(
        chart_revision=current["chart"]["revision"], allow_incomplete=True,
    ))["admission"] == "admitted"
    review = review_saved(app._folder)
    assert review["manifest"]["schema_version"] == 5 and review["chart"]["available"]
    assert review["manifest"]["chart"]["finished"]


def test_cancelled_retry_keeps_original_selected_success_and_native_receipt(app, monkeypatch):
    _configure(app, 1)
    _measure(app)
    selected = app.snapshot()["chart"]["cells"][0]["selected_attempt"]
    assert _edit(app, "select_cell", cell=1)["admission"] in {"admitted", "no_change"}
    prepare = app._frontend.prepare

    def cancelled(request):
        operation = prepare(request)
        operation.cancel(operation.operation_id)
        return operation

    monkeypatch.setattr(app._frontend, "prepare", cancelled)
    state = app.snapshot()
    admitted = _command(app, "measure_chart", dict(
        context_id=state["context_id"], draft_id=state["draft"]["id"],
        draft_version=state["draft"]["version"], chart_revision=state["chart"]["revision"],
    ))
    assert admitted["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle")
    state = app.snapshot()
    assert state["chart"]["cells"][0]["selected_attempt"] == selected
    assert state["chart"]["coverage"] == 1 and state["accepted_count"] == 2
    review = review_saved(app._folder)
    assert review["chart"]["available"]
    attempts = review["manifest"]["chart"]["passes"][0]["attempts"]
    assert attempts[-1]["status"] == "cancelled"
    assert review["operations"][-1]["result"]["status"] == "cancelled"


def test_chart_pause_and_stale_revision_refuse_before_prepare(app, monkeypatch):
    _configure(app)
    revision = app.snapshot()["chart"]["revision"]
    assert _edit(app, "set_paused", paused=True)["admission"] == "admitted"
    calls = []
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: calls.append(args))
    state = app.snapshot()
    for expected in (revision, state["chart"]["revision"]):
        refused = _command(app, "measure_chart", dict(
            context_id=state["context_id"], draft_id=state["draft"]["id"],
            draft_version=state["draft"]["version"], chart_revision=expected,
        ))
        assert refused["admission"] == "refused"
    assert calls == [] and app.snapshot()["accepted_count"] == 0


def test_chart_control_before_intent_retains_cancelled_handle_and_cell(app, monkeypatch):
    _configure(app)
    original = app._intent_entry
    control = KeyboardInterrupt("Control before chart intent construction")
    calls = []

    def interrupt_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise control
        return original(*args)

    monkeypatch.setattr(app, "_intent_entry", interrupt_once)
    state = app.snapshot()
    with pytest.raises(KeyboardInterrupt) as caught:
        app.command(str(uuid4()), state["generation"], "measure_chart", dict(
            context_id=state["context_id"], draft_id=state["draft"]["id"],
            draft_version=state["draft"]["version"], chart_revision=state["chart"]["revision"],
        ), batch_id=state["batch_id"])
    assert caught.value is control
    final = app.snapshot()
    assert final["phase"] == "idle" and final["accepted_count"] == 1
    assert final["chart"]["coverage"] == 0
    reviewed = review_saved(app._folder)
    attempt = reviewed["manifest"]["chart"]["passes"][0]["attempts"][0]
    assert attempt["cell"] == 1 and attempt["result_status"] == "cancelled"
    assert reviewed["chart"]["available"]


def test_chart_original_context_tamper_is_not_accepted_by_saved_review(app):
    _configure(app, 1)
    _measure(app)
    manifest_path = app._folder / "batch.json"
    manifest = json.loads(manifest_path.read_text())
    attempt = manifest["chart"]["passes"][0]["attempts"][0]
    attempt["accepted_context"]["material"]["observer"]["value"] = "forged observer"
    manifest["chart"]["passes"][0]["context"]["material"]["observer"]["value"] = (
        "forged observer"
    )
    # Rehash the mutable chart packet: the original operation must remain authority.
    terminal = {key: attempt[key] for key in (
        "result_status", "result_reason", "status", "reason", "readings",
        "accepted_context", "position_check",
    )}
    attempt["terminal_sha256"] = hashlib.sha256(json.dumps(
        terminal, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    manifest_path.write_text(application._json(manifest))
    review = review_saved(app._folder)
    assert not review["chart"]["available"]
    assert "original native evidence" in review["chart"]["reason"]


def test_chart_continuation_new_pass_is_portable_and_source_tamper_refused(app, tmp_path):
    _configure(app, 1)
    _measure(app)
    source = review_saved(app._folder)
    owner = _demo_batch(tmp_path / "continued")
    owner.bind()
    try:
        outcome = _command(owner, "continue_chart", dict(
            folder=str(app._folder), source_sha256=source["manifest_sha256"], action="new-pass",
            source_pass_id=source["manifest"]["chart"]["active_pass"], acknowledged_material=[],
        ))
        assert outcome["admission"] == "admitted", outcome
        state = owner.snapshot()
        assert state["chart"]["coverage"] == 0 and state["chart"]["pass_count"] == 2
        assert state["chart"]["source_count"] == 1
        stored = owner._folder / f"source-{source['manifest_sha256']}.json"
        assert stored.read_bytes() == (app._folder / "batch.json").read_bytes()
        # Review needs no original-folder files once admission has retained them.
        (app._folder / "batch.json").unlink()
        reviewed = review_saved(owner._folder)
        assert reviewed["chart"]["available"] and len(reviewed["inherited_operations"]) == 1
        inherited = reviewed["manifest"]["chart"]["passes"][0]["attempts"][0]
        original = source["manifest"]["chart"]["passes"][0]["attempts"][0]
        assert inherited == original
        stored.write_bytes(b"tampered")
        rejected = review_saved(owner._folder)
        assert not rejected["chart"]["available"]
        assert rejected["inherited_operations"] == []
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def test_chart_numbering_refuses_before_preparing_unretainable_reading(app, monkeypatch):
    _configure(app)
    draft = app.snapshot()["draft"]
    name = "x" * 1020 + " #98"
    assert _command(app, "edit_draft", dict(
        draft_id=draft["id"], version=draft["version"], name=name, notes="",
        options=draft["options"], number_samples=True,
    ))["admission"] == "admitted"
    _measure(app)
    calls = []
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: calls.append(args))
    state = app.snapshot()
    assert state["save_reason"] is None and state["saved_count"] == 1
    assert state["draft"]["id"] != draft["id"]
    assert state["draft"]["name"] == name and state["draft"]["number_samples"]
    assert state["draft"]["notes"] == ""
    saved = review_saved(app._folder)
    operation, = saved["operations"]
    assert operation["review_state"] == "accepted"
    original = (app._folder / operation["path"]).read_bytes()
    refused = _command(app, "measure_chart", dict(
        context_id=state["context_id"], draft_id=state["draft"]["id"],
        draft_version=state["draft"]["version"], chart_revision=state["chart"]["revision"],
    ))
    assert refused["admission"] == "refused" and calls == []
    assert "Next sample label exceeds 1,024 characters" in refused["reason"]
    assert app.snapshot()["accepted_count"] == 1 and app.snapshot()["chart"]["coverage"] == 1
    assert review_saved(app._folder)["operations"][0]["review_state"] == "accepted"
    assert (app._folder / operation["path"]).read_bytes() == original


def test_chart_position_check_rehash_does_not_authenticate_forged_science(app):
    _configure(app, 1)
    _measure(app)
    path = app._folder / "batch.json"
    manifest = json.loads(path.read_text())
    attempt = manifest["chart"]["passes"][0]["attempts"][0]
    attempt["position_check"]["reason"] = "Forged advisory"
    attempt["terminal_sha256"] = chart_state._digest(chart_state._terminal_facts(attempt))
    path.write_text(application._json(manifest))
    review = review_saved(app._folder)
    assert not review["chart"]["available"]
    assert "original native/reference evidence" in review["chart"]["reason"]


def test_chart_pass_cannot_demote_original_native_material_to_unknown(app):
    _configure(app, 1)
    _measure(app)
    path = app._folder / "batch.json"
    manifest = json.loads(path.read_text())
    baseline = manifest["chart"]["passes"][0]["context"]
    baseline["material"]["xyz_unit"].update(value=None, certainty="unknown")
    path.write_text(application._json(manifest))
    review = review_saved(app._folder)
    assert not review["chart"]["available"] and "baseline" in review["chart"]["reason"]


def test_chart_saved_review_refuses_selected_native_material_conflict(app, monkeypatch):
    _configure(app)
    _measure(app)
    samples = app._frontend._meter.samples
    apply_sample = samples.apply

    def changed_native_condition(patch, mode, chart_sample=None):
        apply_sample(patch, mode, chart_sample)
        # Substitute only the Demo's native event, before the real parser and dispatch.
        patch["backend_reported"]["mcond"] = 1

    monkeypatch.setattr(samples, "apply", changed_native_condition)
    live = _measure(app)
    assert live["chart"]["coverage"] == 1 and live["chart"]["pending_context"] is not None
    original = review_saved(app._folder)
    assert original["chart"]["available"]
    assert len(original["operations"]) == 2
    assert all(item["review_state"] == "accepted" for item in original["operations"])
    path = app._folder / "batch.json"
    manifest = json.loads(path.read_text())
    chart = manifest["chart"]
    chart_pass = chart["passes"][0]
    first, second = chart_pass["attempts"]
    assert first["status"] == "completed" and second["status"] == "unresolved"
    assert first["result_status"] == second["result_status"] == "completed"
    first_fact = first["accepted_context"]["material"]["native_mcond"]
    second_fact = second["accepted_context"]["material"]["native_mcond"]
    assert first_fact["certainty"] == second_fact["certainty"] == "readback"
    assert first_fact["value"] == 0 and second_fact["value"] == 1
    contexts = [application._json(item["accepted_context"]) for item in (first, second)]
    native_bytes = {
        entry["path"]: (app._folder / entry["path"]).read_bytes()
        for entry in manifest["operations"]
    }
    # Reclassify and select the originals without changing their native evidence.
    second.update(status="completed", reason=second["result_reason"])
    second["terminal_sha256"] = chart_state._digest(chart_state._terminal_facts(second))
    chart_pass["selected_attempts"] = [first["id"], second["id"]]
    chart_pass["context"] = second["accepted_context"]
    chart["pending_context"] = None
    chart_state.validate_state(chart)
    path.write_text(application._json(manifest))
    review = review_saved(app._folder)
    assert all(item["review_state"] == "accepted" for item in review["operations"])
    assert contexts == [application._json(item["accepted_context"]) for item in (first, second)]
    assert native_bytes == {
        name: (app._folder / name).read_bytes() for name in native_bytes
    }
    assert not review["chart"]["available"], review["chart"]
    assert "material" in review["chart"]["reason"]


def test_chart_saved_review_keeps_original_unknowns_during_native_enrichment(app, monkeypatch):
    _configure(app)
    samples = app._frontend._meter.samples
    apply_sample = samples.apply

    def spectrum_without_xyz(patch, mode, chart_sample=None):
        apply_sample(patch, mode, chart_sample)
        patch["backend_reported"].update(XYZ_v=0, XYZ=None)

    monkeypatch.setattr(samples, "apply", spectrum_without_xyz)
    _measure(app)
    first_review = review_saved(app._folder)
    assert first_review["chart"]["available"]
    first_context = first_review["manifest"]["chart"]["passes"][0]["attempts"][0][
        "accepted_context"
    ]
    assert first_context["material"]["xyz_unit"]["certainty"] == "unknown"
    first_operation = first_review["operations"][0]["result"]
    assert first_operation["patches"][0]["observation"]["backend_reported"]["xyz"] is None
    monkeypatch.setattr(samples, "apply", apply_sample)
    live = _measure(app)
    assert live["chart"]["coverage"] == 2 and live["chart"]["pending_context"] is None
    review = review_saved(app._folder)
    assert review["chart"]["available"], review["chart"]
    chart_pass = review["manifest"]["chart"]["passes"][0]
    first, second = chart_pass["attempts"]
    assert chart_pass["selected_attempts"] == [first["id"], second["id"]]
    assert first["accepted_context"] == first_context
    assert second["accepted_context"]["material"]["xyz_unit"]["certainty"] == "readback"
    assert chart_state.context_difference(first_context, second["accepted_context"]) == []
    assert review["operations"][0]["result"] == first_operation
    assert review["operations"][1]["result"]["patches"][0]["observation"]["backend_reported"][
        "xyz"
    ] is not None


def test_chart_unsaved_abandonment_snapshot_keeps_only_compact_chart(app, monkeypatch):
    _configure(app, 1)
    _measure(app)
    _wait(app, lambda state: not state["companions"]["saving"])
    publish = application._publish_owned_file

    def fail_manifest(path, *args):
        if path.name == "batch.json":
            raise OSError("Deliberate chart metadata publication refusal")
        return publish(path, *args)

    monkeypatch.setattr(application, "_publish_owned_file", fail_manifest)
    assert _edit(app, "rotate", degrees=90)["state"] == "failed"
    # Failure is reported before the canonical writer releases its guard.
    # Confirm only after that writer and optional publication have settled.
    _wait(app, lambda state: state["phase"] == "idle" and not state["companions"]["saving"])
    summary = app.discard_summary()
    assert summary["chart"]["passes"][0]["attempts"]
    outcome = _command(app, "abandon", dict(
        discard_token=summary["token"], reason="Explicit software-only discard fixture",
    ))
    assert outcome["admission"] == "admitted", outcome
    compact = app.snapshot()["shutdown"]["discarded"]["chart"]
    assert compact["coverage"] == 1 and "passes" not in compact
    assert "request" not in json.dumps(compact)
    assert app._data["shutdown"]["discarded"]["chart"] == summary["chart"]


def test_chart_ti3_selected_inheritance_is_outside_current_acquisitions(app, tmp_path, monkeypatch):
    _configure(app, 1)
    _measure(app)
    source = review_saved(app._folder)
    owner = _demo_batch(tmp_path / "continued-export")
    owner.bind()
    observed = []
    update = ti3_exports.update_json

    def inspect_snapshot(text, *args):
        observed.append(json.loads(text))
        return update(text, *args)

    monkeypatch.setattr(ti3_exports, "update_json", inspect_snapshot)
    try:
        assert _command(owner, "continue_chart", dict(
            folder=str(app._folder), source_sha256=source["manifest_sha256"], action="new-pass",
            source_pass_id=source["manifest"]["chart"]["active_pass"], acknowledged_material=[],
        ))["admission"] == "admitted"
        assert _command(owner, "export_ti3")["admission"] == "admitted"
        packet = observed[-1]
        assert packet["ordinary_record_ids"] == [] and len(packet["records"]) == 1
        assert len(packet["charts"]) == 2 and len(packet["charts"][0]["rows"]) == 1
        assert packet["charts"][1]["rows"] == []
        original = source["operations"][0]["result"]["patches"][0]["observation"]
        row = packet["records"][0]
        assert row["context"]["provenance"]["observation_id"] == original["observation_id"]
        assert row["context"]["provenance"]["source_segment_id"] == source["manifest"]["batch_id"]
        assert packet["charts"][0]["rows"][0]["record_id"] == row["id"]
        assert row["xyz"]["values"] == original["backend_reported"]["xyz"]["values"]
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))



def test_chart_readings_carry_their_canonical_cell_in_live_and_saved_rows(app):
    """Table rows name chart readings by coordinate, live and in saved review."""
    from color_measurement.dataset_binding import DatasetBinding

    outcome = _configure_chart(app, chart_state.definition("classic"))
    assert outcome["admission"] == "admitted", outcome
    _measure(app)
    _measure(app)

    def cells(binding):
        return sorted((row["chart_cell"]["coordinate"], row["chart_cell"]["patch_name"])
                      for row in binding.data()["readings"].values())

    live = cells(DatasetBinding(owner=app))
    assert live == [("A1", "Dark skin"), ("B1", "Light skin")]
    _wait(app, lambda state: not state["companions"]["saving"])
    assert cells(DatasetBinding(folder=app._folder)) == live
