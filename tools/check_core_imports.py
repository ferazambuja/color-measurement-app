"""Check core imports in an isolated interpreter; never launch a product host.

The package gate runs this file with the core wheel installed and no extras.
These guards detect accidental import effects, not hostile Python code.
"""

from __future__ import annotations

import _thread
import argparse
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import json
import os
import pkgutil
import re
import socket
import subprocess
import sys
import threading
import tomllib
from pathlib import Path

# Distribution and import names are different contracts. SciPy also imports
# its required NumPy dependency; neither may become a core import dependency.
IMPORT_ROOTS = {
    "scipy": frozenset({"scipy", "numpy"}),
    "pyserial": frozenset({"serial"}),
    "pyside6": frozenset({"PySide6", "shiboken6"}),
    "numpy": frozenset({"numpy"}),
    "pywebview": frozenset({"webview"}),
    "colour-science": frozenset({"colour"}),
    "matplotlib": frozenset({"matplotlib"}),
}
PROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
MUTATIONS = frozenset({
    "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.link", "os.symlink",
    "os.chmod", "os.chown", "os.truncate", "os.utime", "os.setxattr", "os.removexattr",
    "os.chdir", "os.fchdir", "os.putenv", "os.unsetenv",
})
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class ImportEffectError(RuntimeError):
    """An import attempted a forbidden optional dependency or side effect."""


def optional_import_roots(project=PROJECT, extras=None):
    """Resolve selected declared extras without importing their dependencies.

    Known distribution aliases and required import dependencies are explicit.
    Other installed distributions may supply top_level.txt. An unresolvable
    mapping refuses rather than guessing that a distribution is an import name.
    None selects every extra, as required by the core-only installed gate.
    """
    groups = tomllib.loads(Path(project).read_text(encoding="utf-8"))["project"].get(
        "optional-dependencies", {})
    selected = set(groups) if extras is None else set(extras)
    unknown = selected - groups.keys()
    if unknown:
        raise ValueError(f"Unknown optional extras: {sorted(unknown)}")
    roots = set()
    for extra in sorted(selected):
        for requirement in groups[extra]:
            match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
            if match is None:
                raise ValueError(f"Invalid optional requirement: {requirement!r}")
            distribution = re.sub(r"[-_.]+", "-", match[1]).lower()
            names = IMPORT_ROOTS.get(distribution)
            if names is None:
                try:
                    declared = importlib.metadata.distribution(distribution).read_text(
                        "top_level.txt")
                except importlib.metadata.PackageNotFoundError:
                    declared = None
                names = frozenset(declared.split()) if declared else frozenset()
                if not names or any(not name.isidentifier() for name in names):
                    raise ValueError(
                        f"Declare import roots for optional distribution {distribution!r}")
            roots.update(names)
    return frozenset(roots)


def require_module_origin(module, expected_root):
    """Refuse a cached, editable or mixed-tree module outside the named root."""
    origin = getattr(module, "__file__", None)
    expected = Path(expected_root).resolve()
    if origin is None or not Path(origin).resolve().is_relative_to(expected):
        raise ImportEffectError(
            f"Import origin mismatch: {module.__name__} came from {origin!r}; expected {expected}")
    if any(not Path(path).resolve().is_relative_to(expected)
           for path in getattr(module, "__path__", ())):
        raise ImportEffectError(f"Import search path escapes expected root: {module.__name__}")
    return str(Path(origin).resolve())


def package_file_inventory(directory):
    """Hash every package source/resource recursively, excluding only bytecode.

    The expected inventory comes from the current build source, never from the
    wheel or an installed distribution's own manifest. Symlinks are refused so
    neither inventory can silently follow a different source tree.
    """
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ImportEffectError(f"Package source mismatch: invalid package directory {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ImportEffectError(f"Package source mismatch: symlink {path}")
        if path.is_file():
            files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "__init__.py" not in files:
        raise ImportEffectError(f"Package source mismatch: no source __init__.py in {root}")
    return files


def inventory_module_paths(files, package="color_measurement"):
    """Derive recursive import expectations from authenticated source files."""
    modules = {}
    for name in files:
        if name.endswith(".py"):
            parts = Path(name).with_suffix("").parts
            if parts[-1] == "__main__":
                continue
            suffix = parts[:-1] if parts[-1] == "__init__" else parts
            module = ".".join((package, *suffix))
            if module in modules:
                raise ImportEffectError(f"Package source mismatch: ambiguous module {module}")
            modules[module] = name
    return modules


def require_package_source(module, expected_source_root):
    """Bind the actual package files and cached origins to current source bytes."""
    package = module.__name__
    expected_directory = Path(expected_source_root).joinpath(*package.split("."))
    expected = package_file_inventory(expected_directory)
    actual_directory = Path(module.__file__).parent
    actual = package_file_inventory(actual_directory)
    if actual != expected:
        missing = sorted(expected.keys() - actual.keys())
        unexpected = sorted(actual.keys() - expected.keys())
        changed = sorted(name for name in actual.keys() & expected.keys()
                         if actual[name] != expected[name])
        raise ImportEffectError(f"Package source mismatch: missing={missing}; "
                                f"unexpected={unexpected}; changed={changed}")
    module_paths = inventory_module_paths(expected, package)
    for name, cached in tuple(sys.modules.items()):
        if name == package or name.startswith(package + "."):
            if name not in module_paths:
                raise ImportEffectError(f"Package source mismatch: unexpected cached module {name}")
            origin = require_module_origin(cached, actual_directory)
            if Path(origin) != (actual_directory / module_paths[name]).resolve():
                raise ImportEffectError(f"Package source mismatch: cached module path {name}")
    return expected


class ImportGuard(importlib.abc.MetaPathFinder):
    """Refuse effects and retain attempts even if imported code catches errors."""

    def __init__(self, optional=None):
        self.attempts: list[str] = []
        self.optional = optional_import_roots() if optional is None else frozenset(optional)

    def refuse(self, effect):
        self.attempts.append(effect)
        raise ImportEffectError(f"Import side effect: {effect}")

    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] in self.optional:
            self.refuse(f"optional import {fullname}")
        return None

    def audit(self, event, args):
        if event == "open":
            path, mode, flags = args
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & WRITE_FLAGS
            ) or (
                isinstance(flags, int) and getattr(os, "O_TMPFILE", 0)
                and flags & os.O_TMPFILE == os.O_TMPFILE
            ):
                self.refuse("file write")
            if isinstance(path, (str, bytes)):
                name = os.fsdecode(path)
                if name.startswith("/dev/") and name not in {"/dev/null", "/dev/urandom"}:
                    self.refuse("device open")
        if (event in MUTATIONS or event.startswith("subprocess.")
                or (event.startswith("socket.") and event != "socket.gethostname")
                or event in {"os.system", "os.fork", "os.forkpty", "os.posix_spawn"}
                or event.startswith(("os.exec", "os.spawn", "_thread.start"))
                or event == "ctypes.dlopen"):
            self.refuse(event)

    def install(self):
        if any(name.partition(".")[0] in self.optional for name in sys.modules):
            self.refuse("optional dependency already loaded")
        # Python 3.11 emits no thread-start audit event. Replace direct starts,
        # Thread.start and cached aliases in already imported standard modules.
        starts = [threading.Thread.start]
        for module in (_thread, threading):
            for name in ("start_new_thread", "start_new", "start_joinable_thread",
                         "_start_new_thread", "_start_joinable_thread"):
                value = getattr(module, name, None)
                if value is not None:
                    starts.append(value)

        def denied_thread(*args, **kwargs):
            self.refuse("thread start")

        for module in tuple(sys.modules.values()):
            if module is not None:
                for name, value in tuple(vars(module).items()):
                    if any(value is start for start in starts):
                        setattr(module, name, denied_thread)
        threading.Thread.start = denied_thread
        sys.meta_path.insert(0, self)
        sys.addaudithook(self.audit)

    def canaries(self):
        # Keep the finder canary meaningful even when a project has no extras.
        canary = "_cm_optional_import_canary"
        declared_optional = self.optional
        self.optional = self.optional | {canary}
        checks = {
            "write flags": lambda: os.open("import-canary", os.O_WRONLY | os.O_CREAT),
            "optional import": lambda: importlib.import_module(canary),
            "subprocess": lambda: subprocess.Popen([sys.executable, "-c", "pass"]),
            "network": lambda: socket.socket(),
            "file mutation": lambda: os.mkdir("import-canary-directory"),
            "low-level thread": lambda: _thread.start_new_thread(lambda: None, ()),
            "Thread.start": lambda: threading.Thread(target=lambda: None).start(),
        }
        if hasattr(_thread, "start_joinable_thread"):
            checks["joinable thread"] = lambda: _thread.start_joinable_thread(lambda: None)
        if hasattr(threading, "_start_new_thread"):
            checks["cached thread alias"] = lambda: threading._start_new_thread(lambda: None, ())
        for label, action in checks.items():
            try:
                action()
            except ImportEffectError:
                pass
            else:
                raise RuntimeError(f"Import guard failed canary: {label}")
        self.optional = declared_optional
        self.attempts.clear()
        return sorted(checks)


def check(package="color_measurement", *, expected_root=None, expected_source_root=None,
          optional=None):
    """Import the same package walk as MATLAB, excluding executable __main__."""
    guard = ImportGuard(optional)
    guard.install()
    canaries = guard.canaries()
    root = importlib.import_module(package)
    modules = [package]
    origins = {}
    if expected_root is not None:
        origins[package] = require_module_origin(root, expected_root)
    files = (require_package_source(root, expected_source_root)
             if expected_source_root is not None else None)
    module_paths = inventory_module_paths(files, package) if files is not None else None
    package_directory = Path(root.__file__).parent
    for item in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
        if item.name.rsplit(".", 1)[-1] != "__main__":
            module = importlib.import_module(item.name)
            if expected_root is not None:
                origins[item.name] = require_module_origin(module, expected_root)
            if module_paths is not None and (
                item.name not in module_paths
                or Path(module.__file__).resolve()
                != (package_directory / module_paths[item.name]).resolve()
            ):
                raise ImportEffectError(
                    f"Package source mismatch: imported module path {item.name}")
            modules.append(item.name)
    if module_paths is not None and set(modules) != set(module_paths):
        raise ImportEffectError("Package source mismatch: import walk differs from current source")
    if guard.attempts:
        raise ImportEffectError("Caught import side effects: " + ", ".join(guard.attempts))
    if any(name.partition(".")[0] in guard.optional for name in sys.modules):
        raise ImportEffectError("Import loaded an optional dependency")
    return {"origin": root.__file__, "modules": sorted(modules), "module_origins": origins,
            "canaries": canaries, "optional_imports": sorted(guard.optional),
            "source_files": files,
            "expected_source_root": str(Path(expected_source_root).resolve())
            if expected_source_root is not None else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    origins = parser.add_mutually_exclusive_group()
    origins.add_argument("--source-root", type=Path,
                         help="Exact source directory; default is this checkout's src")
    origins.add_argument("--installed-prefix", type=Path,
                         help="Explicit installed mode; every imported module must be below it")
    parser.add_argument("--expected-source-root", type=Path,
                        help="Required in installed mode: current build's src directory")
    parser.add_argument("--pyproject", type=Path, default=PROJECT)
    parser.add_argument("--extra", action="append", default=None,
                        help="Extra whose import roots are blocked; default is all extras")
    args = parser.parse_args(argv)
    if (args.installed_prefix is not None
            and args.installed_prefix.resolve() != Path(sys.prefix).resolve()):
        parser.error("--installed-prefix must match this interpreter's sys.prefix")
    if args.installed_prefix is not None and args.expected_source_root is None:
        parser.error("--installed-prefix requires --expected-source-root from the current build")
    if args.installed_prefix is None and args.expected_source_root is not None:
        parser.error("Source mode uses --source-root for its expected source")
    optional = optional_import_roots(args.pyproject, args.extra)
    expected = args.installed_prefix or args.source_root or PROJECT.parent / "src"
    if args.installed_prefix is None:
        sys.path.insert(0, str(expected.resolve()))
    print(json.dumps(check(expected_root=expected,
                           expected_source_root=args.expected_source_root or expected,
                           optional=optional)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
