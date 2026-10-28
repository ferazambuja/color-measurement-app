"""Shared platform identity and source-bound authentication for native workers.

A trusted local receipt detects source/binary drift. It does not authenticate
its author, establish compilation, or make arbitrary executable bytes safe.
Verification never runs an executable. A source checkout is required explicitly;
a packaged application without those build inputs cannot authenticate substitutes.
"""

import hashlib
import json
import platform
from pathlib import Path


def native_worker_name(name: str, *, report: dict | None = None) -> str:
    """Return the host basename; a supplied receipt must name this exact build OS."""
    system = platform.system()
    if system not in {"Darwin", "Linux", "Windows"}:
        raise ValueError("Native workers require macOS, Linux or Windows")
    if report is not None and (
        not isinstance(report, dict) or report.get("build_system") != system
    ):
        raise ValueError("Native worker build_system must match the current host")
    return name + (".exe" if system == "Windows" else "")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def check_build_sources(report: dict, *, source_root: Path) -> None:
    """Require the current complete native input set, without running any worker.

    This is shared by substituted-worker authentication and binary packaging.
    UI-only changes do not stale a native worker; native recipes, contribution
    files, vocabulary and build inputs do. A trusted receipt is still required.
    """
    source_root = Path(source_root).resolve()
    sources = report.get("original_check_source_sha256")
    require(isinstance(sources, dict) and all(isinstance(name, str) for name in sources),
            "Native worker requires a native source manifest")
    # Conservatively bind the native build/check inputs, not an individual
    # executable's dependency closure. The real driver build uses Python
    # transforms; the substitutes link project C against upstream headers.
    # A transform-only edit need not change those substitute bytes.
    # Builder/source hashes detect drift in a trusted local receipt; anyone
    # who can rewrite that receipt can copy them. They do not authenticate
    # the author, prove compilation occurred, or make arbitrary bytes safe.
    builder = "tools/argyll_native_check.py"
    vocabulary = "src/color_measurement/argyll_capability.py"
    contribution_prefix = None
    if "contribution" in report:
        contribution = report["contribution"]
        require(isinstance(contribution, dict),
                "Native worker native source contribution metadata is malformed")
        name = contribution["directory_name"]
        require(isinstance(name, str) and name not in ("", ".", "..")
                and "/" not in name and "\\" not in name,
                "Native worker has an invalid contribution directory")
        contribution_prefix = f"tools/native_argyll/contributions/{name}/"

    def produces_worker(name: str) -> bool:
        directory, _, entry = name.rpartition("/")
        if contribution_prefix is not None and name.startswith(contribution_prefix):
            return True  # Every selected series file is declared by its manifest.
        return not entry.startswith(".") and (
            name in (builder, vocabulary) or directory == "tools/native_argyll")

    contribution_names = {name for name in sources
                          if name.startswith("tools/native_argyll/contributions/")}
    require(not contribution_names or (contribution_prefix is not None and
            all(name.startswith(contribution_prefix) for name in contribution_names)),
            "Native worker native source contribution metadata is missing or inconsistent")

    recorded_names = {name for name in sources if produces_worker(name)}
    current_names = {name for name in (
        builder, vocabulary, *(source.relative_to(source_root).as_posix()
                   for source in (source_root / "tools/native_argyll").iterdir()
                   if source.is_file()))
        if produces_worker(name)}
    if contribution_prefix is not None:
        directory = source_root / contribution_prefix
        require(directory.is_dir() and not directory.is_symlink()
                and not directory.parent.is_symlink(),
                "Native worker native source contribution is missing or a symlink")
        contribution_files = list(directory.rglob("*"))
        require(not any(path.is_symlink() for path in contribution_files),
                "Native worker native source contribution contains a symlink")
        current_names.update(path.relative_to(source_root).as_posix()
                             for path in contribution_files if path.is_file())
        require(f"{contribution_prefix}manifest.json" in current_names,
                "Native worker native source contribution manifest is missing")
    require(current_names and not (current_names - recorded_names),
            "Native worker native source manifest omits current build inputs: "
            + ", ".join(sorted(current_names - recorded_names)))
    stale = []
    for name in recorded_names:
        source = source_root / name
        if name not in current_names:
            stale.append(f"{name} (removed since the build)")
        elif hashlib.sha256(source.read_bytes()).hexdigest() != sources[name]:
            stale.append(name)
    require(not stale,
            "Native worker predates changes to native source or build transforms: "
            + ", ".join(sorted(stale)))


def authenticate_stub(path: Path, *, source_root: Path, report_bytes: bytes | None = None) -> dict:
    """Verify one original worker against one report snapshot and current inputs."""
    path = Path(path).resolve()
    if source_root is None:
        raise RuntimeError("Substituted worker authentication requires an explicit source root")
    source_root = Path(source_root).resolve()
    from .argyll_source import ARCHIVE_SHA256, ARCHIVE_VERSION

    worker = path.name.removesuffix(".exe")
    fields = {
        "generic-session-stub-worker": ("generic_session", "executable_sha256"),
        "generic-chart-stub-worker": ("generic_session", "chart_executable_sha256"),
        "description-stub-worker": ("description_workflow", "executable_sha256"),
    }
    require(worker in fields, "Not a dedicated substituted worker")
    try:
        report = json.loads(report_bytes if report_bytes is not None
                            else (path.parent / "report.json").read_bytes())
        require(path.name == native_worker_name(worker, report=report),
                "Not the current platform's dedicated substituted worker")
        section, key = fields[worker]
        require(report["status"] == "passed" and report[section]["status"] == "passed",
                "Substituted worker requires a passing build report")
        require(report["archive_sha256"] == ARCHIVE_SHA256
                and report["argyll_version"] == ARCHIVE_VERSION,
                "Substituted worker source identity differs from the pinned archive")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == report[section][key],
                "Substituted worker digest differs from its build report")
        # The digest above binds the binary to its own receipt; it says nothing
        # about the checkout it is about to be run against. The report records the
        # sources it was compiled from, so a worker built before those sources
        # moved no longer represents this tree -- and a green run would then prove
        # the OLD native behaviour. Derived from the report rather than a
        # maintained list of files; the remedy is a rebuild.
        check_build_sources(report, source_root=source_root)
        return report
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"Cannot authenticate substituted worker: {path}") from error

