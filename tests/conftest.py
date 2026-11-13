"""Shared optional source input; never discover devices or download an archive."""

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest
from check_core_imports import require_module_origin, require_package_source

ROOT = Path(__file__).resolve().parents[1]
IMPORT_MODE = os.environ.get("CM_TEST_IMPORT_MODE", "source")
if IMPORT_MODE not in {"source", "installed"}:
    raise RuntimeError(f"Unknown CM_TEST_IMPORT_MODE: {IMPORT_MODE!r}")

import color_measurement  # noqa: E402

# Standalone pytest must refuse a foreign editable package. Source runners set
# ROOT/src explicitly; installed pytest must opt in, use its own prefix, and
# match the current source tree that owns this test suite.
require_module_origin(color_measurement,
                      Path(sys.prefix) if IMPORT_MODE == "installed" else ROOT / "src")
if IMPORT_MODE == "installed":
    require_package_source(color_measurement, ROOT / "src")

from color_measurement.argyll_source import ARCHIVE_SHA256  # noqa: E402
from color_measurement.native_receipt import native_worker_name  # noqa: E402


def resolve_argyll_archive():
    supplied = os.environ.get("CM_ARGYLL_ARCHIVE")
    path = Path(supplied) if supplied is not None else (
        ROOT / "local/argyll-audit/Argyll_V3.5.0_src.zip"
    )
    if not path.is_file():
        if supplied is not None:
            pytest.fail(f"Explicit CM_ARGYLL_ARCHIVE is not a file: {path}")
        pytest.skip("Separately supplied pinned Argyll source archive is unavailable")
    if hashlib.sha256(path.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        pytest.fail("Argyll source archive SHA-256 does not match pinned 3.5.0")
    return path.resolve()


@pytest.fixture(scope="session")
def argyll_archive():
    return resolve_argyll_archive()


def write_stub_receipt(*paths):
    """Synthetic receipt for runner routing tests; these files are never executed."""
    import json
    for path in paths:
        receipt = path.parent / "report.json"
        report = json.loads(receipt.read_text()) if receipt.exists() else {
            "status": "passed", "archive_sha256": ARCHIVE_SHA256, "argyll_version": "3.5.0",
            "build_system": platform.system(),
            "generic_session": {"status": "passed"},
            "description_workflow": {"status": "passed"},
        }
        keys = {"generic-session-stub-worker": ("generic_session", "executable_sha256"),
                "generic-chart-stub-worker": ("generic_session", "chart_executable_sha256"),
                "description-stub-worker": ("description_workflow", "executable_sha256")}
        section, key = keys[path.name.removesuffix(".exe")]
        report[section][key] = hashlib.sha256(path.read_bytes()).hexdigest()
        # Mirrors argyll_native_check.py's original_check_source_sha256 recorder: everything
        # the worker is built from, including the Python that generates its C.
        # Narrower than that and these receipts would not satisfy the gate's own
        # authenticator, which is the behaviour they stand in for.
        report["original_check_source_sha256"] = {
            source.relative_to(ROOT).as_posix(): hashlib.sha256(source.read_bytes()).hexdigest()
            for source in (ROOT / "tools/argyll_native_check.py",
                           ROOT / "src/color_measurement/argyll_capability.py",
                           *sorted((ROOT / "tools/native_argyll").iterdir()))
            if source.is_file() and not source.name.startswith(".")
        }
        receipt.write_text(json.dumps(report))


_RUNNER = None


def authenticate_worker(path):
    """A matching basename does not authenticate a worker.

    Connected regressions assert the substituted worker's behaviour, so an
    unauthenticated or stale one turns this file into a check that cannot fail
    for the reason it exists. Reuses the gate's own helper rather than growing a
    second authentication path.
    """
    global _RUNNER
    if _RUNNER is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "cm_gate_runner", ROOT / "tools" / "test_all.py")
        assert spec is not None and spec.loader is not None
        _RUNNER = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_RUNNER)
    _RUNNER.authenticate_stub(path)


def supplied_stub(variable):
    """Resolve optional connected-test input and verify it before any launch."""
    value = os.environ.get(variable)
    if not value:
        pytest.skip(f"{variable} not supplied")
    path = Path(value).resolve()
    authenticate_worker(path)
    return str(path)


def native_display_unsupported():
    """Current native shape when no display selector is advertised."""
    return {"status": "unsupported", "native_code": 0x50000, "count": 0,
            "catalogue_id": None, "applied_choice_id": None,
            "readback": {"native_code": 0x50000, "technology_code": None,
                         "refresh_mode": None, "base_calibration_id": None}}


def retained_build_report(folder):
    """Inert report-loader input; neither worker is an executable."""
    import json

    folder.mkdir(parents=True, exist_ok=True)
    description = folder / native_worker_name("argyll-description-worker-never-run")
    description.write_bytes(b"Inert description input")
    session = folder / native_worker_name("generic-worker-never-run")
    session.write_bytes(b"Inert acquisition input")

    def link(path):
        return {"status": "passed", "executed": False,
                "executable_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    report = {
        "status": "passed", "argyll_version": "3.5.0", "archive_sha256": ARCHIVE_SHA256,
        "driver_scope": "all_public", "build_scope": "all_public",
        "build_system": platform.system(),
        "description_worker_build_link": link(description),
        "generic_worker_build_link": link(session),
        "driver_manifest": {"archive_sha256": ARCHIVE_SHA256,
                            "build_scope": "all_public", "instrument_types": []},
    }
    path = folder / "report.json"
    path.write_text(json.dumps(report))
    return path


def bind_cli_substitute(monkeypatch, worker, report_path):
    """Substitute only CLI binding; launch the authenticated worker at its safe path."""
    from types import SimpleNamespace

    from color_measurement.discovery import InstrumentBuild

    authenticate_worker(Path(worker))
    calls = []
    binding = SimpleNamespace(
        _helper=Path(worker),
        _helper_sha256=hashlib.sha256(Path(worker).read_bytes()).hexdigest(),
        _client=SimpleNamespace(
            _report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest()),
    )

    def from_report(path):
        calls.append(path)
        assert path == report_path
        return binding

    monkeypatch.setattr(InstrumentBuild, "from_report", from_report)
    return calls


def assert_validator_survives_optimization(module, name, good, bad):
    """Run retained proof tables without recompiling their source harness."""
    script = """
import importlib.util, json, sys
from pathlib import Path
path = Path(sys.argv[1])
sys.path.insert(0, str(path.parents[1]))
spec = importlib.util.spec_from_file_location("verdict_control", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    getattr(module, sys.argv[2])(json.load(sys.stdin))
except AssertionError:
    print("refused")
else:
    print("accepted")
"""
    for optimized in (False, True):
        for table, expected in ((good, "accepted"), (bad, "refused")):
            run = subprocess.run(
                [sys.executable, *(["-O"] if optimized else []), "-c", script,
                 module.__file__, name],
                input=json.dumps(table), capture_output=True, text=True, timeout=30,
            )
            assert run.returncode == 0, run.stderr
            assert run.stdout.strip() == expected, (optimized, expected, run.stdout)


def copy_proof_with_probe_mutation(proof, tmp_path, old, new):
    """A copy of a proof runner whose probe has `old` replaced by `new` exactly once.

    The repository module and its probe are read through the import spec, not
    `__file__`: tests point `__file__` at an earlier copy, and a second mutation
    must still start from the repository probe.
    """
    module = Path(proof.__spec__.origin)
    probe = module.with_name(module.stem + "_probe.c")
    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / module.name).write_bytes(module.read_bytes())
    harness = probe.read_text()
    assert harness.count(old) == 1
    (source / probe.name).write_text(harness.replace(old, new))
    return source / module.name


@pytest.fixture
def mat():
    """The optional MAT dependencies, or a skip: the default gate carries no mat
    extra and reports MAT UNAVAILABLE instead of failing MAT cases."""
    np = pytest.importorskip("numpy", reason="MAT tests require optional mat extra")
    scipy_io = pytest.importorskip("scipy.io", reason="MAT tests require optional mat extra")
    return np, scipy_io


@pytest.fixture(scope="session")
def prepared_production(argyll_archive, tmp_path_factory):
    """One current CR/PR source preparation for inert source-mutation checks."""
    import argyll_native_check as native

    source, _ = native.prepare(
        argyll_archive, tmp_path_factory.mktemp("production") / "build",
        contribution=native.NATIVE / "contributions/cr_pr")
    return source


@pytest.fixture
def mutate_production(prepared_production, tmp_path):
    """Make an owned source tree, replacing files before writing any C mutant.

    Unchanged files are hardlinked to avoid repeated full source copies. Mutated
    paths are unlinked first, so the authenticated shared preparation stays intact.
    No compiler or proof is allowed to write to this input tree.
    """
    import shutil

    outputs = []

    def mutate(paths, transform):
        target = tmp_path / f"production-mutant-{len(outputs)}"
        shutil.copytree(prepared_production, target, copy_function=os.link)
        outputs.append(target)
        changed = False
        for relative in paths:
            original = prepared_production / relative
            text = original.read_text(encoding="utf-8")
            replacement = transform(text, relative)
            if replacement != text:
                path = target / relative
                path.unlink()
                path.write_text(replacement, encoding="utf-8", newline="\n")
                changed = True
        assert changed, "C mutation did not change a production input"
        return target

    yield mutate
    for output in outputs:
        shutil.rmtree(output)
