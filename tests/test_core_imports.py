"""Discriminating guard tests; the package gate separately uses an installed core wheel."""

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from check_core_imports import (
    inventory_module_paths,
    optional_import_roots,
    package_file_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_core_imports.py"


def probe(tmp_path, source, *, before="", expected_root=None):
    package = tmp_path / "probe_package"
    package.mkdir()
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (package / "safe.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "__main__.py").write_text("raise RuntimeError('executed main')\n", encoding="utf-8")
    origin = repr(str(expected_root)) if expected_root is not None else "None"
    code = (
        "import runpy, sys, json\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        + before + "\n"
        + f"namespace = runpy.run_path({str(CHECKER)!r})\n"
        + "print(json.dumps(namespace['check']('probe_package', "
        + f"expected_root={origin})))\n"
    )
    return subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=tmp_path,
                          text=True, capture_output=True, timeout=20)


def test_walk_allows_read_only_imports_and_excludes_main(tmp_path):
    result = probe(tmp_path, "from pathlib import Path\nPath(__file__).read_bytes()\n"
                            "import socket\nsocket.gethostname()\n")
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert proof["modules"] == ["probe_package", "probe_package.safe"]
    assert {"write flags", "optional import", "subprocess", "low-level thread",
            "Thread.start", "network", "file mutation"} <= set(proof["canaries"])
    assert not (tmp_path / "import-canary").exists()
    assert not (tmp_path / "import-canary-directory").exists()


@pytest.mark.parametrize("effect", [
    "open('effect.txt', 'w')",
    "__import__('os').open('effect.txt', __import__('os').O_WRONLY | __import__('os').O_CREAT)",
    "__import__('os').mkdir('effect-directory')",
    "__import__('subprocess').Popen([__import__('sys').executable, '-c', 'pass'])",
    "__import__('socket').socket()",
    "__import__('_thread').start_new_thread(lambda: None, ())",
    "__import__('threading').Thread(target=lambda: None).start()",
    "__import__('scipy')",
    "__import__('serial')",
])
def test_effect_cannot_pass_even_if_import_catches_guard_error(tmp_path, effect):
    result = probe(tmp_path, f"try:\n    {effect}\nexcept RuntimeError:\n    pass\n")
    assert result.returncode != 0
    assert "Caught import side effects" in result.stderr
    assert not (tmp_path / "effect.txt").exists()
    assert not (tmp_path / "effect-directory").exists()


def test_cached_low_level_thread_alias_is_also_guarded(tmp_path):
    (tmp_path / "cached_alias.py").write_text(
        "from _thread import start_new_thread as start\n", encoding="utf-8")
    result = probe(tmp_path, "from cached_alias import start\nstart(lambda: None, ())\n",
                   before="import cached_alias")
    assert result.returncode != 0
    assert "Import side effect: thread start" in result.stderr


@pytest.mark.parametrize("dependency", ["scipy", "serial"])
def test_preloaded_optional_dependency_cannot_bypass_finder(tmp_path, dependency):
    result = probe(
        tmp_path, "",
        before=f"import types\nsys.modules[{dependency!r}] = types.ModuleType({dependency!r})")
    assert result.returncode != 0
    assert "optional dependency already loaded" in result.stderr


def test_declared_extras_resolve_distribution_aliases_and_required_imports(tmp_path, monkeypatch):
    lookups = []

    def unavailable_distribution(name):
        lookups.append(name)
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(
        "check_core_imports.importlib.metadata.distribution", unavailable_distribution)
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\n[project.optional-dependencies]\n'
                       'mat = ["scipy>=1.12,<2"]\n'
                       'app = ["pyserial>=3.5,<4"]\n'
                       'viewer = ["pywebview>=5", "colour-science>=0.4"]\n')
    assert optional_import_roots(project, ["mat"]) == {"scipy", "numpy"}
    assert optional_import_roots(project, ["app"]) == {"serial"}
    assert optional_import_roots(project, ["viewer"]) == {"webview", "colour"}
    assert optional_import_roots(project) == {"scipy", "numpy", "serial", "webview", "colour"}
    assert not lookups
    with pytest.raises(ValueError, match="Unknown optional extras"):
        optional_import_roots(project, ["missing"])
    project.write_text('[project]\n[project.optional-dependencies]\n'
                       'missing = ["cm-unavailable-distribution-with-an-alias>=1"]\n')
    with pytest.raises(ValueError, match="Declare import roots"):
        optional_import_roots(project)
    assert lookups == ["cm-unavailable-distribution-with-an-alias"]


def test_expected_origin_refuses_a_package_search_path_escape(tmp_path):
    result = probe(tmp_path, "__path__.append('/unrelated/package/tree')\n",
                   expected_root=tmp_path)
    assert result.returncode != 0
    assert "Import search path escapes expected root" in result.stderr


@pytest.mark.parametrize("mode,inside_prefix,fault,accepted", [
    ("source", False, None, False), ("installed", False, None, False),
    ("installed", True, None, True), ("unknown", True, None, False),
    ("installed", True, "stale", False), ("installed", True, "missing_resource", False),
])
def test_conftest_installed_mode_binds_current_source_as_well_as_prefix(
    tmp_path, mode, inside_prefix, fault, accepted,
):
    prefix = tmp_path / "installed-prefix"
    package_parent = prefix / "site-packages" if inside_prefix else tmp_path / "foreign"
    package = package_parent / "color_measurement"
    shutil.copytree(ROOT / "src/color_measurement", package,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    if fault == "stale":
        with (package / "application.py").open("a") as stream:
            stream.write("\n# Different previously built package bytes\n")
    elif fault == "missing_resource":
        next(path for path in (package / "data").rglob("*") if path.is_file()).unlink()
    script = (
        "import os, runpy, sys\n"
        f"sys.prefix={str(prefix)!r}\n"
        f"sys.path.insert(0,{str(ROOT / 'tools')!r})\n"
        f"sys.path.insert(0,{str(package_parent)!r})\n"
        f"os.environ['CM_TEST_IMPORT_MODE']={mode!r}\n"
        f"runpy.run_path({str(ROOT / 'tests/conftest.py')!r})\n"
    )
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert (result.returncode == 0) == accepted, result.stderr
    if not accepted:
        assert any(message in result.stderr for message in (
            "Import origin mismatch", "Unknown CM_TEST_IMPORT_MODE", "Package source mismatch"))


def test_source_import_walk_covers_the_real_current_package_and_declared_extras(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(CHECKER), "--source-root", str(ROOT / "src")],
        cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert "color_measurement.application" in proof["modules"]
    assert proof["optional_imports"] == sorted(optional_import_roots(ROOT / "pyproject.toml"))
    assert set(proof["module_origins"]) == set(proof["modules"])
    assert all(Path(origin).is_relative_to(ROOT / "src")
               for origin in proof["module_origins"].values())


def test_installed_cli_mode_cannot_call_a_source_tree_an_interpreter_prefix(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(CHECKER), "--installed-prefix", str(ROOT / "src")],
        cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "must match this interpreter's sys.prefix" in result.stderr


def test_installed_cli_requires_current_build_source_even_for_the_real_prefix(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(CHECKER), "--installed-prefix", sys.prefix],
        cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "requires --expected-source-root from the current build" in result.stderr


def test_recursive_inventory_includes_resources_and_excludes_only_bytecode(tmp_path):
    files = {"__init__.py": b"# Root\n", "nested/__init__.py": b"# Nested\n",
             "nested/child.py": b"VALUE = 1\n", "nested/resources/reference.csv": b"400,0.1\n",
             "nested/resources/NOTICE.md": b"Original data terms\n"}
    for name, raw in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__/ignored.pyc").write_bytes(b"Disposable bytecode")
    assert package_file_inventory(tmp_path) == {
        name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    assert inventory_module_paths(files, "probe_package") == {
        "probe_package": "__init__.py", "probe_package.nested": "nested/__init__.py",
        "probe_package.nested.child": "nested/child.py"}


@pytest.mark.parametrize("fault", [None, "stale", "missing_resource", "extra_nested",
                                   "cached_sibling"])
def test_import_walk_binds_installed_files_and_cached_modules_to_expected_source(
    tmp_path, fault,
):
    expected = tmp_path / "current source"
    package = expected / "probe_package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# Current root\n")
    (package / "safe.py").write_text("VALUE = 1\n")
    (package / "__main__.py").write_text("raise RuntimeError('executed main')\n")
    (package / "resources").mkdir()
    (package / "resources/reference.csv").write_text("400,0.1\n")
    prefix = tmp_path / "installed-prefix"
    installed_parent = prefix / "site-packages"
    installed = installed_parent / "probe_package"
    shutil.copytree(package, installed)
    if fault == "stale":
        (installed / "safe.py").write_text("VALUE = 0\n")
    elif fault == "missing_resource":
        (installed / "resources/reference.csv").unlink()
    elif fault == "extra_nested":
        (installed / "obsolete").mkdir()
        (installed / "obsolete/__init__.py").write_text("# Obsolete package\n")
    before = ""
    if fault == "cached_sibling":
        sibling = prefix / "other/probe_package/safe.py"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("VALUE = 0\n")
        before = ("import types\nforeign=types.ModuleType('probe_package.safe')\n"
                  f"foreign.__file__={str(sibling)!r}\n"
                  "sys.modules['probe_package.safe']=foreign\n")
    code = ("import runpy,sys,json\n"
            f"sys.path.insert(0,{str(installed_parent)!r})\n" + before
            + f"namespace=runpy.run_path({str(CHECKER)!r})\n"
            + "print(json.dumps(namespace['check']('probe_package',"
            + f"expected_root={str(prefix)!r},expected_source_root={str(expected)!r})))\n")
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    if fault is None:
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["source_files"] == package_file_inventory(package)
        assert receipt["modules"] == ["probe_package", "probe_package.safe"]
    else:
        assert result.returncode != 0
        assert ("Package source mismatch" in result.stderr
                or "Import origin mismatch" in result.stderr)
