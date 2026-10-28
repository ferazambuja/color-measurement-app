"""Native-held correction selection and ordinary batch forwarding; no device I/O."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from test_colorimeter_correction import ccmx, ccss
from test_desktop_session import send, wait

from color_measurement.argyll_capability import CAPABILITY2, mode_word
from color_measurement.colorimeter_correction import correction_material_json
from color_measurement.desktop_session import DesktopSession
from color_measurement.instrument import Configuration, Instrument
from color_measurement.instrument_frontend import InstrumentFrontend


@pytest.fixture
def selected(tmp_path):
    helper = tmp_path / "inert-worker"
    helper.write_bytes(b"Inert worker fixture; never executed")
    frontend = InstrumentFrontend(
        Instrument(helper, "X-Rite i1 DisplayPro, ColorMunki Display",
                   expected_helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest()),
        mode_capabilities=mode_word("emissive_spot", spectral=False),
        trigger_capabilities=CAPABILITY2["ccmx"] | CAPABILITY2["ccss"],
        filter_capabilities=0, provenance="simulated",
    )
    session = DesktopSession([frontend])
    selection = session.settings(None)["selections"][0]["selection_id"]
    settings = session.settings(selection)
    scope = dict(selection_id=selection, mode="emissive_spot",
                 capability_id=settings["descriptor"]["capability_id"],
                 settings_generation=settings["settings_generation"])
    path = tmp_path / "selected.ccmx"
    path.write_text(ccmx())
    return session, frontend, path, scope


def current_scope(session):
    state = session.snapshot()["batch"]
    return dict(batch_id=state["batch_id"], generation=state["generation"],
                context_id=state["context_id"], draft_id=state["draft"]["id"],
                version=state["draft"]["version"])


def edit_payload(session, correction):
    draft = session.snapshot()["batch"]["draft"]
    return dict(draft_id=draft["id"], version=draft["version"], name=draft["name"],
                notes=draft["notes"], number_samples=draft["number_samples"],
                options=dict(draft["options"], correction=correction))


def test_native_correction_capture_create_retains_original_material_without_renderer_paths(
    selected, tmp_path,
):
    session, _, path, scope = selected
    staged = session.stage_correction(path, **scope, options={})
    raw = path.read_bytes()
    assert staged["material"]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert staged["material"]["material_sha256"] == hashlib.sha256(
        correction_material_json(raw.decode()).encode()).hexdigest()
    assert str(path) not in json.dumps(staged)
    packet = session.create(tmp_path / "batch", "Study", "", selection_id=scope["selection_id"],
                            mode=scope["mode"], options=dict(correction=staged["correction"]))
    assert set(packet["batch"]["draft"]["options"]["correction"]) == {"token"}
    assert packet["correction_material"] == staged["material"]
    actual = session.batch.snapshot()["draft"]["options"]["correction"]
    assert actual["text"].encode() == raw and actual["source"]["path"] == str(path)
    Configuration.from_options(dict(correction=actual))
    manifest = json.loads((tmp_path / "batch" / "batch.json").read_bytes())
    assert manifest["draft"]["options"]["correction"] == actual
    assert session.snapshot() == session.snapshot()  # Stable token under unchanged settings.
    assert send(session, "finish")["admission"] == "admitted"


@pytest.mark.parametrize("changed", ["bytes", "mode", "generation", "selection", "descriptor"])
def test_correction_token_refuses_changed_source_or_setup_before_batch_creation(
    selected, tmp_path, changed,
):
    session, frontend, path, scope = selected
    staged = session.stage_correction(path, **scope, options={})
    selection, mode = scope["selection_id"], scope["mode"]
    if changed == "bytes":
        path.write_bytes(path.read_bytes() + b"\n")
    elif changed == "mode":
        mode = "ambient_spot"
    elif changed == "generation":
        session._settings_generation += 1
    elif changed == "selection":
        selection = str(uuid4())
    else:
        frontend._caps = (frontend._caps[0], frontend._caps[1] & ~CAPABILITY2["ccss"],
                          frontend._caps[2])
    with pytest.raises(ValueError):
        session.create(tmp_path / "refused", "Study", "", selection_id=selection, mode=mode,
                       options=dict(correction=staged["correction"]))
    assert not (tmp_path / "refused").exists()


def test_current_correction_apply_clear_and_uuid_replay_share_existing_batch_registry(
    selected, tmp_path,
):
    session, _, path, scope = selected
    session.create(tmp_path / "batch", "Study", "", selection_id=scope["selection_id"],
                   mode=scope["mode"], options={})
    staged = session.stage_current_correction(path, **current_scope(session), options={})
    payload = edit_payload(session, staged["correction"])
    before = session.snapshot()["batch"]
    command_id = str(uuid4())
    first = send(session, "edit_draft", payload, identity=command_id, snapshot=before)
    assert first["admission"] == "admitted"
    wait(session, lambda state: state["phase"] == "idle")
    wait(session, lambda state: not state["companions"]["saving"])
    accepted = session.snapshot()
    path.write_bytes(path.read_bytes() + b"\n")
    replay = send(session, "edit_draft", payload, identity=command_id, snapshot=before)
    assert replay == session.batch.command_outcome(command_id)
    assert session.snapshot() == accepted
    with pytest.raises(ValueError, match="source bytes changed"):
        send(session, "edit_draft", edit_payload(session,
             accepted["batch"]["draft"]["options"]["correction"]))
    cleared = send(session, "edit_draft", edit_payload(session, None))
    assert cleared["admission"] == "admitted"
    wait(session, lambda state: state["phase"] == "idle")
    assert session.batch.snapshot()["draft"]["options"]["correction"] is None
    assert session.snapshot()["correction_material"] is None
    assert send(session, "finish")["admission"] == "admitted"


def test_current_correction_stale_draft_and_raw_renderer_material_refuse(selected, tmp_path):
    session, _, path, scope = selected
    session.create(tmp_path / "batch", "Study", "", selection_id=scope["selection_id"],
                   mode=scope["mode"], options={})
    staged = session.stage_current_correction(path, **current_scope(session), options={})
    old = edit_payload(session, staged["correction"])
    changed = edit_payload(session, None)
    changed["name"] = "New draft"
    assert send(session, "edit_draft", changed)["admission"] == "admitted"
    wait(session, lambda state: state["phase"] == "idle")
    with pytest.raises(ValueError, match="current settings changed"):
        send(session, "edit_draft", old)
    for raw in [dict(path=str(path)), dict(text=ccmx()), dict(text=ccmx(), source={})]:
        with pytest.raises(ValueError, match="native file dialog"):
            send(session, "edit_draft", edit_payload(session, raw))
    with pytest.raises(ValueError, match="Unknown correction"):
        send(session, "edit_draft", edit_payload(session, dict(token=str(uuid4()))))
    with pytest.raises(ValueError, match="Unknown desktop method"):
        session.request("stage_current_correction", dict(path=str(path)))
    assert send(session, "finish")["admission"] == "admitted"


def test_correction_native_selection_validates_kind_bytes_and_provenance(selected, tmp_path):
    session, frontend, path, scope = selected
    link = tmp_path / "linked.ccmx"
    link.symlink_to(path)
    with pytest.raises(OSError):
        session.stage_correction(link, **scope, options={})
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeError):
        session.stage_correction(path, **scope, options={})
    path.write_text("CCMX\n" + "x" * 16384)
    with pytest.raises(ValueError, match="byte bound"):
        session.stage_correction(path, **scope, options={})
    path.write_text(ccmx())
    staged = session.stage_correction(path, **scope, options={})
    actual = deepcopy(session._corrections[staged["correction"]["token"]]["correction"])
    for field in ("sha256", "material_sha256"):
        damaged = deepcopy(actual)
        damaged["source"][field] = "0" * 64
        with pytest.raises(ValueError, match="provenance"):
            Configuration.from_options(dict(correction=damaged))
    # Narrow the native facility itself; a broad accepts_correction boolean is insufficient.
    frontend._caps = (frontend._caps[0], frontend._caps[1] & ~CAPABILITY2["ccss"],
                      frontend._caps[2])
    descriptor = json.loads(frontend.describe_json())
    path.write_text(ccss())
    request = dict(schema_version=1, device_id=descriptor["device_id"],
                   capability_id=descriptor["capability_id"], mode="emissive_spot",
                   workflow="spot", patch_ids=["target"],
                   options=dict(correction=dict(text=ccss())),
                   layout={}, event_contract="retained_patch_v1")
    with pytest.raises(ValueError, match="CCSS support"):
        frontend._validate_request(json.dumps(request))
    for initial, configured, accepted in (
        (CAPABILITY2["ccmx"] | CAPABILITY2["ccss"], CAPABILITY2["ccmx"], False),
        (CAPABILITY2["ccmx"], CAPABILITY2["ccmx"] | CAPABILITY2["ccss"], True),
        (0, CAPABILITY2["ccss"], True),
    ):
        frontend._caps = (frontend._caps[0], initial, frontend._caps[2])
        frontend._mode_snapshots["emissive_spot"] = dict(
            mode_capabilities=frontend._caps[0], trigger_capabilities=configured,
            filter_capabilities=0,
        )
        descriptor = json.loads(frontend.describe_json())
        offer = next(item for item in descriptor["options"] if item["id"] == "correction")
        request["capability_id"] = descriptor["capability_id"]
        if accepted:
            frontend._validate_request(json.dumps(request))
        else:
            with pytest.raises(ValueError, match="CCSS support"):
                frontend._validate_request(json.dumps(request))
        assert ("ccss" in offer["by_mode"]["emissive_spot"]["kinds"]) is accepted
