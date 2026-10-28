"""Focused selection and source-input failures must not masquerade as acceptance."""

import hashlib
import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import conftest as inputs
import pytest
from conftest import write_stub_receipt

from color_measurement.native_receipt import native_worker_name

TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location("focused_runner", TOOLS / "test_scoped.py")
runner = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(TOOLS))
try:
    SPEC.loader.exec_module(runner)
finally:
    sys.path.remove(str(TOOLS))
#: The gate module whose child runner executes the focused modules.
gate = sys.modules["test_all"]


@pytest.fixture(autouse=True)
def isolated_inputs(monkeypatch):
    for name in ("COLOR_MEASUREMENT_GENERIC_STUB", "COLOR_MEASUREMENT_CHART_STUB",
                 "CM_ARGYLL_ARCHIVE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runner, "require_renderer_dependencies", lambda: None)


def write_report(path, modules, *, skip=None, failure=None):
    suite = ET.Element("testsuite")
    for module in modules:
        # `test_instrument` carries BOTH substituted-worker anchors, one per
        # worker, because both drive the same acquisition path.
        anchors = {
            "test_desktop_client": (
                "test_delayed_owner_return_reuses_mutation_uuid_and_dialog_wait_has_no_deadline",
                "test_actual_renderer_dialog_identity_and_camera_behaviors",
            ),
            "test_instrument": ("test_a_colorimeter_reads_over_the_generic_helper",
                                "test_a_sheet_is_read_from_fiducials_the_operator_sighted"),
            "test_operation_exports":
                ("test_mat_native_quantities_metadata_and_exact_canonical_json",),
        }.get(module, ("test_synthetic_case",))
        for anchor in anchors:
            case = ET.SubElement(suite, "testcase", classname=f"tests.{module}", name=anchor)
            if module == skip:
                ET.SubElement(case, "skipped", message="Synthetic missing optional input")
            if module == failure:
                ET.SubElement(case, "failure", message="Synthetic failed assertion")
    ET.ElementTree(suite).write(path)


def fake_run(monkeypatch, *, skip=None, failure=None):
    calls = []

    def execute(command, *, cwd, extra_env, **kwargs):
        calls.append((command, cwd, extra_env))
        modules = [Path(item).stem for item in command if item.startswith("tests/")]
        path = command[command.index("--junitxml") + 1]
        write_report(path, modules, skip=skip, failure=failure)
        return "1 passed in 0.01s\n"

    monkeypatch.setattr(gate, "run", execute)
    return calls


def test_profiles_cover_every_module_and_shared_consumer_boundaries():
    modules = runner.select_modules(tuple(runner.PROFILES))
    assert {f"tests/{name}.py" for name in modules} == set(
        gate.discover_test_modules(runner.ROOT))
    # One acquisition profile, because there is one acquisition path.
    selected = runner.select_modules(["instrument"])
    assert {"test_instrument", "test_instrument_frontend", "test_argyll_native",
            "test_operation_records", "test_operation_exports"} <= set(selected)
    assert not set(runner.SOURCE_TESTS) & set(selected)
    assert {"test_frontend", "test_instrument_frontend", "test_event_frames"} <= set(
        runner.select_modules(["frontend"])
    )
    assert "test_instrument" not in runner.select_modules(["science"])
    assert len(runner.select_modules(["records", "records"])) == len(runner.RECORDS)


@pytest.mark.parametrize("profile", ["application", "frontend"])
def test_ui_profiles_require_renderer_dependencies_before_running(profile, monkeypatch):
    calls = fake_run(monkeypatch)

    def refuse():
        raise RuntimeError("Required renderer dependency unavailable")

    monkeypatch.setattr(runner, "require_renderer_dependencies", refuse)
    assert runner.main([profile]) == 1
    assert not calls


def test_non_ui_science_scope_does_not_require_node_or_renderer(monkeypatch):
    calls = fake_run(monkeypatch)
    monkeypatch.setattr(runner, "require_renderer_dependencies",
                        lambda: pytest.fail("Non-UI scope requested renderer dependencies"))
    assert runner.main(["science"]) == 0
    assert calls


def test_ui_profile_refuses_renderer_skip_after_dependency_preflight(monkeypatch):
    fake_run(monkeypatch, skip="test_desktop_client")
    assert runner.main(["application"]) == 1


@pytest.mark.parametrize("names", [[], ["unknown"]])
def test_empty_and_unknown_selection_cannot_pass(names):
    with pytest.raises(RuntimeError):
        runner.select_modules(names)


@pytest.mark.parametrize("drift", [
    "test_new_contract.py", "new_contract_test.py",
    "nested/test_new_contract.py", "nested/new_contract_test.py", None,
])
def test_profile_inventory_drift_requires_an_explicit_mapping(tmp_path, drift):
    for module in runner.select_modules(tuple(runner.PROFILES)):
        path = tmp_path / f"tests/{module}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text("# Synthetic inventory only\n")
    if drift is not None:
        path = tmp_path / "tests" / drift
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Unmapped\n")
    else:
        (tmp_path / "tests/test_science.py").unlink()
    with pytest.raises(RuntimeError, match="inventory is stale") as error:
        runner.select_modules(["science"], root=tmp_path)
    if drift is not None:
        assert f"Unregistered: ['tests/{drift}']" in str(error.value)


def test_list_is_inert_and_states_full_acceptance_is_separate(monkeypatch, capsys):
    monkeypatch.setattr(gate, "run", lambda *a, **k: pytest.fail("Listing ran tests"))
    assert runner.main(["--list", "science"]) == 0
    output = capsys.readouterr().out
    assert "tests/test_science.py" in output and "tools/test_all.py" in output
    assert "test_instrument.py" not in output


@pytest.mark.parametrize("arguments", [
    [], ["unknown"], ["science", "--require-mat"],
    ["science", "--generic-stub", "generic-session-stub-worker"],
    ["science", "--argyll-archive", "source.zip"], ["--list", "--require-mat"],
])
def test_invalid_scope_or_irrelevant_option_refuses_before_child(monkeypatch, arguments):
    monkeypatch.setattr(gate, "run", lambda *a, **k: pytest.fail("Invalid scope ran tests"))
    assert runner.main(arguments) == 1


@pytest.mark.parametrize("variant", ["empty", "missing", "extra", "failed", "malformed"])
def test_report_cannot_hide_missing_or_failed_selected_modules(tmp_path, variant):
    report = tmp_path / "pytest.xml"
    modules = ("test_science", "test_workflow")
    actual = () if variant == "empty" else modules[:1] if variant == "missing" else (
        *modules, "test_other"
    ) if variant == "extra" else modules
    write_report(report, actual, failure="test_workflow" if variant == "failed" else None)
    if variant == "malformed":
        report.write_text("not XML")
    with pytest.raises((RuntimeError, ET.ParseError)):
        runner.check_report(report, modules)


def test_focused_union_runs_only_pytest_with_exact_modules_and_no_inherited_unrelated_stub(
    monkeypatch, capsys,
):
    monkeypatch.setenv("COLOR_MEASUREMENT_CHART_STUB", "/must/not/be/used/real-helper")
    calls = fake_run(monkeypatch)
    assert runner.main(["records", "science", "records", "--require-mat"]) == 0
    # One pytest process per selected module, each module exactly once.
    assert sorted(command[-1] for command, _, _ in calls) == sorted(
        f"tests/{name}.py" for name in runner.select_modules(["records", "science", "records"]))
    for command, cwd, environment in calls:
        assert command[:3] == [sys.executable, "-m", "pytest"]
        assert sum(item.startswith("tests/") for item in command) == 1
        assert cwd == runner.ROOT
        assert environment == gate.source_environment(
            {"COLOR_MEASUREMENT_GENERIC_STUB": "", "COLOR_MEASUREMENT_CHART_STUB": ""})
    output = capsys.readouterr().out
    assert "PASS: focused pytest" in output and "Full acceptance remains a separate" in output
    assert "all 5" not in output


@pytest.mark.parametrize("family", ["generic", "chart"])
@pytest.mark.parametrize("source", ["argument", "environment"])
def test_relevant_trusted_stub_is_forwarded_and_actual_anchor_is_required(
    tmp_path, monkeypatch, family, source,
):
    worker = {"generic": "generic-session-stub-worker",
              "chart": "generic-chart-stub-worker"}[family]
    helper = tmp_path / native_worker_name(worker)
    helper.write_text("Inert path: fake_run never executes it")
    write_stub_receipt(helper)
    # One acquisition profile; both workers substitute instruments into it.
    arguments = ["instrument"]
    if source == "argument":
        arguments += [f"--{family}-stub", str(helper)]
    else:
        monkeypatch.setenv(f"COLOR_MEASUREMENT_{family.upper()}_STUB", str(helper))
    calls = fake_run(monkeypatch)
    assert runner.main(arguments) == 0
    assert calls[0][2][f"COLOR_MEASUREMENT_{family.upper()}_STUB"] == str(helper.resolve())
    fake_run(monkeypatch, skip="test_instrument")
    assert runner.main(arguments) == 1


@pytest.mark.parametrize(
    "filename", ["generic-worker-never-run", "missing/generic-session-stub-worker"])
def test_invalid_native_path_refused_before_pytest(tmp_path, monkeypatch, filename):
    path = tmp_path / filename
    if path.parent == tmp_path:
        path.write_text("Never execute real helper")
    monkeypatch.setattr(gate, "run", lambda *a, **k: pytest.fail("Invalid helper ran tests"))
    assert runner.main(["instrument", "--generic-stub", str(path)]) == 1


def test_explicit_archive_reaches_selected_source_tests(tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    archive.write_bytes(b"Synthetic path-plumbing input; source verification is tested separately")
    calls = fake_run(monkeypatch)
    assert runner.main(["native", "--argyll-archive", str(archive)]) == 0
    assert calls[0][2]["CM_ARGYLL_ARCHIVE"] == str(archive.resolve())


def test_requested_mat_skip_fails_and_unrequested_skip_is_reported(monkeypatch, capsys):
    fake_run(monkeypatch, skip="test_operation_exports")
    assert runner.main(["records", "--require-mat"]) == 1
    assert runner.main(["records"]) == 0
    assert "SKIPPED test_operation_exports:" in capsys.readouterr().out


@pytest.mark.parametrize("error,code", [(RuntimeError("failed child"), 1),
                                      (subprocess.TimeoutExpired("pytest", 300), 1),
                                      (KeyboardInterrupt(), 130)])
def test_child_failure_timeout_or_interruption_is_incomplete(monkeypatch, capsys, error, code):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(gate, "run", fail)
    assert runner.main(["science"]) == code
    assert "PASS:" not in capsys.readouterr().out


def test_absent_default_archive_skips_but_explicit_missing_input_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(inputs, "ROOT", tmp_path)
    with pytest.raises(pytest.skip.Exception):
        inputs.resolve_argyll_archive()
    for supplied in ("", str(tmp_path / "missing.zip")):
        monkeypatch.setenv("CM_ARGYLL_ARCHIVE", supplied)
        with pytest.raises(pytest.fail.Exception, match="Explicit CM_ARGYLL_ARCHIVE"):
            inputs.resolve_argyll_archive()


def test_archive_override_is_authenticated_before_any_extraction(tmp_path, monkeypatch):
    supplied = tmp_path / "supplied.zip"
    supplied.write_bytes(b"Synthetic hash-validator input, never extracted")
    monkeypatch.setenv("CM_ARGYLL_ARCHIVE", str(supplied))
    with pytest.raises(pytest.fail.Exception, match="SHA-256"):
        inputs.resolve_argyll_archive()
    monkeypatch.setattr(inputs, "ARCHIVE_SHA256", hashlib.sha256(supplied.read_bytes()).hexdigest())
    assert inputs.resolve_argyll_archive() == supplied.resolve()
