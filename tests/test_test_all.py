"""Failure-path checks for the hardware-free validation command."""

import ast
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import tarfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from conftest import write_stub_receipt

from color_measurement.native_receipt import native_worker_name

SPEC = importlib.util.spec_from_file_location(
    "test_all_runner", Path(__file__).resolve().parents[1] / "tools" / "test_all.py"
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
RENDERER_DEPENDENCIES = runner.require_renderer_dependencies
#: Every module the Tests stage must run, each in its own pytest process.
MODULES = runner.discover_test_modules(runner.ROOT)


@pytest.mark.parametrize("fault", [None, "wheel_terms", "sdist_dynamic", "missing_text"])
def test_package_licenses_follow_archive_scope(tmp_path, fault):
    """Catch repository-wide terms leaking into a Python-only wheel, frozen
    source terms, or a licence declared in metadata but absent from the archive.
    The real sdist-to-wheel build is exercised by the gate using this checker.
    """
    for kind in ("sdist", "wheel"):
        terms = "AGPL-3.0-only AND MIT AND CC-BY-SA-4.0"
        if kind == "sdist" or fault == "wheel_terms":
            terms += " AND GPL-2.0-or-later"
        metadata = f"Metadata-Version: 2.4\nLicense-Expression: {terms}\n"
        if kind == "sdist" and fault != "sdist_dynamic":
            metadata += "Dynamic: License-Expression\n"
        prefix = "package" if kind == "sdist" else "package.dist-info"
        files = {}
        for name in ("LICENSE", "LICENSES/GPL-2.0-or-later.txt",
                     "LICENSES/MIT.txt", "LICENSES/CC-BY-SA-4.0.txt"):
            metadata += f"License-File: {name}\n"
            if fault != "missing_text" or name == "LICENSE":
                path = f"{prefix}/{'licenses/' if kind == 'wheel' else ''}{name}"
                files[path] = (runner.ROOT / name).read_bytes()
        files[f"{prefix}/{'PKG-INFO' if kind == 'sdist' else 'METADATA'}"] = metadata.encode()
        if kind == "sdist":
            with tarfile.open(tmp_path / "package.tar.gz", "w:gz") as archive:
                for name, value in files.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(value)
                    archive.addfile(info, io.BytesIO(value))
        else:
            with zipfile.ZipFile(tmp_path / "package.whl", "w") as archive:
                for name, value in files.items():
                    archive.writestr(name, value)
    if fault is None:
        assert runner.check_package_licenses(tmp_path) == tmp_path / "package.whl"
    else:
        message = {"wheel_terms": "wheel: License-Expression",
                   "sdist_dynamic": "sdist: archive-dependent",
                   "missing_text": "sdist: missing or changed licence text"}[fault]
        with pytest.raises(RuntimeError, match=message):
            runner.check_package_licenses(tmp_path)


@pytest.fixture(autouse=True)
def isolated_runner_environment(monkeypatch):
    monkeypatch.delenv("COLOR_MEASUREMENT_GENERIC_STUB", raising=False)
    monkeypatch.delenv("COLOR_MEASUREMENT_CHART_STUB", raising=False)
    monkeypatch.delenv("CM_ARGYLL_ARCHIVE", raising=False)
    # These runner-control tests substitute every execution stage. Dependency
    # refusal and actual renderer report coverage are checked separately below.
    monkeypatch.setattr(runner, "require_renderer_dependencies", lambda: None)


def write_pytest_report(command, *, outcome="skipped", mat_outcome="skipped", environment=None):
    """One module's report: each anchor appears only in its own module's run.

    The joined Argyll C leaves are registered only when the gate exports the
    native report, so they are counted only then, as in a real run.
    """
    path = Path(command[command.index("--junitxml") + 1])
    module = Path(command[-1]).stem
    write_case_report(path, outcome=outcome, mat_outcome=mat_outcome,
                      count=int(module == "test_instrument"),
                      mat_count=int(module == "test_operation_exports"),
                      renderer_count=int(module == "test_desktop_client"),
                      native_cases="5" if "CM_RENDERER_NATIVE_REPORT" in (environment or {})
                      else None)
    return "1 passed in 0.01s\n"  # what the captured pytest run returns


def pytest_calls(calls):
    return [call for call in calls if "pytest" in (call[0] if isinstance(call, tuple) else call)]


#: The two substituted-worker anchors, both in tests/test_instrument.py because
#: both workers drive the SAME acquisition path. The coverage rule is unchanged:
#: a configured path is never evidence, only an executed case is.
GENERIC_ANCHORS = (
    "test_a_colorimeter_reads_over_the_generic_helper",
    "test_a_sheet_is_read_from_fiducials_the_operator_sighted",
)


def write_case_report(
    path, *, outcome="skipped", count=1, mat_outcome="skipped", mat_count=1, renderer_count=0,
    renderer_cases="55", native_cases=None,
):
    mat_child = "" if mat_outcome == "passed" else f'<{mat_outcome} message="fixture outcome"/>'
    mat_case = (
        '<testcase classname="tests.test_operation_exports" '
        'name="test_mat_native_quantities_metadata_and_exact_canonical_json">'
        f"{mat_child}</testcase>"
    )
    child = "" if outcome == "passed" else f'<{outcome} message="fixture outcome"/>'
    cases = "".join(
        f'<testcase classname="tests.test_instrument" name="{anchor}">'
        f"{child}</testcase>" * count
        for anchor in GENERIC_ANCHORS
    )
    executed = "".join(
        f'<property name="{name}" value="{value}"/>'
        for name, value in (("renderer_cases", renderer_cases),
                            ("renderer_native_cases", native_cases)) if value is not None
    )
    executed = f"<properties>{executed}</properties>" if executed else ""
    path.write_text(
        f"<testsuites><testsuite>{cases}{mat_case * mat_count}"
        + "".join(
            '<testcase classname="tests.test_desktop_client" '
            f'name="{name}">{executed if name == runner.RENDERER_DOM_ANCHOR else ""}'
            "</testcase>" * renderer_count
            for name in (
                "test_delayed_owner_return_reuses_mutation_uuid_and_dialog_wait_has_no_deadline",
                runner.RENDERER_DOM_ANCHOR,
            )
        ) + "</testsuite></testsuites>"
    )


@pytest.mark.parametrize("missing", ["node", "jsdom"])
def test_required_renderer_dependencies_refuse_missing_inputs(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(runner.shutil, "which", lambda _: None if missing == "node" else "/node")
    if missing == "node":
        (tmp_path / "tools/desktop_builder/node_modules/jsdom").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="Node.js" if missing == "node" else "locked desktop"):
        RENDERER_DEPENDENCIES(tmp_path)


def test_required_renderer_dependencies_pass_only_with_both_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/node")
    (tmp_path / "tools/desktop_builder/node_modules/jsdom").mkdir(parents=True)
    RENDERER_DEPENDENCIES(tmp_path)


def test_full_gate_refuses_missing_renderer_dependency_before_any_execution(monkeypatch, capsys):
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/uv")

    def refuse():
        raise RuntimeError("Required renderer dependency unavailable")

    monkeypatch.setattr(runner, "require_renderer_dependencies", refuse)
    monkeypatch.setattr(runner, "run",
                        lambda *a, **k: pytest.fail("Gate executed without renderer"))
    assert runner.main([]) == 1
    assert "Required renderer dependency unavailable" in capsys.readouterr().err


@pytest.mark.parametrize(
    "fault",
    [None, "missing", "skipped", "failure", "error", "duplicate", "no_count", "zero_cases"],
)
def test_required_renderer_report_cannot_pass_static_only_or_unexecuted_cases(tmp_path, fault):
    path = tmp_path / "renderer.xml"
    # An empty or wholly skipped renderer file still exits 0 under node --test;
    # the anchor's recorded count is the only evidence that renderer cases ran.
    write_case_report(
        path, count=0, mat_count=0, renderer_count=1,
        renderer_cases=None if fault == "no_count" else "0" if fault == "zero_cases" else "55",
    )
    report = ET.parse(path)
    suite = report.getroot().find("testsuite")
    case = suite.findall("testcase")[-1]
    if fault == "missing":
        suite.remove(case)
    elif fault == "duplicate":
        suite.append(ET.fromstring(ET.tostring(case)))
    elif fault not in (None, "no_count", "zero_cases"):
        ET.SubElement(case, fault, message="Synthetic missing DOM execution")
    report.write(path)
    if fault is None:
        assert "EXERCISED (55 renderer cases" in runner.renderer_coverage(path)
    else:
        with pytest.raises(RuntimeError, match="Required renderer"):
            runner.renderer_coverage(path)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("joined", [None, "0", "1", "4", "5", "five"])
def test_a_contribution_run_requires_the_joined_argyll_c_renderer_leaves(tmp_path, native, joined):
    """Unregistered leaves leave no skip behind: only their count shows they ran."""
    path = tmp_path / "renderer.xml"
    write_case_report(path, count=0, mat_count=0, renderer_count=1, native_cases=joined)
    if joined == "five" or native and joined != "5":
        with pytest.raises(RuntimeError, match="joined Argyll C"):
            runner.renderer_coverage(path, native=native)
        return
    summary = runner.renderer_coverage(path, native=native)
    assert "EXERCISED (55 renderer cases" in summary
    assert (f"Joined Argyll C renderer coverage: EXERCISED ({joined} cases" if joined not in
            (None, "0") else "Joined Argyll C renderer coverage: UNAVAILABLE") in summary


@pytest.mark.parametrize("family", ["xunit1", "xunit2"])
def test_real_pytest_case_properties_reach_renderer_coverage(tmp_path, family):
    source = tmp_path / "test_desktop_client.py"
    source.write_text(
        "def test_delayed_owner_return_reuses_mutation_uuid_and_dialog_wait_has_no_deadline():\n"
        "    pass\n"
        f"def {runner.RENDERER_DOM_ANCHOR}(record_property):\n"
        "    record_property('renderer_cases', 7)\n",
    )
    report = tmp_path / "pytest.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--confcutdir", str(tmp_path),
         "-o", "addopts=", "-o", f"junit_family={family}", "--junitxml", str(report), str(source)],
        cwd=tmp_path, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cases = list(ET.parse(report).iter("testcase"))
    assert len(cases) == 2
    assert "EXERCISED (7 renderer cases" in runner.renderer_coverage(report)


@pytest.mark.parametrize(
    "error", [RuntimeError("test failure"), subprocess.TimeoutExpired("test", 300)]
)
def test_stage_failure_stops_without_false_success(error, monkeypatch, capsys):
    called = []

    def fail(command, *, cwd, **kwargs):
        called.append((command, cwd))
        raise error

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", fail)
    assert runner.main() == 1
    assert len(called) == len(pytest_calls(called))
    if isinstance(error, subprocess.TimeoutExpired):
        assert 1 <= len(called) <= len(MODULES)  # Pending modules may be aborted.
    else:
        assert len(called) == len(MODULES)
    assert all(cwd == runner.ROOT for _, cwd in called)
    captured = capsys.readouterr()
    assert "FAIL: Tests" in captured.err
    assert "PASS: all" not in captured.out


def test_cancel_is_incomplete(monkeypatch, capsys):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", interrupt)
    assert runner.main() == 130
    captured = capsys.readouterr()
    assert "CANCELLED" in captured.err
    assert "PASS: all" not in captured.out


def test_each_module_runs_alone_and_every_failure_is_named(monkeypatch, tmp_path, capsys):
    """Catches modules sharing a process (a module that passes only after another
    ran), a failure hidden by later passes, and a merge that drops a module's
    cases from the report the coverage checks read."""
    called = []

    def record(command, *, cwd, extra_env=None, capture=False, **kwargs):
        called.append(command)
        if command[-1] in ("tests/test_science.py", "tests/test_workflow.py"):
            raise RuntimeError("module failed")
        return write_pytest_report(command, outcome="passed", mat_outcome="passed")

    monkeypatch.setattr(runner, "run", record)
    report = tmp_path / "pytest.xml"
    with pytest.raises(RuntimeError,
                       match=f"2 of {len(MODULES)} test modules failed: "
                             "tests/test_science.py, tests/test_workflow.py"):
        runner.run_tests(report, {}, 4)
    assert sorted(command[-1] for command in called) == MODULES
    assert all(sum(argument.startswith("tests/") for argument in command) == 1
               for command in called)
    # Each session has its own basetemp under the gate's directory: sessions that
    # share pytest's default root all rewrite its pytest-current symlink, and one
    # dies at sessionfinish resolving a link another has just replaced.
    basetemps = [Path(command[command.index("--basetemp") + 1]) for command in called]
    assert len(set(basetemps)) == len(called)
    assert all(tmp_path / "pytest-modules" in base.parents for base in basetemps)
    # Without failures the merged report holds each module's cases once.
    called.clear()
    monkeypatch.setattr(runner, "run", lambda command, **kwargs: write_pytest_report(
        command, outcome="passed", mat_outcome="passed"))
    (tmp_path / "pytest-modules").rename(tmp_path / "first-run")
    runner.run_tests(report, {}, 4)
    assert runner.native_stub_coverage(report, requested=True).endswith("(dedicated substituted "
                                                                          "worker; no hardware).")
    assert "EXERCISED" in runner.mat_coverage(report, requested=True)
    capsys.readouterr()


def test_parallel_discovery_and_reports_preserve_nested_module_identity(monkeypatch, tmp_path):
    """Use real pytest children: a nested failure cannot disappear from the gate,
    and duplicate basenames must retain both modules' cases in the merged XML."""
    modules = {
        "tests/test_root.py": "def test_root(): assert True\n",
        "tests/a/test_shared.py": "def test_a(): assert True\n",
        "tests/b/test_shared.py": "def test_b(): assert False\n",
        "tests/c/additional_test.py": "def test_suffix(): assert True\n",
    }
    for name, text in modules.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert runner.discover_test_modules(tmp_path) == sorted(modules)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="1 of 4 test modules failed: tests/b/test_shared.py"):
        runner.run_tests(tmp_path / "failed.xml", {}, 2)
    (tmp_path / "pytest-modules").rename(tmp_path / "failed-reports")
    (tmp_path / "tests/b/test_shared.py").write_text("def test_b(): assert True\n")
    runner.run_tests(tmp_path / "passed.xml", {}, 2)
    cases = ET.parse(tmp_path / "passed.xml").getroot().findall(".//testcase")
    assert {case.attrib["name"] for case in cases} == {
        "test_root", "test_a", "test_b", "test_suffix"}
    assert len(cases) == 4


@pytest.mark.parametrize("renderer_fails", [False, True])
def test_renderer_runs_exclusively_before_parallel_modules(monkeypatch, tmp_path, renderer_fails):
    """The renderer owns Node and its Python peer; other modules must not overlap it.

    Two ordinary modules still meet at a real worker barrier. Listing them first
    also catches a runner that merely preserves the input order in one pool.
    An ordinary renderer assertion failure must not suppress their execution.
    """
    renderer = "tests/test_desktop_client.py"
    normal = ["tests/test_first.py", "tests/test_second.py"]
    renderer_started = threading.Event()
    release_renderer = threading.Event()
    renderer_finished = threading.Event()
    together = threading.Barrier(2, timeout=5)
    completed = []

    class ObservedPool(runner.ThreadPoolExecutor):
        def submit(self, function, module):
            if module != renderer and not renderer_finished.is_set():
                release_renderer.set()  # Let the in-flight child finish before shutdown.
                pytest.fail("Ordinary module submitted while renderer was in flight")
            future = super().submit(function, module)
            if module == renderer:
                assert renderer_started.wait(5)
                actual_result = future.result

                def await_renderer(*args, **kwargs):
                    release_renderer.set()
                    return actual_result(*args, **kwargs)

                monkeypatch.setattr(future, "result", await_renderer)
            return future

    def child(command, **kwargs):
        module = command[-1]
        if module == renderer:
            renderer_started.set()
            assert release_renderer.wait(5), "Renderer was not awaited"
            if renderer_fails:
                renderer_finished.set()
                raise RuntimeError("renderer assertion failed")
        else:
            assert renderer_finished.is_set(), "Ordinary module overlapped renderer"
            together.wait()
        part = Path(command[command.index("--junitxml") + 1])
        suites = ET.Element("testsuites")
        suite = ET.SubElement(suites, "testsuite", name=module)
        ET.SubElement(suite, "testcase", classname=module, name="test_fixture")
        ET.ElementTree(suites).write(part)
        completed.append(module)
        if module == renderer:
            renderer_finished.set()
        return "1 passed"

    monkeypatch.setattr(runner, "ThreadPoolExecutor", ObservedPool)
    monkeypatch.setattr(runner, "run", child)
    report = tmp_path / "pytest.xml"
    if renderer_fails:
        with pytest.raises(RuntimeError, match="1 of 3 test modules failed: " + renderer):
            runner.run_tests(report, {}, 2, [*normal, renderer])
        assert set(completed) == set(normal)
        assert not report.exists()
    else:
        runner.run_tests(report, {}, 2, [*normal, renderer])
        assert completed[0] == renderer
        assert set(completed) == {*normal, renderer}
        assert {case.get("classname") for case in ET.parse(report).iter("testcase")} == {
            *normal, renderer}


@pytest.mark.parametrize("renderer_outcome", ["passed", "timeout", "deadline", "interrupt"])
def test_renderer_phase_shares_deadline_and_does_not_launch_after_abort(
    monkeypatch, tmp_path, renderer_outcome,
):
    """The exclusive phase neither resets the stage budget nor bypasses its stop signal."""
    renderer = "tests/test_desktop_client.py"
    clock = [100.0]
    launched = []

    def child(command, **kwargs):
        module = command[-1]
        launched.append(module)
        if module == renderer:
            assert kwargs["timeout"] == runner.PYTEST_STAGE_SECONDS
            clock[0] += 200
            if renderer_outcome == "timeout":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            if renderer_outcome == "deadline":
                clock[0] += runner.PYTEST_STAGE_SECONDS
            if renderer_outcome == "interrupt":
                raise KeyboardInterrupt
        else:
            assert kwargs["timeout"] == runner.PYTEST_STAGE_SECONDS - 200
        return write_pytest_report(command)

    monkeypatch.setattr(runner, "run", child)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    report = tmp_path / "pytest.xml"
    modules = ["tests/test_first.py", renderer]
    if renderer_outcome == "passed":
        runner.run_tests(report, {}, 2, modules)
        assert launched == [renderer, modules[0]]
    else:
        error = KeyboardInterrupt if renderer_outcome == "interrupt" else RuntimeError
        with pytest.raises(error):
            runner.run_tests(report, {}, 2, modules)
        assert launched == [renderer]
        assert not report.exists()


def test_missing_uv_is_not_success(monkeypatch, capsys):
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    assert runner.main() == 1
    assert "uv is required" in capsys.readouterr().err


def test_child_failure_carries_the_end_of_stderr(tmp_path):
    """A module that dies after its last test prints nothing more on stdout; its
    traceback is on stderr, and the failure the gate prints must carry it."""
    script = ("import sys; print('first', file=sys.stderr); "
              "print('last', file=sys.stderr); raise SystemExit(2)")
    with pytest.raises(RuntimeError, match=r"got 2.*\nstderr \(last lines\):\nfirst\nlast$"):
        runner.run([sys.executable, "-c", script], cwd=tmp_path, capture=True)


def test_child_failure_propagates_with_diagnostics(tmp_path, capsys):
    with pytest.raises(RuntimeError, match="got 3"):
        runner.run(
            [sys.executable, "-c", "print('child diagnostic'); raise SystemExit(3)"],
            cwd=tmp_path,
            capture=True,
        )
    assert "child diagnostic" in capsys.readouterr().out


def test_renderer_anchor_guard_matches_the_pytest_stage_deadline():
    """The renderer anchor's own hang guard copied the stage's 900 s and kept it
    when the stage moved to 1800 s, so it would have cut off a two-worker cohort
    the stage still allowed. Its value is read from source, not imported."""
    source = (runner.ROOT / "tests" / "test_desktop_client.py").read_text(encoding="utf-8")
    values = [node.value.value for node in ast.walk(ast.parse(source))
              if isinstance(node, ast.Assign)
              and [getattr(target, "id", None) for target in node.targets]
              == ["RENDERER_COHORT_SECONDS"]]
    assert values == [runner.PYTEST_STAGE_SECONDS]


@pytest.mark.parametrize("clock_seconds", [248.001, 1000.0])
def test_full_suite_has_headroom_without_extending_other_child_commands(monkeypatch, clock_seconds):
    """A two-worker suite exceeding fifteen minutes retains stage headroom."""
    actual_run = runner.run
    observed = []

    def child(command, **kwargs):
        timeout = kwargs.get("timeout", 300)
        observed.append(timeout)
        if "pytest" in command:
            if timeout <= 900:
                raise subprocess.TimeoutExpired(command, timeout)
            write_pytest_report(command)
        return ""

    monkeypatch.setattr(runner, "run", child)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "tree_state", lambda: ("a" * 40, "", "fixture"))
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    with monkeypatch.context() as clock:
        clock.setattr(runner.time, "monotonic", lambda: clock_seconds)
        assert runner.main(["--jobs", "2"]) == 0
    tests, others = observed[:len(MODULES)], observed[len(MODULES):]
    # Every module shares the stage's 1800 s deadline.
    # Subtraction on an unchanged coarse tick can round up by one binary64 step.
    ceiling = math.nextafter(1800.0, math.inf)
    assert all(900 < timeout <= ceiling for timeout in tests), tests
    assert others and all(timeout == 300 for timeout in others)
    # Explicit deadlines still propagate through the shared child runner.
    with pytest.raises(subprocess.TimeoutExpired):
        actual_run([sys.executable, "-c", "import time; time.sleep(10)"],
                   cwd=runner.ROOT, timeout=0.05)


@pytest.mark.skipif(os.name != "posix", reason="POSIX inherited group and pipe control")
def test_native_stage_has_its_own_deadline_without_extending_other_commands(monkeypatch, tmp_path):
    """A progressing native stage may exceed five minutes on the Windows runner."""
    deadlines = []

    def child(command, **kwargs):
        deadline = kwargs.get("timeout", 300)
        deadlines.append((command, deadline))
        if "tools/argyll_native_check.py" in command:
            if deadline <= 300:
                raise subprocess.TimeoutExpired(command, deadline)
        if "pytest" in command:
            return write_pytest_report(command, outcome="passed", mat_outcome="passed",
                                       environment=kwargs.get("extra_env"))
        return ""

    monkeypatch.setattr(runner, "run", child)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "tree_state", lambda: ("a" * 40, "", "fixture"))
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    assert runner.main(["--argyll-archive", str(tmp_path / "source.zip")]) == 0
    native = [deadline for command, deadline in deadlines
              if "tools/argyll_native_check.py" in command]
    ordinary = [deadline for command, deadline in deadlines
                if "tools/argyll_native_check.py" not in command and "pytest" not in command]
    assert len(native) == 1 and 300 < native[0] <= 900
    assert ordinary and all(deadline == 300 for deadline in ordinary)


def test_timeout_returns_even_when_a_descendant_keeps_the_captured_pipe(tmp_path):
    """The outer group owner retires the writer; run must not wait for its EOF.

    The writer's finite lifetime also makes the original bug fail an assertion
    instead of stranding the test. No process leaves the caller's owned group.
    """
    writer = (
        "import os,time; from pathlib import Path; "
        f"Path({str(tmp_path / 'writer.pid')!r}).write_text(str(os.getpid())); "
        "time.sleep(4)"
    )
    command = [sys.executable, "-c",
               "import os,subprocess,sys,time; from pathlib import Path; "
               f"Path({str(tmp_path / 'child.pid')!r}).write_text(str(os.getpid())); "
               f"subprocess.Popen([sys.executable,'-c',{writer!r}]); "
               "time.sleep(10)"]
    started = time.monotonic()
    pid = child = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            runner.run(command, cwd=tmp_path, capture=True, timeout=0.5)
        elapsed = time.monotonic() - started
        pid = int((tmp_path / "writer.pid").read_text())
        child = int((tmp_path / "child.pid").read_text())
        assert elapsed < 3, "Timeout waited for the descendant's inherited pipe"
        assert os.getpgid(pid) == os.getpgrp(), "Writer escaped outer group ownership"
        # The direct child was killed and reaped, not merely abandoned to its
        # ten-second sleep: the bounded wait alone would also raise TimeoutExpired.
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    finally:
        for name, recorded in (("writer.pid", pid), ("child.pid", child)):
            if recorded is None and (tmp_path / name).exists():
                recorded = int((tmp_path / name).read_text())
            if recorded is not None:
                try:
                    if os.getpgid(recorded) == os.getpgrp():
                        os.kill(recorded, 9)  # Only this control's finite, recorded processes.
                except ProcessLookupError:
                    pass


def test_module_timeout_aborts_other_active_commands_and_queued_modules(monkeypatch, tmp_path):
    """A shared-stage timeout must not leave unrelated modules running to 1800 s."""
    actual_run = runner.run
    entered = threading.Barrier(2, timeout=5)
    completed = []
    launched = []

    def inert_run(command, **kwargs):
        module = command[-1]
        launched.append(module)
        entered.wait()
        kwargs["timeout"] = 0.2 if module.endswith("first.py") else 10
        try:
            return actual_run([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)
        finally:
            completed.append(module)

    monkeypatch.setattr(runner, "run", inert_run)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="3 of 3 test modules failed"):
        runner.run_tests(tmp_path / "pytest.xml", {}, 2,
                         ["tests/test_first.py", "tests/test_second.py", "tests/test_queued.py"])
    assert time.monotonic() - started < 3
    assert set(launched) == set(completed) == {"tests/test_first.py", "tests/test_second.py"}
    assert not (tmp_path / "pytest.xml").exists(), "Failed stage published a passing report"


def test_child_source_path_isolation_and_expected_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "poisoned-source"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "poisoned-home"))
    text = runner.run(
        [sys.executable, "-c", "import os; print(os.getenv('PYTHONPATH')); raise SystemExit(2)"],
        cwd=tmp_path,
        expected=2,
        capture=True,
    )
    assert text.strip() == "None"


def test_full_check_cannot_inherit_a_pytest_subset(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_lab_black_and_white")
    text = runner.run(
        [sys.executable, "-c", "import os; print(os.getenv('PYTEST_ADDOPTS'))"],
        cwd=tmp_path,
        capture=True,
    )
    assert text.strip() == "None"


def test_source_runner_binds_selected_tree_for_pytest_and_its_subprocess(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    package = selected / "src/color_measurement"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("TREE = 'selected'\n")
    tests = selected / "tests"
    tests.mkdir()
    (tests / "test_origin.py").write_text(
        "import subprocess,sys,color_measurement\n"
        "def test_parent_and_child_are_selected():\n"
        "    assert color_measurement.TREE == 'selected'\n"
        "    subprocess.run([sys.executable,'-c',"
        "'import color_measurement; assert color_measurement.TREE == \\\"selected\\\"'],"
        "check=True)\n")
    monkeypatch.setattr(runner, "ROOT", selected)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "foreign"))
    monkeypatch.setenv("CM_TEST_IMPORT_MODE", "installed")
    runner.run_tests(tmp_path / "origin.xml", {}, 1)
    assert len(list(ET.parse(tmp_path / "origin.xml").iter("testcase"))) == 1


def test_source_runner_refuses_an_already_cached_foreign_package(tmp_path):
    foreign = tmp_path / "foreign/color_measurement"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text("# Cached foreign source\n")
    script = (
        "import runpy,sys\n"
        f"sys.path.insert(0,{str(foreign.parent)!r})\n"
        "import color_measurement\n"
        f"sys.path.insert(0,{str(runner.ROOT / 'tools')!r})\n"
        f"runpy.run_path({str(runner.ROOT / 'tools/test_all.py')!r})\n"
    )
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "Import origin mismatch" in result.stderr


@pytest.mark.parametrize("fault", [None, "missing_module", "foreign_module", "missing_canary",
                                   "unbound_source"])
def test_installed_import_walk_is_mandatory_complete_and_prefix_bound(tmp_path, monkeypatch, fault):
    build = tmp_path / "current build"
    source = build / "src/color_measurement"
    source.mkdir(parents=True)
    (build / "pyproject.toml").write_bytes((runner.ROOT / "pyproject.toml").read_bytes())
    monkeypatch.setattr(runner, "ROOT", build)
    wheel = tmp_path / "package.whl"
    payload = b"# Inventory fixture\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in ("__init__.py", "science.py", "nested/__init__.py", "nested/child.py",
                     "__main__.py", "nested/data/reference.csv"):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            archive.writestr("color_measurement/" + name, payload)
    files = runner.package_file_inventory(source)
    prefix = tmp_path / "installed environment"
    modules = {"color_measurement", "color_measurement.science",
               "color_measurement.nested", "color_measurement.nested.child"}
    origins = {name: str(prefix / "site-packages" / (name.replace(".", "/") + ".py"))
               for name in modules}
    canaries = ["write flags", "optional import", "subprocess", "network", "file mutation",
                "low-level thread", "Thread.start"]
    if fault == "missing_module":
        modules.remove("color_measurement.nested.child")
        origins.pop("color_measurement.nested.child")
    elif fault == "foreign_module":
        origins["color_measurement.science"] = "/foreign/tree/science.py"
    elif fault == "missing_canary":
        canaries.remove("network")
    optional = sorted(runner.optional_import_roots(runner.ROOT / "pyproject.toml"))
    proof = {"origin": str(prefix / "site-packages/color_measurement/__init__.py"),
             "modules": sorted(modules), "module_origins": origins, "canaries": canaries,
             "optional_imports": optional, "source_files": files,
             "expected_source_root": str((build / "src").resolve())}
    if fault == "unbound_source":
        proof.pop("source_files")
    walks = []

    class ReachedCLI(RuntimeError):
        pass

    def execute(command, *, cwd, **kwargs):
        if str(runner.ROOT / "tools/check_core_imports.py") in command:
            walks.append(command)
            assert command[1:3] == ["-I", "-B"]
            assert command[command.index("--installed-prefix") + 1] == str(prefix)
            assert command[command.index("--expected-source-root") + 1] == str(build / "src")
            assert command[command.index("--pyproject") + 1] == str(runner.ROOT / "pyproject.toml")
            return json.dumps(proof)
        if "--help" in command:
            raise ReachedCLI("Import walk completed before CLI")
        return ""

    monkeypatch.setattr(runner, "check_package_licenses", lambda *_: wheel)
    monkeypatch.setattr(runner, "run", execute)
    messages = {"missing_module": "omitted or added", "foreign_module": "foreign or missing",
                "missing_canary": "canaries are incomplete",
                "unbound_source": "not bound to current build source"}
    with pytest.raises(ReachedCLI if fault is None else RuntimeError,
                       match="Import walk completed" if fault is None else messages[fault]):
        runner.check_installed_wheel("uv", tmp_path, tmp_path)
    assert len(walks) == 1, "Deleting the installed import walk must fail this regression"


@pytest.mark.parametrize("fault", ["stale_code", "missing_resource", "extra_subpackage",
                                   "source_added_subpackage", "duplicate_entry"])
def test_wheel_authentication_uses_current_recursive_source_not_its_own_inventory(
    tmp_path, monkeypatch, fault,
):
    build = tmp_path / "current build"
    source = build / "src/color_measurement"
    source.mkdir(parents=True)
    files = {"__init__.py": b"# Current package\n", "nested/__init__.py": b"# Nested package\n",
             "nested/child.py": b"VALUE = 1\n", "nested/resources/reference.csv": b"400,0.1\n"}
    for name, raw in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    changed = dict(files)
    if fault == "stale_code":
        changed["nested/child.py"] = b"VALUE = 0\n"
    elif fault == "missing_resource":
        changed.pop("nested/resources/reference.csv")
    elif fault == "extra_subpackage":
        changed["obsolete/__init__.py"] = b"# Obsolete package\n"
    elif fault == "source_added_subpackage":
        (source / "added").mkdir()
        (source / "added/__init__.py").write_bytes(b"# Current source addition\n")
    wheel = tmp_path / "package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in changed.items():
            archive.writestr("color_measurement/" + name, raw)
        if fault == "duplicate_entry":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("color_measurement/nested/child.py", b"VALUE = 0\n")
    monkeypatch.setattr(runner, "ROOT", build)
    monkeypatch.setattr(runner, "check_package_licenses", lambda *_: wheel)
    monkeypatch.setattr(runner, "run", lambda *_, **__:
                        pytest.fail("Launched before authentication"))
    with pytest.raises(RuntimeError, match="duplicate package entries" if fault == "duplicate_entry"
                       else "differs from current build source"):
        runner.check_installed_wheel("uv", tmp_path, tmp_path)


def test_missing_wheel_cannot_pass(tmp_path):
    with pytest.raises(RuntimeError, match="exactly one wheel"):
        runner.check_installed_wheel("uv", tmp_path, tmp_path)


def test_requested_native_failure_stops_full_run(monkeypatch, tmp_path, capsys):
    called = []

    def record(command, *, cwd, extra_env=None, **kwargs):
        called.append(command)
        if "pytest" in command:
            assert extra_env == runner.source_environment(
                {"CM_ARGYLL_ARCHIVE": str(archive.resolve())})
            return write_pytest_report(command)
        else:
            assert extra_env == runner.source_environment()
        if "tools/argyll_native_check.py" in command:
            raise RuntimeError("native build failed")

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(
        runner, "check_installed_wheel", lambda *_: pytest.fail("ran after failure")
    )
    archive = tmp_path / "source archive.zip"
    arguments = ["--argyll-archive", str(archive)]
    assert runner.main(arguments) == 1
    # The native stage runs first, so nothing else ran after its failure.
    assert len(called) == 1
    assert str(archive) in called[-1]
    assert not {"--all-drivers", "--single-driver", "--family"}.intersection(called[-1])
    captured = capsys.readouterr()
    assert "FAIL: Offline Argyll native feasibility" in captured.err
    assert "PASS: all" not in captured.out


def test_the_summary_names_the_scope_it_actually_ran(monkeypatch, tmp_path, capsys):
    """A five-stage run was once reported as the full gate. The runner states
    which it was, from what it was given, and names the commit it checked."""
    generic = tmp_path / native_worker_name("generic-session-stub-worker")
    chart = tmp_path / native_worker_name("generic-chart-stub-worker")
    for path in (generic, chart):
        path.write_text("never executed by this runner test")
    write_stub_receipt(generic, chart)
    outcome = "skipped"
    commands = []

    def record(command, *, cwd, extra_env=None, **kwargs):
        if "pytest" in command:
            return write_pytest_report(command, outcome=outcome, mat_outcome=outcome,
                                       environment=extra_env)
        commands.append(list(command))
        if "tools/argyll_native_check.py" in command:
            output = Path(command[command.index("--output") + 1])
            output.mkdir()
            (output / "report.json").write_text("{}")

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    for key in ("COLOR_MEASUREMENT_GENERIC_STUB", "COLOR_MEASUREMENT_CHART_STUB"):
        monkeypatch.delenv(key, raising=False)
    assert runner.main([]) == 0
    out = capsys.readouterr().out
    assert "PASS: all 5 software checks" in out
    # The count is derived from the stages that ran, and these are exactly the
    # non-pytest stages: a dropped lint step or a lost --require must show here.
    documentation = ["tools/check_doc_snippets.py", "--check", "docs", *(
        flag for name in ("xyz_to_xyy", "xyy_to_xyz", "xyz_to_lab", "lab_to_xyz",
                          "reflectance_fraction", "delta_e_2000", "xyz_to_uvy",
                          "compare_spectra", "compare_xyz") for flag in ("--require", name))]
    assert [command[1:] for command in commands[:2]] == [["-m", "ruff", "check", "."],
                                                           documentation]
    assert len(commands) == 3 and commands[2][:3] == ["uv", "build", "--out-dir"]
    assert [line for line in out.splitlines() if line.startswith("[")] == [
        "[1/5] Tests", "[2/5] Lint", "[3/5] Documentation agrees with the tree",
        "[4/5] Build source distribution and wheel", "[5/5] Installed wheel and CLI recovery"]
    assert ("Scope: PARTIAL gate, not the full-form gate; not supplied: --argyll-archive, "
            "--argyll-contribution, --generic-stub, --chart-stub, --require-mat") in out
    outcome = "passed"
    full = ["--argyll-archive", str(tmp_path / "source.zip"),
            "--argyll-contribution", str(tmp_path),
            "--generic-stub", str(generic), "--chart-stub", str(chart)]
    # Everything but required MAT: MAT cases that may skip do not make a full form.
    assert runner.main(full) == 0
    out = capsys.readouterr().out
    assert "not supplied: --require-mat" in out
    commands.clear()
    assert runner.main([*full, "--require-mat"]) == 0
    out = capsys.readouterr().out
    assert "PASS: all 6 software checks" in out
    assert len(commands) == 4 and commands[0][1:3] == ["tools/argyll_native_check.py",
                                                        "--archive"]
    assert commands[0][-2:] == ["--contribution", str(tmp_path)]
    assert [command[1:] for command in commands[1:3]] == [["-m", "ruff", "check", "."],
                                                            documentation]
    assert "[1/6] Offline Argyll native feasibility" in out and "[6/6] Installed wheel" in out
    assert "Jobs: " in out and "pytest module processes at a time" in out
    assert "Scope: FULL-FORM gate" in out and "PARTIAL" not in out
    (tree,) = [line for line in out.splitlines() if line.startswith("Tree: ")]
    assert tree.endswith((" clean", " with uncommitted changes", "(checkout state unavailable)"))
    # The start line names the same tree when nothing changed during the run.
    (start,) = [line for line in out.splitlines() if line.startswith("Tree at start: ")]
    assert start.removeprefix("Tree at start: ") == tree.removeprefix("Tree: ")
    assert out.index(start) < out.index("Hardware-free software checks")
    assert "Skipped: none" in out


def test_optional_skipped_case_makes_a_fully_supplied_run_partial_and_is_named(
    monkeypatch, tmp_path, capsys
):
    """An optional native alert skip prevents FULL-FORM scope. Required renderer
    anchors must execute; separate coverage checks refuse their skips. Every
    remaining skip is named with its reason and makes the scope partial."""
    generic = tmp_path / native_worker_name("generic-session-stub-worker")
    chart = tmp_path / native_worker_name("generic-chart-stub-worker")
    for path in (generic, chart):
        path.write_text("never executed by this runner test")
    write_stub_receipt(generic, chart)
    reason = "Native alert checks require the app extra"

    def record(command, *, cwd, extra_env=None, **kwargs):
        if "tools/argyll_native_check.py" in command:
            output = Path(command[command.index("--output") + 1])
            output.mkdir()
            (output / "report.json").write_text("{}")
        if "pytest" not in command:
            return None
        if Path(command[-1]).stem != "test_desktop_host":
            return write_pytest_report(command, outcome="passed", mat_outcome="passed",
                                       environment=extra_env)
        Path(command[command.index("--junitxml") + 1]).write_text(
            '<testsuites><testsuite><testcase classname="tests.test_desktop_host" '
            'name="test_native_failure_alert_keeps_escape_and_default_acknowledgement">'
            f'<skipped message="{reason}"/></testcase>'
            "</testsuite></testsuites>"
        )
        return "1 skipped in 0.02s\n"

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    full = ["--argyll-archive", str(tmp_path / "source.zip"),
            "--argyll-contribution", str(tmp_path),
            "--generic-stub", str(generic), "--chart-stub", str(chart), "--require-mat"]
    assert runner.main(full) == 0
    out = capsys.readouterr().out
    identity = (
        "tests.test_desktop_host::test_native_failure_alert_keeps_escape_and_default_acknowledgement"
    )
    assert f"Skipped: 1 case(s): {identity} ({reason})" in out
    assert f"Scope: PARTIAL gate, not the full-form gate; skipped: {identity}" in out
    assert "FULL-FORM" not in out
    # The census itself: every skipped case and reason, nothing else.
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="tests.a" name="ran"></testcase>'
        '<testcase classname="tests.b" name="later"><skipped message="second"/></testcase>'
        '<testcase classname="tests.a" name="first"><skipped>text only</skipped></testcase>'
        "</testsuite></testsuites>"
    )
    assert runner.skipped_cases(report) == [
        ("tests.a::first", "text only"), ("tests.b::later", "second"),
    ]
    with pytest.raises(RuntimeError, match="skipped pytest cases"):
        runner.skipped_cases(tmp_path / "missing.xml")


@pytest.mark.parametrize("kind", ["tracked", "staged", "untracked"])
def test_tree_state_detects_content_changes_without_a_status_change(monkeypatch, tmp_path, kind):
    """Two dirty endpoints can have identical HEAD/status and different tested bytes."""
    def git(*arguments):
        return subprocess.run(
            ["git", *arguments], cwd=tmp_path, check=True, capture_output=True, text=True,
        ).stdout

    git("init", "--quiet")
    source = tmp_path / "source.txt"
    source.write_text("committed\n")
    git("add", ".")
    git("-c", "user.name=Gate test", "-c", "user.email=gate@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Baseline")
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    if kind == "untracked":
        source = tmp_path / "new source.txt"
    source.write_text("first edit\n")
    if kind == "staged":
        git("add", str(source))
        source.write_text("working copy stays the same\n")
    before = runner.tree_state()
    status = git("status", "--porcelain")
    assert runner.tree_state() == before

    source.write_text("second edit\n")
    if kind == "staged":
        git("add", str(source))
        source.write_text("working copy stays the same\n")
    after = runner.tree_state()
    assert git("status", "--porcelain") == status
    assert before is not None and after is not None
    assert before != after
    assert "changed during the run" in runner.tree_identity(before, after)


def test_the_tree_line_reports_a_tree_that_changed_while_the_stages_ran(monkeypatch, capsys):
    """One reading at the end described a tree nothing had been tested against
    whenever a commit or an edit landed mid-run."""
    readings = iter([("a" * 40, "", "first"), ("b" * 40, "", "second")])
    monkeypatch.setattr(runner, "tree_state", lambda: next(readings))
    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", lambda command, **_: write_pytest_report(command)
                        if "pytest" in command else None)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    for key in ("COLOR_MEASUREMENT_GENERIC_STUB", "COLOR_MEASUREMENT_CHART_STUB"):
        monkeypatch.delenv(key, raising=False)
    assert runner.main([]) == 0
    out = capsys.readouterr().out
    (tree,) = [line for line in out.splitlines() if line.startswith("Tree: ")]
    assert tree == f"Tree: changed during the run (start {'a' * 40} clean, end {'b' * 40} clean)"
    # The initial reading is retained; it does not certify later stage inputs.
    assert f"Tree at start: {'a' * 40} clean" in out.splitlines()
    clean = ("c" * 40, "", "same")
    assert runner.tree_identity(clean, ("c" * 40, " M docs/x.md", "different")) == (
        f"changed during the run (start {'c' * 40} clean, "
        f"end {'c' * 40} with uncommitted changes)")
    assert runner.tree_identity(clean, clean) == f"{'c' * 40} clean"
    assert runner.tree_identity(None, clean) == "unknown (checkout state unavailable)"


@pytest.mark.parametrize("options", [["--argyll-all-drivers"], ["--argyll-single-driver"],
                                    ["--argyll-all-drivers", "--argyll-single-driver"]])
def test_retired_native_scope_options_are_refused_before_running(monkeypatch, options):
    monkeypatch.setattr(runner, "run", lambda *a, **k: pytest.fail("Unexpected command"))
    with pytest.raises(SystemExit) as error:
        runner.main(["--argyll-archive", "archive.zip", *options])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "supplied", [None, "generic-session-stub-worker", "generic-session-stub-worker.exe"])
def test_complete_summary_reports_actual_native_stub_coverage(
    monkeypatch, tmp_path, capsys, supplied
):
    calls = []

    def record(command, *, cwd, extra_env=None, **kwargs):
        calls.append((command, extra_env))
        if "pytest" in command:
            return write_pytest_report(command, outcome="passed" if supplied else "skipped")

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    stub = tmp_path / (supplied or native_worker_name("generic-session-stub-worker"))
    stub.write_text("never executed by this test")
    write_stub_receipt(stub)
    if supplied and supplied != native_worker_name("generic-session-stub-worker"):
        with pytest.raises(SystemExit) as error:
            runner.main(["--generic-stub", str(stub)])
        assert error.value.code == 2 and calls == []
        return
    assert runner.main(["--generic-stub", str(stub)] if supplied else []) == 0
    out = capsys.readouterr().out
    if supplied:
        assert "Generic-ladder pytest: EXERCISED" in out
        assert all(env == runner.source_environment({"COLOR_MEASUREMENT_GENERIC_STUB": str(stub)})
                   for _, env in pytest_calls(calls))
        assert all(env == runner.source_environment() for _, env in calls[len(MODULES):])
    else:
        assert "Generic-ladder pytest: UNAVAILABLE" in out and "one case skipped" in out
        assert "--generic-stub PATH" in out
    assert "PASS: all 5 software checks" in out
    assert "MAT pytest: UNAVAILABLE" in out


@pytest.mark.parametrize("outcome", ["passed", "skipped"])
def test_requested_mat_coverage_requires_execution_before_gate_can_pass(
    monkeypatch, capsys, outcome,
):
    calls = []

    def record(command, **kwargs):
        calls.append(command)
        if "pytest" in command:
            return write_pytest_report(command, mat_outcome=outcome)

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    assert runner.main(["--require-mat"]) == (0 if outcome == "passed" else 1)
    captured = capsys.readouterr()
    if outcome == "passed":
        assert "MAT pytest: EXERCISED" in captured.out
    else:
        assert calls == pytest_calls(calls)
        assert "Requested MAT coverage was skipped" in captured.err
        assert "PASS: all" not in captured.out


@pytest.mark.parametrize("outcome,count,refusal", [
    ("passed", 0, "exactly one"), ("passed", 2, "exactly one"),
    ("failure", 1, "export case failed"), ("error", 1, "export case failed"),
    ("skipped", 1, "was skipped"),
])
def test_missing_failed_or_requested_but_skipped_mat_coverage_is_not_success(
    tmp_path, outcome, count, refusal,
):
    report = tmp_path / "pytest.xml"
    write_case_report(report, mat_outcome=outcome, mat_count=count)
    with pytest.raises(RuntimeError, match=refusal):
        runner.mat_coverage(report, requested=True)


def test_mat_coverage_detects_skipped_validation_beside_passing_roundtrip(tmp_path):
    report = tmp_path / "pytest.xml"
    write_case_report(report, mat_outcome="passed")
    additional = (
        '<testcase classname="tests.test_operation_exports" '
        'name="test_invalid_mat_payload_is_validation_error[empty]">'
        '<skipped/></testcase>'
        '<testcase classname="tests.test_operation_exports" '
        'name="test_failure_retains_payload_and_result_for_retry[mat]">'
        '<skipped/></testcase>'
    )
    report.write_text(report.read_text().replace("</testsuite>", additional + "</testsuite>"))
    summary = runner.mat_coverage(report, requested=False)
    assert "PARTIAL (1 exercised, 2 skipped)" in summary
    with pytest.raises(RuntimeError, match="Requested MAT coverage was skipped"):
        runner.mat_coverage(report, requested=True)


@pytest.mark.parametrize("outcome,count,refusal", [
    ("passed", 0, "exactly one"), ("passed", 2, "exactly one"),
    ("failure", 1, "integration case failed"), ("error", 1, "integration case failed"),
    ("skipped", 1, "was skipped"),
])
@pytest.mark.parametrize("family", ["generic", "chart"])
def test_missing_failed_or_requested_but_skipped_native_coverage_is_not_success(
    tmp_path, outcome, count, family, refusal,
):
    report = tmp_path / "pytest.xml"
    write_case_report(report, outcome=outcome, count=count)
    with pytest.raises(RuntimeError, match=refusal):
        runner.native_stub_coverage(report, requested=True, family=family)


def test_invalid_native_report_cannot_claim_coverage(tmp_path):
    report = tmp_path / "pytest.xml"
    for content in (None, "<broken"):
        if content is not None:
            report.write_text(content)
        with pytest.raises(RuntimeError, match="Cannot establish"):
            runner.native_stub_coverage(report, requested=False)


@pytest.mark.parametrize(
    "name", ["generic-worker-never-run", "missing/generic-session-stub-worker"])
def test_wrong_or_missing_native_helper_refused_before_execution(monkeypatch, tmp_path, name):
    path = tmp_path / name
    if not name.startswith("missing"):
        path.write_text("never execute")
    monkeypatch.setattr(runner, "run", lambda *a, **k: pytest.fail("Unexpected execution"))
    with pytest.raises(SystemExit) as error:
        runner.main(["--generic-stub", str(path)])
    assert error.value.code == 2


def test_explicit_stub_environment_is_child_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("COLOR_MEASUREMENT_GENERIC_STUB", "previous")
    text = runner.run(
        [sys.executable, "-c", "import os; print(os.getenv('COLOR_MEASUREMENT_GENERIC_STUB'))"],
        cwd=tmp_path, capture=True,
        extra_env={"COLOR_MEASUREMENT_GENERIC_STUB": "explicit"},
    )
    assert text.strip() == "explicit"
    assert runner.os.environ["COLOR_MEASUREMENT_GENERIC_STUB"] == "previous"


@pytest.mark.parametrize("source", ["argument", "environment"])
def test_both_stubs_propagate_together_and_report_executed_coverage(
    monkeypatch, tmp_path, capsys, source,
):
    """Two substituted workers, one acquisition path. They used to be two
    per-device families; they are now two sets of substituted instruments driven
    through the same ladder, and the propagation rule is unchanged."""
    generic = tmp_path / native_worker_name("generic-session-stub-worker")
    chart = tmp_path / native_worker_name("generic-chart-stub-worker")
    for path in (generic, chart):
        path.write_text("never executed by this runner test")
    write_stub_receipt(generic, chart)
    calls = []

    def record(command, *, cwd, extra_env=None, **kwargs):
        calls.append((command, extra_env))
        if "pytest" in command:
            return write_pytest_report(command, outcome="passed")

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    arguments = ["--generic-stub", str(generic)]
    # An explicit path wins over an inherited path without changing the parent.
    inherited = str(chart) if source == "environment" else "old-invalid-path"
    monkeypatch.setenv("COLOR_MEASUREMENT_CHART_STUB", inherited)
    if source == "argument":
        arguments += ["--chart-stub", str(chart)]
    assert runner.main(arguments) == 0
    assert all(environment == runner.source_environment({
        "COLOR_MEASUREMENT_GENERIC_STUB": str(generic),
        "COLOR_MEASUREMENT_CHART_STUB": str(chart),
    }) for _, environment in pytest_calls(calls))
    assert all(environment == runner.source_environment()
               for _, environment in calls[len(MODULES):])
    assert runner.os.environ["COLOR_MEASUREMENT_CHART_STUB"] == inherited
    output = capsys.readouterr().out
    assert "Generic-ladder pytest: EXERCISED" in output
    assert "Generic-ladder chart pytest: EXERCISED" in output
    assert "PASS: all 5 software checks" in output


def test_requested_but_skipped_anchor_stops_before_other_stages(
    monkeypatch, tmp_path, capsys,
):
    stub = tmp_path / native_worker_name("generic-session-stub-worker")
    stub.write_text("not executed")
    write_stub_receipt(stub)
    calls = []

    def record(command, **kwargs):
        calls.append(command)
        return write_pytest_report(command, outcome="skipped")

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: pytest.fail("ran after skip"))
    assert runner.main(["--generic-stub", str(stub)]) == 1
    assert calls == pytest_calls(calls)
    captured = capsys.readouterr()
    assert "Requested generic-ladder integration case was skipped" in captured.err
    assert "PASS: all" not in captured.out


@pytest.mark.parametrize("name", ["generic-worker-never-run", "generic-chart-stub-worker",
                                  "missing/generic-session-stub-worker"])
def test_wrong_helper_or_missing_path_is_refused_before_execution(
    monkeypatch, tmp_path, name,
):
    """A matching basename does not authenticate a worker, and the wrong worker's
    name is as wrong as no worker at all."""
    path = tmp_path / name
    if not name.startswith("missing"):
        path.write_text("never execute")
    monkeypatch.setattr(runner, "run", lambda *a, **k: pytest.fail("Unexpected execution"))
    monkeypatch.setenv("COLOR_MEASUREMENT_GENERIC_STUB", str(path))
    with pytest.raises(SystemExit) as error:
        runner.main([])
    assert error.value.code == 2


@pytest.mark.parametrize("replacement", ["test_other_module", "test_other_native_case"])
def test_junit_coverage_requires_its_exact_module_and_anchor(tmp_path, replacement):
    report = tmp_path / "pytest.xml"
    write_case_report(report, outcome="passed")
    original = ("test_instrument" if replacement == "test_other_module"
                else "test_a_colorimeter_reads_over_the_generic_helper")
    report.write_text(report.read_text().replace(original, replacement))
    with pytest.raises(RuntimeError, match="exactly one generic-ladder"):
        runner.native_stub_coverage(report, requested=True, family="generic")


@pytest.mark.parametrize("receipt", ["current", "stale", "unregistered"])
def test_unsupplied_workers_come_from_the_gates_own_native_build(
        monkeypatch, tmp_path, capsys, receipt):
    """Catches a second, separate worker build becoming necessary again, a built
    worker reaching pytest unauthenticated, and a gate that builds its workers but
    still reports their coverage as unsupplied."""
    environments = []

    def record(command, *, cwd, extra_env=None, **kwargs):
        if "tools/argyll_native_check.py" in command:
            output = Path(command[command.index("--output") + 1])
            output.mkdir()
            workers = [output / native_worker_name(worker) for _, worker in runner.STUB_WORKERS]
            for worker in workers:
                worker.write_text("never executed by this runner test")
            write_stub_receipt(*workers)
            if receipt == "stale":
                workers[1].write_text("rebuilt after its receipt")
        if "pytest" in command:
            environments.append(extra_env)
            # "unregistered": the renderer never saw the report, so the joined
            # leaves were not even registered; only their count can show it.
            return write_pytest_report(command, outcome="passed", mat_outcome="passed",
                                       environment=None if receipt == "unregistered"
                                       else extra_env)
        return ""

    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    code = runner.main(["--argyll-archive", str(tmp_path / "source.zip"),
                        "--argyll-contribution", str(tmp_path / "series"), "--require-mat"])
    out = capsys.readouterr()
    if receipt == "stale":
        assert code == 1 and not environments
        assert "Substituted worker digest differs from its build report" in out.err
        return
    if receipt == "unregistered":
        assert code == 1 and "Required joined Argyll C renderer cases did not run" in out.err
        return
    assert code == 0
    assert len(environments) == len(MODULES)
    assert all(Path(environment["COLOR_MEASUREMENT_GENERIC_STUB"]).name
               == native_worker_name("generic-session-stub-worker")
               and Path(environment["COLOR_MEASUREMENT_CHART_STUB"]).name
               == native_worker_name("generic-chart-stub-worker") for environment in environments)
    assert "Generic-ladder chart pytest: EXERCISED" in out.out
    # The joined renderer leaves bind the same build's report as its two workers.
    assert all(Path(environment["CM_RENDERER_NATIVE_REPORT"])
               == Path(environment["COLOR_MEASUREMENT_GENERIC_STUB"]).parent / "report.json"
               for environment in environments)
    assert "Joined Argyll C renderer coverage: EXERCISED (5 cases" in out.out
    assert "Scope: FULL-FORM gate" in out.out


def test_substitute_receipt_refuses_missing_changed_or_failed_artifacts(tmp_path):
    import json
    helper = tmp_path / native_worker_name("generic-session-stub-worker")
    helper.write_text("Inert test bytes, never an executable")
    with pytest.raises(RuntimeError, match="authenticate"):
        runner.authenticate_stub(helper)
    write_stub_receipt(helper)
    runner.authenticate_stub(helper)
    helper.write_text("Changed after the build receipt was written")
    with pytest.raises(RuntimeError, match="digest"):
        runner.authenticate_stub(helper)
    write_stub_receipt(helper)
    report = json.loads((tmp_path / "report.json").read_text())
    report["status"] = "failed"
    (tmp_path / "report.json").write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match="passing build"):
        runner.authenticate_stub(helper)


@pytest.mark.parametrize("field", ["archive_sha256", "argyll_version"])
def test_substitute_refuses_wrong_pinned_archive_identity_from_report_snapshot(tmp_path, field):
    from color_measurement.native_receipt import authenticate_stub

    helper = tmp_path / native_worker_name("generic-session-stub-worker")
    helper.write_text("Inert bytes; never executed")
    write_stub_receipt(helper)
    original = (tmp_path / "report.json").read_bytes()
    report = json.loads(original)
    # Explicit bytes, rather than a second mutable disk read, are authoritative.
    (tmp_path / "report.json").write_text("unread malformed replacement")
    assert authenticate_stub(helper, source_root=runner.ROOT, report_bytes=original) == report
    report[field] = "wrong pinned identity"
    with pytest.raises(RuntimeError, match="pinned archive"):
        authenticate_stub(helper, source_root=runner.ROOT,
                          report_bytes=json.dumps(report).encode())
    with pytest.raises(RuntimeError, match="dedicated substituted worker"):
        authenticate_stub(tmp_path / "production-worker", source_root=runner.ROOT,
                          report_bytes=original)


@pytest.mark.parametrize("name", ["", ".", "..", "../example", "other/example", "other\\example"])
def test_substitute_refuses_invalid_contribution_directory_before_file_access(tmp_path, name):
    from color_measurement.native_receipt import authenticate_stub

    helper = tmp_path / native_worker_name("generic-session-stub-worker")
    helper.write_text("Inert bytes; never executed")
    write_stub_receipt(helper)
    report = json.loads((tmp_path / "report.json").read_bytes())
    report["contribution"] = dict(directory_name=name)
    with pytest.raises(RuntimeError, match="invalid contribution directory"):
        authenticate_stub(helper, source_root=runner.ROOT,
                          report_bytes=json.dumps(report).encode())


@pytest.mark.parametrize("change", ["empty", "missing", "malformed", "changed", "removed",
                                   "transform", "builder", "mode_vocabulary"])
def test_substitute_requires_complete_current_native_source_identity(tmp_path, change):
    import json

    helper = tmp_path / native_worker_name("generic-session-stub-worker")
    helper.write_text("Inert bytes; never executed")
    write_stub_receipt(helper)
    receipt = tmp_path / "report.json"
    report = json.loads(receipt.read_text())
    sources = report["original_check_source_sha256"]
    if change == "empty":
        sources.clear()
    elif change == "missing":
        del sources["tools/native_argyll/session.c"]
    elif change == "malformed":
        report["original_check_source_sha256"] = []
    elif change == "changed":
        sources["tools/native_argyll/session.c"] = "0" * 64
    elif change == "transform":
        # Bind the whole build/check input set conservatively. This transform
        # affects the real driver library, not necessarily substitute bytes.
        sources["tools/native_argyll/legacy_scan_recognition.py"] = "0" * 64
    elif change == "builder":
        sources["tools/argyll_native_check.py"] = "0" * 64
    elif change == "mode_vocabulary":
        sources["src/color_measurement/argyll_capability.py"] = "0" * 64
    else:
        sources["tools/native_argyll/removed.c"] = "0" * 64
    receipt.write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match="native source"):
        runner.authenticate_stub(helper)


@pytest.mark.parametrize("change", ["nested_missing", "nested_removed",
                                   "missing_metadata", "malformed_metadata",
                                   "directory_symlink", "parent_symlink", "nested_symlink"])
def test_receipt_requires_complete_selected_contribution_inputs(monkeypatch, tmp_path, change):
    """Accept a complete series receipt; reject missing inputs or metadata.

    This isolates the verifier from any installed contribution. Actual prepare()
    receipts are checked in test_native_contribution.py.
    """
    import hashlib
    import json

    checkout = tmp_path / "checkout"
    names = ["tools/argyll_native_check.py", "tools/native_argyll/session.c",
             "src/color_measurement/argyll_capability.py",
             "tools/native_argyll/contributions/example/manifest.json",
             "tools/native_argyll/contributions/example/files/spectro/example.c"]
    for name in names:
        source = checkout / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("original build input")
    helper = tmp_path / native_worker_name("generic-session-stub-worker")
    helper.write_text("Inert bytes; never executed")
    write_stub_receipt(helper)
    receipt = tmp_path / "report.json"
    report = json.loads(receipt.read_text())
    sources = {name: hashlib.sha256((checkout / name).read_bytes()).hexdigest() for name in names}
    report["original_check_source_sha256"] = sources
    report["contribution"] = {"directory_name": "example"}
    receipt.write_text(json.dumps(report))
    monkeypatch.setattr(runner, "ROOT", checkout)
    runner.authenticate_stub(helper)
    nested = "tools/native_argyll/contributions/example/manifest.json"
    if change == "nested_missing":
        del sources[nested]
        receipt.write_text(json.dumps(report))
    elif change == "nested_removed":
        (checkout / nested).unlink()
    elif change in {"directory_symlink", "parent_symlink", "nested_symlink"}:
        original_path = checkout / {
            "nested_symlink": nested,
            "parent_symlink": "tools/native_argyll/contributions",
            "directory_symlink": "tools/native_argyll/contributions/example",
        }[change]
        target = tmp_path / "original-input"
        original_path.rename(target)
        original_path.symlink_to(target, target_is_directory=target.is_dir())
    else:
        if change == "missing_metadata":
            del report["contribution"]
        else:
            report["contribution"] = []
        receipt.write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match="native source") as error:
        runner.authenticate_stub(helper)
    if change == "nested_missing":
        assert nested in str(error.value)


@pytest.mark.parametrize("module", ["test_instrument", "test_instrument_frontend",
                                   "test_instrument_lifecycle"])
def test_direct_pytest_entrypoints_refuse_workers_without_receipts(monkeypatch, tmp_path, module):
    import importlib

    helper = tmp_path / native_worker_name("generic-session-stub-worker")
    helper.write_text("Inert bytes; never executed")
    monkeypatch.setenv("COLOR_MEASUREMENT_GENERIC_STUB", str(helper))
    tests = importlib.import_module(module)
    with pytest.raises(RuntimeError, match="authenticate"):
        if module == "test_instrument_lifecycle":
            tests.worker()
        else:
            tests.supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")


def test_lifecycle_authenticates_description_before_client_construction(monkeypatch, tmp_path):
    import test_instrument_lifecycle as lifecycle

    generic = tmp_path / native_worker_name("generic-session-stub-worker")
    description = tmp_path / native_worker_name("description-stub-worker")
    for path in (generic, description):
        path.write_text("Inert bytes; never executed")
    write_stub_receipt(generic, description)
    description.write_text("Replaced after receipt")
    monkeypatch.setenv("COLOR_MEASUREMENT_GENERIC_STUB", str(generic))
    monkeypatch.setattr(lifecycle.NativeDescriptionClient, "for_offline_tests",
                        lambda *a: pytest.fail("Constructed client before authentication"))
    with pytest.raises(RuntimeError, match="digest"):
        lifecycle.test_discovery_reaches_real_description_with_exact_effect_acceptance()


@pytest.mark.parametrize("scope", ["i1pro3", "i1pro", "all_public", None,
                                   "typo", "", True, [], {}])
def test_lifecycle_refuses_a_missing_description_worker_for_every_scope(
    monkeypatch, tmp_path, scope
):
    """No retired or malformed scope can turn missing authenticated evidence into a skip."""
    import json

    import test_instrument_lifecycle as lifecycle

    generic = tmp_path / native_worker_name("generic-session-stub-worker")
    generic.write_text("Inert bytes; never executed")
    write_stub_receipt(generic)
    receipt = tmp_path / "report.json"
    report = json.loads(receipt.read_text())
    if scope is not None:
        report["driver_scope"] = scope
    receipt.write_text(json.dumps(report))
    monkeypatch.setenv("COLOR_MEASUREMENT_GENERIC_STUB", str(generic))
    monkeypatch.setattr(lifecycle.NativeDescriptionClient, "for_offline_tests",
                        lambda *a: pytest.fail("Constructed client without a description worker"))
    try:
        lifecycle.test_discovery_reaches_real_description_with_exact_effect_acceptance()
    except pytest.skip.Exception:
        outcome = "skipped"
    except RuntimeError as error:
        assert "authenticate" in str(error)
        outcome = "refused"
    else:
        pytest.fail("ran with no description worker")
    assert outcome == "refused"


def test_contribution_gate_refuses_a_missing_archive():
    with pytest.raises(SystemExit) as error:
        runner.main(["--argyll-contribution", "series"])
    assert error.value.code == 2


def test_contribution_gate_forwards_the_selected_series(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runner, "run", lambda command, **kw: called.append(command))
    monkeypatch.setattr(runner, "run_tests", lambda *_: None)
    monkeypatch.setattr(runner, "check_installed_wheel", lambda *_: None)
    monkeypatch.setattr(runner, "mat_coverage", lambda *a, **k: "skipped")
    monkeypatch.setattr(runner, "native_stub_coverage", lambda *a, **k: "skipped")
    series = tmp_path / "series"
    runner.main(["--argyll-archive", str(tmp_path / "archive.zip"),
                 "--argyll-contribution", str(series)])
    native = called[0]
    assert native[native.index("--contribution") + 1] == str(series.absolute())
