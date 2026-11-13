"""Bench session runner: guidance, saved-batch checks and append-only records.

Batches are real Demo batches saved by the application's own batch owner and
read back through ``review_saved``; no instrument, native worker or port is used.
"""
import hashlib
import importlib.util
import json
import plistlib
import sys
from pathlib import Path

import pytest
from test_application import (
    _command,
    _complete,
    _demo_frontend,
    _snapshot,
    _wait,
    no_native_transport,  # noqa: F401  (autouse guard)
)

from color_measurement.application import BatchApplication, review_saved
from color_measurement.native_receipt import native_worker_name

_SPEC = importlib.util.spec_from_file_location(
    "bench_session", Path(__file__).resolve().parents[1] / "tools" / "bench_session.py")
bench = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bench  # dataclasses resolve their module by name
_SPEC.loader.exec_module(bench)


@pytest.fixture
def owners():
    created = []
    yield created
    for owner in created:
        _wait(owner, lambda state: not state["companions"]["saving"])
        if _snapshot(owner)["status"] == "open":
            _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def demo_batch(owners, folder, persona, mode, *, readings=0, calibrate=False, finish=False,
               options=None):
    owner = BatchApplication(folder, frontend=_demo_frontend(persona), mode=mode,
                             options=options or {})
    owner.bind()
    owners.append(owner)
    if calibrate:
        _complete(owner, "calibrate")
    for _ in range(readings):
        _complete(owner)
    if finish:
        assert _command(owner, "finish")["admission"] == "admitted"
        _wait(owner, lambda state: state["status"] == "finished")
    return owner


def _failed(checks):
    return [check["name"] for check in checks if not check["ok"]]


@pytest.fixture
def packaged_app(tmp_path):
    """Current package and production-report contract, with inert worker bytes."""
    from test_discovery import _report

    app = tmp_path / "Color Measurement Candidate.app"
    resources = app / "Contents" / "Resources"
    kit = resources / "instrument-software"
    kit.mkdir(parents=True)
    catalog = json.loads((Path(__file__).parent / "fixtures/discovery/catalog.json").read_text())
    report = _report(kit, override={"driver_manifest": catalog})
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps(
            dict(
                CFBundleIdentifier="io.github.ferazambuja.colormeasurement.candidate",
                CMInstrumentSoftwareReportSHA256=hashlib.sha256(report.read_bytes()).hexdigest(),
            )
        )
    )
    import importlib.util
    import marshal

    from check_core_imports import package_file_inventory

    source = bench.ROOT / "src" / "color_measurement"
    package = resources / "color_measurement"
    code = app / "Contents" / "Frameworks" / "color_measurement"
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if not path.is_file() or "__pycache__" in relative.parts:
            continue
        target = code / relative.with_suffix(".pyc") if path.suffix == ".py" else package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = path.read_bytes()
        if path.suffix == ".py":
            target.write_bytes(
                importlib.util.MAGIC_NUMBER
                + b"\0" * 12
                + marshal.dumps(compile(content, relative.as_posix(), "exec"))
            )
            if relative.as_posix() in {"science.py", "presentation.py"}:
                retained_source = package / relative
                retained_source.parent.mkdir(parents=True, exist_ok=True)
                retained_source.write_bytes(content)
        else:
            target.write_bytes(content)
    identity = code / "_packaged_identity.pyc"
    text = "REPORT_SHA256 = " + repr(hashlib.sha256(report.read_bytes()).hexdigest()) + "\n"
    identity.write_bytes(importlib.util.MAGIC_NUMBER + b"\0" * 12 +
                         marshal.dumps(compile(text, "_packaged_identity.py", "exec")))
    (resources / "PACKAGE.json").write_text(
        json.dumps(
            dict(
                format=1,
                instrument_report_sha256=hashlib.sha256(report.read_bytes()).hexdigest(),
                package_sha256=package_file_inventory(source),
            )
        )
    )
    return app, report



@pytest.mark.parametrize("field", [
    "backend_version", "description_helper_sha256", "session_helper_sha256",
    "build_report_sha256", "missing", "backend",
])
def test_physical_identity_requires_the_selected_native_report_and_workers(field):
    """Synthetic negative inputs exercise attribution, not physical measurements."""
    from copy import deepcopy

    receipt = dict(backend_version="3.5.0", description_helper_sha256="a" * 64,
                   session_helper_sha256="b" * 64, build_report_sha256="c" * 64)
    review = dict(manifest=dict(descriptor=dict(
        provenance="physical", device=dict(model=bench.DEVICES["cr250"].model,
                                            serial="UNIT-TEST", backend="argyll-native"),
        current_binding_identity=dict(provenance=deepcopy(receipt))), mode="emissive_spot"))
    report = dict(binding_receipt=receipt)
    assert not _failed(bench.check_identity(review, bench.DEVICES["cr250"], rehearsal=False,
                                            serial="UNIT-TEST", report=report))
    if field == "missing":
        review["manifest"]["descriptor"].pop("current_binding_identity")
    elif field == "backend":
        review["manifest"]["descriptor"]["device"]["backend"] = "python-direct-serial"
    else:
        review["manifest"]["descriptor"]["current_binding_identity"]["provenance"][field] = "wrong"
    failures = _failed(bench.check_identity(review, bench.DEVICES["cr250"], rehearsal=False,
                                            serial="UNIT-TEST", report=report))
    assert failures == ["Argyll C backend" if field == "backend" else "native build receipt"]


def test_mode_inventory_keeps_every_descriptor_condition_and_unexecuted_route():
    """The committed substituted descriptor is an offer, never a bench pass."""
    from copy import deepcopy

    descriptor = json.loads((Path(__file__).parent /
                            "fixtures/generic_frontend/i1pro3/describe.json").read_text())
    review = dict(manifest=dict(descriptor=descriptor))
    inventory = bench.mode_inventory(review)
    assert {r["mode"] for r in inventory["rows"]} == {m["mode"] for m in descriptor["modes"]}
    offers = {r["mode"]: r for r in inventory["rows"]}
    for condition in ("m0", "m1", "m2"):
        assert offers["reflective_spot_" + condition]["condition"] == condition
    assert offers["reflective_strip_m1"]["workflow"] == "strip"
    assert offers["reflective_strip_m1"]["operator_setup"]["text"]
    records = [dict(step="batch", facts=dict(mode_inventory=inventory)), dict(
        kind="readings", step="default-reflective", automatic="pass", outcome="pass",
        facts=dict(descriptor_sha256=inventory["descriptor_sha256"],
                   measured_mode="reflective_spot", repeatability=dict(n=5)))]
    rows = {r["mode"]: r for r in bench.mode_coverage(records)[0]["rows"]}
    assert rows["reflective_spot"]["state"] == "pass"
    assert all(rows["reflective_spot_" + c]["state"] == "not executed" for c in ("m0", "m1", "m2"))
    assert rows["reflective_strip_m1"]["state"] == "not executed"
    records[1]["facts"]["descriptor_sha256"] = "different-context"
    assert all(r["state"] == "not executed" for r in bench.mode_coverage(records)[0]["rows"])
    changed = deepcopy(descriptor)
    next(r for r in changed["modes"] if r["mode"] == "reflective_spot_m1").update(
        available=False, unavailable_reason="Recorded configured refusal")
    other = bench.mode_inventory(dict(manifest=dict(descriptor=changed)))
    rows = {r["mode"]: r for r in bench.mode_coverage([
        dict(facts=dict(mode_inventory=other))])[0]["rows"]}
    assert rows["reflective_spot_m1"]["state"] == "not available"
    assert rows["reflective_spot_m1"]["unavailable_reason"] == "Recorded configured refusal"
    records[1].update(automatic="fail", outcome="pass")
    records[1]["facts"]["descriptor_sha256"] = inventory["descriptor_sha256"]
    assert next(r for r in bench.mode_coverage(records)[0]["rows"]
                if r["mode"] == "reflective_spot")["state"] == "fail"


def test_identity_admits_simulated_batches_only_in_rehearsal(owners, tmp_path):
    demo_batch(owners, tmp_path / "b", "cr100", "emissive_spot", readings=1)
    review = review_saved(tmp_path / "b")
    device = bench.DEVICES["cr100"]
    assert not _failed(bench.check_identity(review, device, rehearsal=True,
                                            serial="DEMO-cr100", mode="emissive_spot"))
    assert _failed(bench.check_identity(review, device, rehearsal=False, serial=None)) == [
        "provenance"]
    assert _failed(bench.check_identity(review, bench.DEVICES["cr250"], rehearsal=True,
                                        serial=None)) == ["model"]
    assert _failed(bench.check_identity(review, device, rehearsal=True,
                                        serial="ANOTHER")) == ["serial"]
    assert _failed(bench.check_identity(review, device, rehearsal=True, serial=None,
                                        mode="emissive_tele")) == ["mode"]
    review["manifest"]["descriptor"]["device"]["backend"] = "argyll-native"
    assert _failed(bench.check_identity(review, device, rehearsal=True,
                                        serial=None)) == ["Demo rehearsal source"]


@pytest.mark.parametrize("persona,xyz_only", [
    ("cr100", False), ("cr250", False), ("cr250", True), ("pr655", False)])
def test_readings_require_the_models_spectrum_rule(owners, tmp_path, persona, xyz_only):
    demo_batch(owners, tmp_path / "b", persona, "emissive_spot", readings=2,
               options={"xyz_only": True} if xyz_only else None)
    review = review_saved(tmp_path / "b")
    checks, stats = bench.check_readings(review["operations"], bench.DEVICES[persona],
                                         review=review, count=2, rehearsal=True,
                                         xyz_only=xyz_only)
    assert not _failed(checks), checks
    assert stats["n"] == 2 and stats["Y_sd"] >= 0 and "x_mean" in stats
    # The same batch judged against the wrong count or spectrum rule fails.
    assert _failed(bench.check_readings(review["operations"], bench.DEVICES[persona],
                                        review=review, count=3, rehearsal=True,
                                        xyz_only=xyz_only)[0]) == ["accepted readings"]
    spectral = bench.DEVICES[persona].spectrum and not xyz_only
    other = bench.DEVICES["cr100" if spectral else "cr250"]
    wrong = _failed(bench.check_readings(review["operations"], other, review=review,
                                         count=2, rehearsal=True, xyz_only=False)[0])
    assert len(wrong) == 2 and all("spectrum" in name for name in wrong), wrong


def test_pr_band_count_is_the_models(owners, tmp_path):
    demo_batch(owners, tmp_path / "b", "pr655", "emissive_spot", readings=1)
    review = review_saved(tmp_path / "b")
    narrowed = bench.Device("pr655", "Photo Research PR-655", "instPR655", "pr655", True, 201)
    failed = _failed(bench.check_readings(review["operations"], narrowed, review=review,
                                          count=1, rehearsal=True, xyz_only=False)[0])
    assert len(failed) == 1 and failed[0].endswith(" spectrum"), failed


def test_physical_cr_readings_require_retained_native_facts(owners, tmp_path):
    """Demo has no native facts; a physical CR/PR reading without them fails."""
    demo_batch(owners, tmp_path / "b", "cr250", "emissive_spot", readings=1)
    review = review_saved(tmp_path / "b")
    observation = review["operations"][0]["result"]["patches"][0]["observation"]
    observation["provenance"] = "physical"

    def failed(rehearsal=False):
        return _failed(bench.check_readings(review["operations"], bench.DEVICES["cr250"],
                                            review=review, count=1, rehearsal=rehearsal,
                                            xyz_only=False)[0])

    assert [name.split()[-2:] for name in failed()] == [["native", "facts"]]
    observation["configuration"] = [*observation["configuration"], ["native_facts_json", "{}"]]
    assert not failed()
    # A rehearsal admits only simulated readings, as its batch identity does.
    assert [name.split()[-1] for name in failed(rehearsal=True)] == ["provenance"]


def test_calibration_and_finish_checks(owners, tmp_path):
    owner = demo_batch(owners, tmp_path / "b", "i1pro3", "reflective_spot", calibrate=True,
                       readings=1)
    review = review_saved(tmp_path / "b")
    assert not _failed(bench.check_calibration(review["operations"]))
    calibration = next(op for op in review["operations"]
                       if op["result"]["workflow"] == "calibrate")
    calibration["result"]["calibration"] = dict(calibration["result"]["calibration"],
                                                status="required")
    assert [name.split()[-1] for name in _failed(bench.check_calibration(
        review["operations"]))] == ["calibration"]
    assert _failed(bench.check_calibration([op for op in review["operations"]
                                            if op["result"]["workflow"] == "spot"]))
    assert _failed(bench.check_finish(review)) == ["batch finished"]
    assert _command(owner, "finish")["admission"] == "admitted"
    _wait(owner, lambda state: state["status"] == "finished")
    assert not _failed(bench.check_finish(review_saved(tmp_path / "b")))


def test_settings_apply_and_restore_are_checked(tmp_path, monkeypatch):
    from uuid import uuid4

    from test_desktop_session import completed_command, measure, send, settled, wait
    from test_native_controls import native_frontend, native_readback_seam

    from color_measurement.desktop_session import DesktopSession

    front = native_frontend()
    native_readback_seam(monkeypatch, front)
    session = DesktopSession([front])
    selection = session.request("settings", {"selection_id": None})["selections"][0]
    session.create(tmp_path / "s", "Settings", "", selection_id=selection["selection_id"],
                   mode="emissive_spot", options={})

    def workflow(action, changes=None):
        state = settled(session)
        draft = state["draft"]
        if changes is not None:
            command_id = str(uuid4())
            assert send(session, "edit_draft", dict(
                draft_id=draft["id"], version=draft["version"], name="Settings", notes="",
                number_samples=True, options=dict(native_controls=changes)),
                identity=command_id)["admission"] == "admitted"
            wait(session, lambda _: session.batch.command_outcome(command_id)["state"]
                 not in {"pending", "saving"})
            state = settled(session)
            draft = state["draft"]
        assert send(session, "run_workflow", dict(
            context_id=state["context_id"], draft_id=draft["id"],
            draft_version=draft["version"], workflow="native_controls", patch_ids=[],
            layout=dict(action=action)))["admission"] == "admitted"
        settled(session)
        return review_saved(tmp_path / "s")["operations"][-1:]

    try:
        read_checks, original = bench.check_settings(workflow("read"), action="read")
        assert not _failed(read_checks) and original["values"]["cycles"] == 1
        apply_checks, applied = bench.check_settings(workflow("apply", dict(cycles=3)),
                                                     action="apply")
        assert not _failed(apply_checks) and applied["values"]["cycles"] == 3
        assert _failed(bench.check_restored(applied, original["values"], ("cycles",))) == [
            "cycles restored"]
        restore_checks, restored = bench.check_settings(workflow("apply", dict(cycles=1)),
                                                        action="restore")
        assert not _failed(restore_checks)
        assert not _failed(bench.check_restored(restored, original["values"], ("cycles",)))
        measure(session)
        settled(session)
        completed_command(session, "finish")
    finally:
        if session.batch.snapshot()["status"] == "open":
            _command(session.batch, "close",
                     dict(discard_token=session.batch.discard_summary()["token"]))


def test_an_apply_with_an_unconfirmed_change_fails():
    control = dict(failed=False, status_code=0, requested=dict(present=2), changed=2,
                   attempted=2, acknowledged=2, confirmed=0, values={}, before={})
    operation = dict(command_id="a", review_state="accepted", result=dict(
        workflow="native_controls", configuration=[["native_control_json",
                                                    json.dumps(control)]]))
    assert _failed(bench.check_settings([operation], action="apply")[0]) == [
        "every change confirmed"]
    control["confirmed"] = 2
    operation["result"]["configuration"] = [["native_control_json", json.dumps(control)]]
    assert not _failed(bench.check_settings([operation], action="apply")[0])


def test_cancel_check_requires_a_stopped_then_completed_reading():
    def op(status, state="accepted", patches=1):
        return dict(command_id=status + state, review_state=state, result=dict(
            workflow="spot", status=status, patches=[{}] * patches))

    assert not _failed(bench.check_cancel([op("cancelled", patches=0), op("completed")]))
    assert "cancelled reading published no reading" in _failed(bench.check_cancel(
        [op("completed"), op("completed")]))
    assert "following reading completed" in _failed(bench.check_cancel(
        [op("cancelled", patches=0)]))
    assert "order" in _failed(bench.check_cancel([op("completed"), op("cancelled", patches=0)]))


def test_records_are_append_only_and_chained(tmp_path):
    first = bench.append_record(tmp_path, dict(device="cr100", step="a"))
    second = bench.append_record(tmp_path, dict(device="cr100", step="b"))
    assert first["previous_sha256"] is None and second["seq"] == 2
    assert [r["step"] for r in bench.read_records(tmp_path)] == ["a", "b"]
    lines = (tmp_path / "steps.jsonl").read_bytes().splitlines()
    (tmp_path / "steps.jsonl").write_bytes(
        lines[0].replace(b'"a"', b'"x"') + b"\n" + lines[1] + b"\n")
    with pytest.raises(ValueError, match="record 2 does not follow"):
        bench.read_records(tmp_path)


def test_start_records_the_report_policy_and_refuses_reuse(tmp_path, monkeypatch, packaged_app):
    from color_measurement.discovery import InstrumentBuild

    app, report = packaged_app
    for method in ("attached", "describe", "bind"):
        monkeypatch.setattr(InstrumentBuild, method,
                            lambda *a, **k: pytest.fail("Bench start performed device I/O"))
    arguments = ["start", "--name", "s", "--operator", "op", "--root", str(tmp_path / "root")]
    assert bench.main(arguments) == 1
    assert bench.main(arguments + ["--app", str(app)]) == 0
    meta = json.loads((tmp_path / "root" / "s" / "session.json").read_text())
    assert not meta["rehearsal"] and meta["report"]["sha256"]
    assert meta["report"]["sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert meta["report"]["binding_receipt"]["build_report_sha256"] == meta["report"]["sha256"]
    assert meta["app"]["kind"] == "packaged" and meta["app"]["package_sha256"]
    assert meta["runner"]["commit"] and "commit" not in meta["app"]
    assert meta["models"]["pr655"]["opening_requires"] == "accept_disclosed_initialization"
    sheet = (tmp_path / "root" / "s" / "OPERATOR.md").read_text()
    assert "Photo Research PR-655:" in sheet and "opening requires" in sheet
    assert "X-Rite i1 Pro 3: no read-allowance floor" in sheet
    assert "already configured" in sheet and "Choose instrument software" not in sheet
    # The operator can paste each runner command as written.
    assert f"in `{bench.ROOT}`" in sheet and "<this folder>" not in sheet
    assert "<key>" not in sheet
    for key in bench.DEVICES:
        assert f"--device {key}`" in sheet
    assert f"report --session {bench.shlex.quote(str(tmp_path / 'root' / 's'))}`" in sheet
    assert str(app) in meta["app"]["launch"] and "uv run" not in meta["app"]["launch"]
    assert bench.main(arguments + ["--app", str(app)]) == 1


@pytest.mark.parametrize("changed", ["report", "worker", "package", "symlink"])
def test_changed_packaged_inputs_refuse_before_a_session_is_created(
    tmp_path, packaged_app, changed,
):
    app, report = packaged_app
    target = report if changed == "report" else (
        report.parent / native_worker_name("generic-worker-never-run") if changed == "worker" else
        app / "Contents/Frameworks/color_measurement/desktop.pyc")
    if changed == "symlink":  # identical bytes, but no longer the package's own file
        copy = tmp_path / "desktop.py"
        target = app / "Contents/Frameworks/color_measurement/desktop.pyc"
        copy.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(copy)
    else:
        target.write_bytes(target.read_bytes() + b"\nchanged\n")
    destination = tmp_path / "refused"
    assert bench.main(["start", "--name", "s", "--operator", "op", "--app", str(app),
                       "--root", str(destination)]) == 1
    assert not destination.exists()


def test_older_session_metadata_is_preserved_without_silent_requalification(tmp_path):
    original = b'{"schema": 1, "rehearsal": false}\n'
    (tmp_path / "session.json").write_bytes(original)
    assert bench.main(["run", "--session", str(tmp_path), "--device", "cr100"]) == 1
    assert (tmp_path / "session.json").read_bytes() == original
    assert not (tmp_path / "steps.jsonl").exists()


def test_rehearsal_run_guides_checks_records_and_resumes(owners, tmp_path):
    """The CR-100 list end to end against Demo batches, with scripted answers."""
    assert bench.main(["start", "--name", "r", "--operator", "op", "--rehearsal",
                       "--root", str(tmp_path)]) == 0
    session = tmp_path / "r"
    current, said = {}, []
    batch = tmp_path / "cr100-spot"
    tele = tmp_path / "cr100-tele"

    def say(text=""):
        said.append(text)
        if text.lstrip("\n").startswith("== "):
            current["step"] = text.rsplit("[", 1)[1].rstrip("]")

    def ask(prompt):
        step = current.get("step")
        if prompt.startswith("  Press Enter"):
            if step == "batch-emissive_spot":
                current["owner"] = demo_batch(owners, batch, "cr100", "emissive_spot")
            elif step == "repeat-emissive_spot":
                for _ in range(5):
                    _complete(current["owner"])
            elif step == "reading-after-apply":
                _complete(current["owner"])
            elif step in ("settings-read", "settings-apply", "settings-restore", "cancel"):
                return "s Demo has no native settings or timed cancel"
            elif step == "finish":
                assert _command(current["owner"], "finish")["admission"] == "admitted"
                _wait(current["owner"], lambda state: state["status"] == "finished")
            elif step == "batch-emissive_tele":
                current["owner"] = demo_batch(owners, tele, "cr100", "emissive_tele")
            elif step == "repeat-emissive_tele":
                for _ in range(5):
                    _complete(current["owner"])
            elif step == "finish-emissive_tele":
                assert _command(current["owner"], "finish")["admission"] == "admitted"
                _wait(current["owner"], lambda state: state["status"] == "finished")
            return ""
        if prompt.startswith("  Batch folder"):
            return str(batch if step == "batch-emissive_spot" else tele).replace(" ", "\\ ")
        if prompt.startswith("  Serial"):
            return "DEMO-cr100"
        return ""

    console = bench.Console(ask=ask, say=say)
    assert bench.run_device(session, bench.DEVICES["cr100"], console) == 0, "\n".join(said)
    records = bench.read_records(session)
    by_step = {record["step"]: record for record in records}
    expected = [step.id for step in bench.steps_for(bench.DEVICES["cr100"])]
    assert [record["step"] for record in records] == expected
    for step in ("batch-emissive_spot", "repeat-emissive_spot", "reading-after-apply",
                 "finish", "batch-emissive_tele", "repeat-emissive_tele",
                 "finish-emissive_tele"):
        assert by_step[step]["outcome"] == "pass", (step, by_step[step]["checks"])
        assert by_step[step]["automatic"] == "pass"
    assert by_step["settings-read"]["outcome"] == "skip"
    assert by_step["repeat-emissive_spot"]["facts"]["repeatability"]["n"] == 5
    evidence = by_step["finish"]["evidence"]
    assert {Path(item["path"]).name for item in evidence} >= {"batch.json"}
    assert all((session / item["path"]).is_file() for item in evidence)
    # Resuming records nothing new; the report covers every step.
    assert bench.run_device(session, bench.DEVICES["cr100"], console) == 0
    assert len(bench.read_records(session)) == len(records)
    assert bench.main(["report", "--session", str(session)]) == 0
    summary = (session / "summary.md").read_text()
    assert "REHEARSAL" in summary and "| repeat-emissive_spot | pass | pass |" in summary
    assert "Exploratory comparison" in summary
    assert "Descriptor mode coverage" in summary and "not executed" in summary
    coverage = json.loads((session / "summary.json").read_text())["mode_coverage"]["cr100"]
    assert coverage and any(r["state"] == "pass" for c in coverage for r in c["rows"])


def test_steps_name_the_labels_the_application_presents():
    """Mode and settings captions come from the committed frontend descriptors."""
    import re

    fixtures = Path(__file__).parent / "fixtures" / "generic_frontend"
    for key, device in bench.DEVICES.items():
        descriptor = json.loads((fixtures / key / "describe.json").read_text())
        modes = {row["mode"]: row["label"] for row in descriptor["modes"]}
        members = {member["label"] for option in descriptor["options"]
                   for member in option.get("members") or ()}
        named = 0
        for step in bench.steps_for(device):
            text = " ".join(step.lines)
            if step.kind == "batch" and step.mode in modes:
                assert f"Measurement mode = '{modes[step.mode]}'" in text, step.id
            if step.kind == "settings" and step.action != "read":
                change = text.split(" set ", 1)[1].split(" (blank = unchanged)", 1)[0]
                captions = re.findall(r"'([^']+)'", change.replace("'settings-read'", ""))
                assert captions and set(captions) <= members, (step.id, captions)
                named += 1
        assert named == {None: 0, "cr250": 2, "pr655": 5}[device.native]
        # Every explicit spot measurement condition offered gets its own batch.
        conditions = {row["mode"] for row in descriptor["modes"] if row["workflow"] == "spot"
                      and (row.get("axes") or {}).get("condition")}
        assert conditions <= {step.mode for step in bench.steps_for(device)}, key
        assert key != "i1pro3" or len(conditions) == 3


def test_a_skipped_batch_skips_its_dependent_steps(owners, tmp_path):
    """The XYZ-only steps must not check the previous, finished batch instead."""
    assert bench.main(["start", "--name", "x", "--operator", "op", "--rehearsal",
                       "--root", str(tmp_path)]) == 0
    session, device = tmp_path / "x", bench.DEVICES["cr250"]
    owner = demo_batch(owners, tmp_path / "spot", "cr250", "emissive_spot", readings=1,
                       finish=True)
    operations = [op["command_id"] for op in review_saved(tmp_path / "spot")["operations"]]
    steps = [step.id for step in bench.steps_for(device)]
    for step in bench.steps_for(device)[:steps.index("finish") + 1]:
        record = dict(device=device.key, step=step.id, kind=step.kind, outcome="pass")
        if step.id == "batch-emissive_spot":
            record.update(batch=str(tmp_path / "spot"), operations=operations)
        if step.id == "repeat-emissive_spot":  # one reading has no standard deviation
            record["facts"] = dict(repeatability=bench._repeatability([[1.0, 2.0, 3.0]]))
        bench.append_record(session, record)
    assert owner is not None
    answers = iter(["s no time today", "q"])
    said = []
    console = bench.Console(ask=lambda prompt: next(answers), say=said.append)
    assert bench.run_device(session, device, console) == 0
    by_step = {record["step"]: record for record in bench.read_records(session)}
    assert by_step["batch-emissive_spot-xyz-only"]["outcome"] == "skip"
    for step in ("repeat-emissive_spot-xyz-only", "finish-xyz-only"):
        assert by_step[step]["outcome"] == "skip", by_step[step]
        assert by_step[step]["note"] == "its batch was skipped"
    stats = by_step["repeat-emissive_spot"]["facts"]["repeatability"]
    assert stats["n"] == stats["n_xy"] == 1 and stats["Y_mean"] == 2.0
    assert stats["Y_sd"] is stats["x_sd"] is stats["y_sd"] is None
    assert bench.main(["report", "--session", str(session)]) == 0
    assert "(one reading)" in (session / "summary.md").read_text()


@pytest.mark.parametrize("rows,expected", [
    ([[1e308, 1.0, 1e308]] * 2,
     dict(n=2, n_xy=0, Y_mean=1.0, Y_sd=0.0, x_mean=None, y_mean=None,
          x_sd=None, y_sd=None)),
    ([[1e308] * 3] * 2, dict(n=2, n_xy=0, Y_mean=1e308, Y_sd=0.0, x_mean=None)),
    ([[-2.0, -3.0, -5.0], [-4.0, -6.0, -10.0]],
     dict(n=2, n_xy=2, Y_mean=-4.5, Y_sd=2.1213203435596424,
          x_mean=0.2, y_mean=0.3, x_sd=0.0, y_sd=0.0)),
    ([[0.0, sys.float_info.max, 0.0], [0.0, -sys.float_info.max, 0.0]],
     dict(n=2, n_xy=2, Y_mean=0.0, Y_sd=None, x_mean=0.0, y_mean=1.0)),
    ([[sys.float_info.max, 1.0, -sys.float_info.max],
      [-sys.float_info.max, 1.0, sys.float_info.max]],
     dict(n=2, n_xy=2, Y_mean=1.0, Y_sd=0.0, x_mean=0.0, x_sd=None, y_mean=1.0)),
    ([[2.0, 3.0, 5.0], [0.0, 0.0, 0.0], [1e308, 1.0, 1e308]],
     dict(n=3, n_xy=1, Y_mean=4.0 / 3.0, x_mean=0.2, y_mean=0.3,
          x_sd=None, y_sd=None)),
])
def test_repeatability_preserves_domains_counts_and_unavailable_statistics(rows, expected):
    import math

    stats = bench._repeatability(rows)
    for key, value in expected.items():
        if value is None:
            assert stats[key] is None
        else:
            assert stats[key] == pytest.approx(value)
    assert all(value is None or math.isfinite(value) for value in stats.values())
    json.dumps(stats, allow_nan=False)


@pytest.mark.parametrize("rows,markers", [
    ([[1e308, 1.0, 1e308]] * 2, ("x,y unavailable (n=0/2)", "x_mean unavailable")),
    ([[0.0, sys.float_info.max, 0.0], [0.0, -sys.float_info.max, 0.0]],
     ("Y 0 (sd unavailable)", "Y_sd unavailable", "x,y 0.00000,1.00000 (n=2/2)")),
])
def test_repeatability_report_retains_unavailable_values(tmp_path, rows, markers):
    assert bench.main(["start", "--name", "r", "--operator", "op", "--rehearsal",
                       "--root", str(tmp_path)]) == 0
    session = tmp_path / "r"
    stats = bench._repeatability(rows)
    bench.append_record(session, dict(
        device="cr250", step="repeat-emissive_spot", kind="readings", outcome="pass",
        automatic="pass", facts=dict(repeatability=stats),
    ))
    assert bench.main(["report", "--session", str(session)]) == 0
    text = (session / "summary.md").read_text()
    assert all(marker in text for marker in markers)
    summary = json.loads((session / "summary.json").read_text())
    retained = next(row for row in summary["devices"]["cr250"]
                    if row["step"] == "repeat-emissive_spot")
    assert retained["facts"]["repeatability"] == stats


def test_a_failed_gate_offers_to_stop_the_device(owners, tmp_path):
    assert bench.main(["start", "--name", "g", "--operator", "op", "--root", str(tmp_path),
                       "--rehearsal"]) == 0
    answers = iter(["", "port", "", "",                            # preflight
                    "", "row", "WRONG-SERIAL", "1.0", "f", "", "y"])  # initialize, stop
    said = []
    console = bench.Console(ask=lambda prompt: next(answers), say=said.append)
    assert bench.run_device(tmp_path / "g", bench.DEVICES["cr100"], console) == 1
    steps = [record["step"] for record in bench.read_records(tmp_path / "g")]
    assert steps == ["preflight", "initialize"]
    assert any("Stopped this device" in line for line in said)


_IDENTITY_FAULTS = [None, "candidate", "executable", "bundle_id", "version", "bundle_folder",
                    "missing_executable", "layout", "report_digest"]


@pytest.mark.parametrize("fault", _IDENTITY_FAULTS)
def test_release_preparation_refuses_renamed_or_inconsistent_distribution(tmp_path, fault):
    import prepare_beta_assets as prepare

    name = "Color Measurement Candidate" if fault == "bundle_folder" else "Color Measurement"
    app = tmp_path / (name + ".app")
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    if fault != "missing_executable":
        (contents / "MacOS/Color Measurement").write_bytes(b"inert, never run")
    metadata = dict(application_name="Color Measurement", version="0.1.0", system="Darwin",
                    instrument_report_sha256="a" * 64)
    info = dict(CFBundleName="Color Measurement", CFBundleExecutable="Color Measurement",
                CFBundleIdentifier=prepare.BUNDLE_IDENTIFIER,
                CFBundleVersion="0.1.0", CFBundleShortVersionString="0.1.0",
                LSMinimumSystemVersion="27.0",
                CMInstrumentSoftwareReportSHA256="a" * 64)
    if fault == "candidate":
        metadata["application_name"] = "Color Measurement Candidate"
    elif fault == "executable":
        info["CFBundleExecutable"] = "Color Measurement Candidate"
    elif fault == "bundle_id":
        info["CFBundleIdentifier"] += ".candidate"
    elif fault == "version":
        info["CFBundleVersion"] = "unrelated"
    elif fault == "layout":
        metadata["system"] = "Linux"
    elif fault == "report_digest":
        info["CMInstrumentSoftwareReportSHA256"] = "b" * 64
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    if fault:
        with pytest.raises(ValueError, match="distribution|Distribution"):
            prepare.verify_distribution_identity(app, metadata)
    else:
        prepare.verify_distribution_identity(app, metadata)


@pytest.mark.skipif(sys.platform != "darwin", reason="codesign is macOS-only")
@pytest.mark.parametrize("fault", [None, "unsigned", "modified", "identifier"])
def test_release_signature_check_requires_an_intact_adhoc_seal(tmp_path, fault):
    """Signs and inspects a copied system tool; nothing in the bundle is executed."""
    import shutil
    import subprocess

    import prepare_beta_assets as prepare

    app = tmp_path / "Color Measurement.app"
    executable = app / "Contents/MacOS/Color Measurement"
    executable.parent.mkdir(parents=True)
    (app / "Contents/Resources").mkdir()
    shutil.copyfile("/usr/bin/true", executable)  # Bytes only; system file flags stay behind.
    executable.chmod(0o755)
    (app / "Contents/Resources/sealed.txt").write_text("sealed resource\n")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(dict(
        CFBundleExecutable="Color Measurement", CFBundleIdentifier=prepare.BUNDLE_IDENTIFIER,
        CFBundlePackageType="APPL")))
    if fault == "unsigned":
        subprocess.run(["/usr/bin/codesign", "--remove-signature", str(executable)], check=True)
    else:
        identifier = "example.other" if fault == "identifier" else prepare.BUNDLE_IDENTIFIER
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier",
                        identifier, str(app)], check=True, capture_output=True)
    if fault == "modified":
        (app / "Contents/Resources/sealed.txt").write_text("changed after signing\n")
    if fault:
        with pytest.raises(ValueError, match="intact ad-hoc beta seal"):
            prepare.verify_adhoc_signature(app)
    else:
        prepare.verify_adhoc_signature(app)


@pytest.mark.parametrize(("fault", "message"), [
    (None, None),
    ("missing", "inventory differs"),
    ("extra", "inventory differs"),
    ("changed", "differs from this checkout: module.py"),
    ("duplicate", "duplicate entry"),
    ("link", "unexpected entry"),
    ("directory_escape", "unexpected path"),
    ("checkout_link", "differs from this checkout: module.py"),
])
def test_release_source_check_compares_all_tracked_bytes(tmp_path, fault, message):
    import io
    import subprocess
    import tarfile

    import prepare_beta_assets as prepare

    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    files = {".gitignore": b"local/\n", "module.py": b"value = 1\n"}
    for name, data in {**files, ".gitattributes": b"* text=auto\n"}.items():
        (root / name).write_bytes(data)
    if fault == "checkout_link":
        # Equal bytes through a tracked link; the archive cannot represent it.
        (tmp_path / "outside.py").write_bytes(files["module.py"])
        (root / "module.py").unlink()
        try:
            (root / "module.py").symlink_to(tmp_path / "outside.py")
        except OSError:
            pytest.skip("This account cannot create symbolic links")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    archived = {**files, "PKG-INFO": b"Name: synthetic\n"}
    if fault == "missing":
        archived.pop(".gitignore")
    elif fault == "extra":
        archived["unexpected.py"] = b"pass\n"
    elif fault == "changed":
        archived["module.py"] = b"value = 2\n"
    archive = tmp_path / "color_measurement-0.1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        for name, data in archived.items():
            entry = tarfile.TarInfo("color_measurement-0.1.0/" + name)
            if fault == "link" and name == "module.py":
                # Same inventory, but the tracked file is replaced by a link.
                entry.type = tarfile.SYMTYPE
                entry.linkname = "/outside"
                stream.addfile(entry)
                continue
            entry.size = len(data)
            stream.addfile(entry, io.BytesIO(data))
        if fault == "duplicate":
            # Identical bytes: only the duplicate check can refuse the ambiguity.
            entry = tarfile.TarInfo("color_measurement-0.1.0/module.py")
            entry.size = len(files["module.py"])
            stream.addfile(entry, io.BytesIO(files["module.py"]))
        if fault == "directory_escape":
            entry = tarfile.TarInfo("color_measurement-0.1.0/../../outside")
            entry.type = tarfile.DIRTYPE
            stream.addfile(entry)
    if fault:
        with pytest.raises(ValueError, match=message):
            prepare.verify_source_distribution(archive, root)
    else:
        prepare.verify_source_distribution(archive, root)


_PREPARE_FAULTS = {
    None: None,
    "identity": "worker identity",
    "source_receipt": "Original native report",
    "source_receipt_during_archive": "Original native report changed during preparation",
    "candidate": "without --candidate",
    "system": "metadata must match",
    "home_path": "build-host path",
    "delivered_source": "Delivered source attachment differs",
}


@pytest.mark.parametrize("fault", _PREPARE_FAULTS)
def test_release_assets_bind_code_workers_sources_and_generated_records(
    tmp_path, monkeypatch, packaged_app, fault,
):
    import marshal
    import platform
    import shutil

    import prepare_beta_assets as prepare

    app, report = packaged_app
    app = app.rename(app.with_name("Color Measurement.app"))
    resources = app / "Contents/Resources"
    report = resources / "instrument-software/report.json"
    original = tmp_path / "original-report.json"
    original.write_text('{"original": "native receipt"}\n')
    receipt = json.loads(report.read_bytes())
    receipt["source_report_sha256"] = prepare.sha256(original)
    report.write_text(json.dumps(receipt))
    digest = prepare.sha256(report)
    metadata_path = resources / "PACKAGE.json"
    metadata = json.loads(metadata_path.read_bytes())
    metadata.update(application_name="Color Measurement", version="0.1.0",
                    system=platform.system(), architecture=platform.machine(),
                    instrument_report_sha256=digest)
    if fault == "candidate":
        metadata["application_name"] = "Color Measurement Candidate"
    elif fault == "system":
        metadata["system"] = "Plan 9"
    metadata_path.write_text(json.dumps(metadata))
    info = dict(CFBundleName="Color Measurement", CFBundleExecutable="Color Measurement",
                CFBundleIdentifier=prepare.BUNDLE_IDENTIFIER,
                CFBundleVersion="0.1.0", CFBundleShortVersionString="0.1.0",
                LSMinimumSystemVersion="27.0",
                CMInstrumentSoftwareReportSHA256=digest)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    (app / "Contents/MacOS").mkdir()
    (app / "Contents/MacOS/Color Measurement").write_bytes(b"inert, never run")
    identity = app / "Contents/Frameworks/color_measurement/_packaged_identity.pyc"
    code = "REPORT_SHA256 = " + repr("b" * 64 if fault == "identity" else digest) + "\n"
    identity.write_bytes(importlib.util.MAGIC_NUMBER + b"\0" * 12 +
                         marshal.dumps(compile(code, "_packaged_identity.py", "exec")))
    if fault == "source_receipt":
        original.write_text("changed receipt")
    home = "/home/builder/.local/share/uv/python/cpython-3.11-linux"
    if fault == "home_path":
        (resources / "frozen-stdlib.pyc").write_bytes(b"prefix=" + home.encode())
    sources = tmp_path / "sources"
    sources.mkdir()
    names = ["color_measurement-0.1.0.tar.gz", "Argyll_V3.5.0_src.zip",
             "qt-source.tar.xz", "pyside-source.tar.xz"]
    for name in names:
        (sources / name).write_bytes(b"synthetic attachment " + name.encode())
    # Archive verification has its own byte-comparison tests. This exercises the
    # real package authentication, compiled identity, archiver and emitted records.
    monkeypatch.setattr(prepare, "corresponding_sources", lambda directory, version: {
        sources / name: prepare.sha256(sources / name) for name in names})
    monkeypatch.setattr(prepare, "home_interpreter_prefixes", lambda: [home])
    signed = []
    monkeypatch.setattr(prepare, "verify_adhoc_signature", signed.append)
    if fault == "delivered_source":
        def changed_copy(source, target):
            Path(target).write_bytes(b"not the verified bytes")
        def no_link(source, target):
            raise OSError("different file system")
        monkeypatch.setattr(prepare.os, "link", no_link)
        monkeypatch.setattr(prepare.shutil, "copy2", changed_copy)
    if platform.system() != "Darwin":
        app = app.rename(app.with_name("Color Measurement"))
        contents = app / "Contents"
        resources = contents / "Resources"
        shutil.copytree(contents / "Frameworks/color_measurement",
                        resources / "color_measurement", dirs_exist_ok=True)
        resources.rename(app / "_internal")
        shutil.rmtree(contents)
        executable = "Color Measurement" + (".exe" if sys.platform == "win32" else "")
        (app / executable).write_bytes(b"inert, never run")
    monkeypatch.setattr(prepare.subprocess, "check_output", lambda command, **kwargs:
                        "" if "status" in command else "a" * 40 + "\n")
    output = tmp_path / "assets"
    monkeypatch.setattr(sys, "argv", ["prepare_beta_assets.py", "--application", str(app),
                        "--native-report", str(original), "--source-directory", str(sources),
                        "--output", str(output)])
    # Use the standard library archiver so this inert test also runs off macOS.
    def archive_application(command, **kwargs):
        import zipfile
        with zipfile.ZipFile(command[-1], "w") as zipped:
            for path in app.rglob("*"):
                if path.is_file():
                    zipped.write(path, path.relative_to(app.parent))
        if fault == "source_receipt_during_archive":
            original.write_text("receipt changed while archiving")
    monkeypatch.setattr(prepare.subprocess, "run", archive_application)
    if fault:
        with pytest.raises(ValueError, match=_PREPARE_FAULTS[fault]):
            prepare.main()
        assert fault in {"delivered_source", "source_receipt_during_archive"} or not output.exists()
        assert not (output / "SHA256SUMS").exists()
        return
    prepare.main()
    assert signed == ([app.resolve()] if platform.system() == "Darwin" else [])
    assert len(list(output.iterdir())) == 8
    assert not (output / "README.txt").exists()
    manifest = json.loads((output / "SOURCE-MANIFEST.json").read_bytes())
    system = {"Darwin": "macos"}.get(platform.system(), platform.system().lower())
    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower())
    extension = ".zip" if platform.system() in {"Darwin", "Windows"} else ".tar.gz"
    assert manifest["archive"] == f"Color-Measurement-0.1.0-{system}-{architecture}{extension}"
    assert manifest["archive_sha256"] == prepare.sha256(output / manifest["archive"])
    assert manifest["corresponding_source"] == {
        name: prepare.sha256(sources / name) for name in names
    }
    assert manifest["package"]["instrument_report_sha256"] == digest
    release = (output / "RELEASE.txt").read_text()
    if platform.system() == "Darwin":
        assert manifest["minimum_macos"] == "27.0"
        assert manifest["signing"] == "ad-hoc" and manifest["notarized"] is False
    assert ("Gatekeeper" in release) == (platform.system() == "Darwin")
    assert ("SmartScreen" in release) == (platform.system() == "Windows")
    assert manifest["commit"] in release and manifest["tree"] in release
    checksums = (output / "SHA256SUMS").read_text().splitlines()
    assert [line.split("  ", 1)[1] for line in checksums] == sorted(
        path.name for path in output.iterdir() if path.name != "SHA256SUMS")
    for line in checksums:
        expected, name = line.split("  ", 1)
        assert prepare.sha256(output / name) == expected
    # A later source build or Argyll download must preserve completed deliveries.
    for name in names[:2]:
        delivered = output / name
        saved = delivered.read_bytes()
        (sources / name).write_bytes(b"replacement input archive")
        assert delivered.read_bytes() == saved, name


@pytest.mark.parametrize("fault", [
    None, "changed", "argyll_link", "app_link", "app_changed_during_verification",
])
def test_release_source_set_requires_the_pinned_argyll_archive(tmp_path, monkeypatch, fault):
    import prepare_beta_assets as prepare

    from color_measurement import argyll_source

    directory = tmp_path / "sources"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    app = directory / "color_measurement-0.1.0.tar.gz"
    argyll = directory / "Argyll_V3.5.0_src.zip"
    for path, data, link in ((app, b"synthetic sdist", "app_link"),
                             (argyll, b"synthetic pinned Argyll", "argyll_link")):
        if fault == link:
            # Correct bytes, but behind a link that could change after verification.
            (outside / path.name).write_bytes(data)
            try:
                path.symlink_to(outside / path.name)
            except OSError:
                pytest.skip("This account cannot create symbolic links")
        else:
            path.write_bytes(data)
    monkeypatch.setattr(argyll_source, "ARCHIVE_SHA256", prepare.sha256(argyll))
    if fault == "changed":
        argyll.write_bytes(b"changed archive")
    monkeypatch.setattr(prepare, "verify_sources", lambda directory: {"archives": [
        {"filename": "qt.tar.xz", "sha256": "c" * 64},
        {"filename": "pyside.tar.xz", "sha256": "d" * 64}]})
    compared = []
    def verify_app(archive, root):
        compared.append(archive)
        if fault == "app_changed_during_verification":
            archive.write_bytes(b"changed after source comparison")
    monkeypatch.setattr(prepare, "verify_source_distribution", verify_app)
    if fault:
        message = {"app_link": "must not be a symlink",
                   "app_changed_during_verification": "changed during verification"}.get(
                       fault, "Argyll source archive differs")
        with pytest.raises(ValueError, match=message):
            prepare.corresponding_sources(directory, "0.1.0")
        assert compared == ([app] if fault == "app_changed_during_verification" else [])
    else:
        verified = prepare.corresponding_sources(directory, "0.1.0")
        assert verified == {app: prepare.sha256(app), argyll: prepare.sha256(argyll),
                            directory / "qt.tar.xz": "c" * 64,
                            directory / "pyside.tar.xz": "d" * 64}
        assert compared == [app]


@pytest.mark.parametrize("policy", ["adhoc", "certificate", "team"])
def test_release_signing_policy_requires_each_inspected_property(monkeypatch, policy):
    from subprocess import CompletedProcess

    import prepare_beta_assets as prepare

    properties = ["Signature=adhoc", "TeamIdentifier=not set",
                  "Identifier=" + prepare.BUNDLE_IDENTIFIER]
    if policy == "certificate":
        properties[0] = "Authority=Developer ID Application: Synthetic Test"
    elif policy == "team":
        properties[1] = "TeamIdentifier=SYNTHETIC"

    def inspected(command, **kwargs):
        details = "\n".join(properties) if "--display" in command else ""
        return CompletedProcess(command, 0, stdout="", stderr=details)

    monkeypatch.setattr(prepare.subprocess, "run", inspected)
    if policy == "adhoc":
        prepare.verify_adhoc_signature(Path("synthetic.app"))
    else:
        with pytest.raises(ValueError, match="intact ad-hoc beta seal"):
            prepare.verify_adhoc_signature(Path("synthetic.app"))


@pytest.mark.parametrize(("system", "approval"), [
    ("Darwin", "Gatekeeper"), ("Windows", "SmartScreen"), ("Linux", None),
])
def test_release_notice_describes_only_the_selected_target(system, approval):
    import prepare_beta_assets as prepare

    notice = prepare.release_notice(system, "27.0" if system == "Darwin" else None)
    assert ("Gatekeeper" in notice) == (approval == "Gatekeeper")
    assert ("SmartScreen" in notice) == (approval == "SmartScreen")
    if system == "Darwin":
        assert "macOS 27.0" in notice and "ad-hoc signed" in notice
        assert "not notarized" in notice
        assert "Never disable Gatekeeper or remove quarantine" in notice
    else:
        assert system + " packages are experimental" in notice
        assert "signed" not in notice
        # Their instructions are not in the macOS guide named by every release.
        link = prepare.DOCS_URL + prepare.RUNTIME_REQUIREMENTS
        assert link in notice and "getting-started" not in notice
        document, anchor = prepare.RUNTIME_REQUIREMENTS.split("#")
        headings = [line[3:].strip().lower().replace(" ", "-") for line in
                    (bench.ROOT / "docs" / document).read_text().splitlines()
                    if line.startswith("## ")]
        assert anchor in headings
        assert ("Never disable SmartScreen" in notice) == (system == "Windows")
