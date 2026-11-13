"""Run all current software checks without connecting to an instrument.

From the checkout: uv run --locked python tools/test_all.py
Tests this interpreter/OS. CI also installs MAT dependencies and requires MAT tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.parser import BytesParser
from pathlib import Path

from check_core_imports import (
    inventory_module_paths,
    optional_import_roots,
    package_file_inventory,
    require_module_origin,
)

ROOT = Path(__file__).resolve().parents[1]
#: The parallel pytest stage's shared deadline, in seconds. A two-worker suite
#: needs more than 900 s on a shared host.
PYTEST_STAGE_SECONDS = 1800
#: Native source proofs, compilation and substituted protocols form one stage.
#: The full Windows stage exceeds the ordinary five-minute child-command bound.
NATIVE_STAGE_SECONDS = 900
sys.path.insert(0, str(ROOT / "src"))
if "color_measurement" in sys.modules:
    require_module_origin(sys.modules["color_measurement"], ROOT / "src")
#: The generic ladder's two substituted workers, by coverage family.
STUB_WORKERS = (("generic", "generic-session-stub-worker"),
                ("chart", "generic-chart-stub-worker"))


def run(
    command: list[str], *, cwd: Path, expected: int = 0, capture: bool = False,
    extra_env: dict[str, str] | None = None, include_stderr: bool = False,
    timeout: float = 300,
    stop: threading.Event | None = None,
) -> str:
    """Bound command waits without leaving the caller's owned process group.

    The outer gate owner retires descendants. This function kills and reaps its
    direct child on failure, but never waits forever for an inherited pipe that
    a descendant still holds. It does not establish standalone tree ownership.
    """
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    # A developer's filter must not turn the full check into a passing subset.
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("CM_TEST_IMPORT_MODE", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        environment.update(extra_env)
    if stop is not None and stop.is_set():
        raise RuntimeError("Tests stage aborted before command launch")
    child = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + timeout
        while True:
            if stop is not None and stop.is_set():
                raise RuntimeError("Tests stage aborted while command was running")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                output, errors = child.communicate(
                    timeout=remaining if stop is None else min(remaining, 0.2))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise
    except BaseException:
        child.kill()
        try:
            child.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            # POSIX communicate uses selectors; no reader thread holds these
            # streams. Windows readers may still hold their stream locks, so
            # closing them here could block again. Their daemon readers close
            # them on EOF when the outer owner retires the remaining writers.
            if os.name != "nt":
                for stream in (child.stdout, child.stderr):
                    if stream is not None:
                        stream.close()
            child.wait(timeout=1.0)
        raise
    if child.returncode != expected:
        if capture:
            print(output, end="")
            print(errors, end="", file=sys.stderr)
        # Once both streams go to one log they interleave out of order, so the
        # failure itself carries the end of stderr, where a crash's traceback is.
        tail = "\n".join(errors.strip().splitlines()[-25:]) if capture and errors.strip() else ""
        raise RuntimeError(f"Expected exit {expected}, got {child.returncode}: {command!r}"
                           + (f"\nstderr (last lines):\n{tail}" if tail else ""))
    if not capture:
        return ""
    # Diagnostics go to stderr by contract, so a check on what the CLI TOLD the
    # operator has to be able to read it.
    return output + errors if include_stderr else output


def require(condition: bool, message: str) -> None:
    """Keep smoke checks active even when Python is started with optimization."""
    if not condition:
        raise RuntimeError(message)


def require_renderer_dependencies(root=ROOT):
    """Required application checks cannot silently skip their JavaScript consumer."""
    require(shutil.which("node") is not None,
            "Required renderer checks need Node.js; install Node before running this gate")
    require((root / "tools/desktop_builder/node_modules/jsdom").is_dir(),
            "Required renderer checks need locked desktop builder dependencies; run "
            "npm ci --prefix tools/desktop_builder --ignore-scripts --no-audit --no-fund")


#: The DOM anchor records how many renderer cases it executed; ``node --test``
#: exits 0 for an empty or wholly skipped file, so a passed case alone is no count.
RENDERER_DOM_ANCHOR = "test_actual_renderer_dialog_identity_and_camera_behaviors"


def renderer_coverage(report, *, native=False):
    """Require actual bridge and DOM executions, including after dependency preflight.

    With ``native``, this run composed the CR/PR contribution and pointed the
    renderer at its report, so the joined Argyll C leaves must have run too: when
    the variable is unset they are not even registered, so only their count shows
    that they executed. The anchor validates each of the four CR/PR leaves and
    the configured i1Pro3 leaf exactly once before recording their count.
    """
    anchors = {
        "test_delayed_owner_return_reuses_mutation_uuid_and_dialog_wait_has_no_deadline",
        RENDERER_DOM_ANCHOR,
    }
    found = {}
    for case in ET.parse(report).iter("testcase"):
        if ("test_desktop_client" in case.get("classname", "").split(".")
                and case.get("name") in anchors):
            require(case.get("name") not in found, "Required renderer anchor is duplicated")
            found[case.get("name")] = case
    require(set(found) == anchors, "Required renderer bridge/DOM cases were not collected")
    for name, case in found.items():
        skipped = case.find("skipped")
        reason = skipped.get("message", "dependency unavailable") if skipped is not None else ""
        require(skipped is None,
                f"Required renderer case {name} skipped: {reason}")
        require(case.find("failure") is None and case.find("error") is None,
                f"Required renderer case {name} did not pass")
    properties = {
        item.get("name"): item.get("value")
        for item in found[RENDERER_DOM_ANCHOR].iter("property")
    }
    executed = properties.get("renderer_cases")
    require(executed is not None and executed.isdigit() and int(executed) >= 1,
            "Required renderer DOM anchor recorded no executed renderer case")
    joined = properties.get("renderer_native_cases", "0")
    require(joined.isdigit(), "Renderer DOM anchor recorded an invalid joined Argyll C count")
    require(not native or int(joined) == 5,
            "Required joined Argyll C renderer cases did not run")
    return (f"Renderer bridge/DOM coverage: EXERCISED ({int(executed)} renderer cases; "
            "software execution; no native UI proof).\n"
            + (f"Joined Argyll C renderer coverage: EXERCISED ({int(joined)} cases; "
               "authenticated substituted workers; no physical instrument)."
               if int(joined) else
               "Joined Argyll C renderer coverage: UNAVAILABLE (no CR/PR native build "
               "in this run; add --argyll-contribution)."))


def source_environment(extra=None):
    """Bind this checkout for source children and their ordinary subprocesses."""
    return {**(extra or {}), "PYTHONPATH": str(ROOT / "src"), "CM_TEST_IMPORT_MODE": "source"}


def authenticate_stub(path: Path) -> None:
    """Use the application verifier with this gate's exact source checkout."""
    from color_measurement.native_receipt import authenticate_stub as authenticate

    authenticate(path, source_root=ROOT)


def native_stub_coverage(report: Path, *, requested: bool, family: str = "generic") -> str:
    """Report actual pytest execution, never infer coverage from a configured path.

    Two substituted workers, not four. The two per-device anchors named tests for
    adapters that no longer exist; there is one acquisition path now, and these
    two workers substitute different instruments into it.
    """
    anchors = {
        "generic": ("test_instrument", "test_a_colorimeter_reads_over_the_generic_helper"),
        "chart": ("test_instrument", "test_a_sheet_is_read_from_fiducials_the_operator_sighted"),
    }
    module, anchor = anchors[family]
    labels = {"generic": "Generic-ladder", "chart": "Generic-ladder chart"}
    label = labels[family]
    subjects = {"generic": "generic-ladder", "chart": "generic-ladder chart"}
    subject = subjects[family]
    try:
        cases = [
            case for case in ET.parse(report).iter("testcase")
            if case.get("name") == anchor
            and case.get("classname", "").split(".")[-1] == module
        ]
    except (OSError, ET.ParseError) as error:
        raise RuntimeError(
            f"Cannot establish {subject} pytest coverage: {error}"
        ) from error
    require(len(cases) == 1,
            f"Pytest report must contain exactly one {subject} integration case")
    case = cases[0]
    require(case.find("failure") is None and case.find("error") is None,
            f"{label} integration case failed")
    if case.find("skipped") is not None:
        require(not requested, f"Requested {subject} integration case was skipped")
        return (
            f"{label} pytest: UNAVAILABLE (no substituted worker supplied; one case skipped). "
            f"Use --{family}-stub PATH or COLOR_MEASUREMENT_{family.upper()}_STUB."
        )
    return f"{label} pytest: EXERCISED (dedicated substituted worker; no hardware)."


def mat_coverage(report: Path, *, requested: bool) -> str:
    """Report the executed MAT round-trip, validation and publication cases."""
    anchor = "test_mat_native_quantities_metadata_and_exact_canonical_json"
    try:
        cases = [
            case for case in ET.parse(report).iter("testcase")
            if case.get("classname", "").split(".")[-1] == "test_operation_exports"
            and (
                case.get("name", "").startswith(("test_mat_", "test_invalid_mat_payload_"))
                or case.get("name") == "test_failure_retains_payload_and_result_for_retry[mat]"
            )
        ]
    except (OSError, ET.ParseError) as error:
        raise RuntimeError(f"Cannot establish MAT pytest coverage: {error}") from error
    require(sum(case.get("name") == anchor for case in cases) == 1,
            "Pytest report must contain exactly one canonical MAT round-trip case")
    require(all(case.find("failure") is None and case.find("error") is None for case in cases),
            "MAT export case failed")
    skipped = sum(case.find("skipped") is not None for case in cases)
    if skipped:
        require(not requested, f"Requested MAT coverage was skipped ({skipped}/{len(cases)} cases)")
        status = "UNAVAILABLE" if skipped == len(cases) else "PARTIAL"
        return (
            f"MAT pytest: {status} ({len(cases) - skipped} exercised, {skipped} skipped). "
            "Use uv run --locked --extra mat python tools/test_all.py --require-mat."
        )
    return f"MAT pytest: EXERCISED ({len(cases)} export cases; no MATLAB runtime required)."


def skipped_cases(report: Path) -> list[tuple[str, str]]:
    """Name every skipped case with its reason: a skipped test is scope that did not run.

    The coverage lines above answer for the MAT and substituted-worker anchors;
    this answers for everything else, so a renderer or native-framework proof
    that skipped for a missing dependency cannot hide inside a green run.
    """
    try:
        cases = list(ET.parse(report).iter("testcase"))
    except (OSError, ET.ParseError) as error:
        raise RuntimeError(f"Cannot establish skipped pytest cases: {error}") from error
    skipped = []
    for case in cases:
        skip = case.find("skipped")
        if skip is not None:
            identity = f"{case.get('classname', '?')}::{case.get('name', '?')}"
            skipped.append((identity, skip.get("message") or skip.text or "no reason recorded"))
    return sorted(skipped)


def discover_test_modules(root: Path) -> list[str]:
    """Return both default pytest filename patterns, retaining nested paths."""
    return sorted(path.relative_to(root).as_posix()
                  for path in (root / "tests").rglob("*.py")
                  if path.name.startswith("test_") or path.name.endswith("_test.py"))


def run_tests(report: Path, environment: dict[str, str], jobs: int,
              modules: list[str] | None = None) -> None:
    """Run each test module (default: all) in its own pytest process.

    At f2fc9765 on an 8-core host this took the stage from 584 s to 115 s. A
    process per module also proves that each module passes alone, which focused
    runs rely on: one shared process hid an import that worked only after an
    earlier module had extended ``sys.path``. The per-module reports are merged
    into ``report`` for the coverage checks. Ordinary failures are collected
    after every module runs; timeout or interruption aborts active and pending
    modules instead of waiting for unrelated work.

    The renderer module owns Node plus its Python peer, so it runs exclusively
    before the other modules use the ``jobs`` workers. Both phases share the
    same stage deadline, child supervision and report collection.
    """
    if modules is None:
        modules = discover_test_modules(ROOT)
    require(bool(modules), "No test modules found")
    environment = source_environment(environment)
    parts = report.parent / "pytest-modules"
    parts.mkdir()
    deadline = time.monotonic() + PYTEST_STAGE_SECONDS
    stop = threading.Event()

    def module_run(module: str) -> tuple[str, Path, str | None]:
        part = parts / Path(module).with_suffix(".xml")
        part.parent.mkdir(parents=True, exist_ok=True)
        if stop.is_set():
            return module, part, "Not started: tests stage aborted"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stop.set()
            return module, part, "Not started: shared tests deadline expired"
        # Each module gets its own basetemp. Concurrent sessions that share pytest's
        # default root all rewrite its pytest-current symlink, and one of them dies
        # at sessionfinish resolving a link another has just replaced
        # (_pytest/pathlib.py cleanup_dead_symlinks).
        try:
            output = run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                          "--basetemp", str(part.with_suffix("")),
                          "--junitxml", str(part), module],
                         cwd=ROOT, extra_env=environment, capture=True, include_stderr=True,
                         timeout=remaining, stop=stop)
        except subprocess.TimeoutExpired as error:
            stop.set()  # A deadline failure aborts the whole owned gate stage.
            return module, part, str(error)
        except RuntimeError as error:
            return module, part, str(error)
        lines = output.strip().splitlines()
        # The final count and every skip reason: a skipped test was not run.
        summary = [line for line in lines if line.startswith(("SKIPPED", "XFAIL", "XPASS"))]
        print("\n    ".join([f"{module}: {lines[-1] if lines else ''}", *summary]), flush=True)
        return module, part, None

    pool = ThreadPoolExecutor(jobs)
    try:
        renderer = "tests/test_desktop_client.py"
        results = []
        if renderer in modules:
            results.append(pool.submit(module_run, renderer).result())
        results.extend(future.result() for future in as_completed([
            pool.submit(module_run, module) for module in modules if module != renderer
        ]))
    finally:
        stop.set()
        pool.shutdown(cancel_futures=True)
    failed = sorted(module for module, _, error in results if error is not None)
    for module, _, error in sorted(results):
        if error is not None:
            print(f"FAIL {module}: {error}", flush=True)
    require(not failed, f"{len(failed)} of {len(modules)} test modules failed: {', '.join(failed)}")
    merged = ET.Element("testsuites", name="pytest tests")
    for _, part, _ in sorted(results):
        require(part.is_file(), f"Pytest wrote no report for {part.stem}")
        merged.extend(ET.parse(part).getroot())
    ET.ElementTree(merged).write(report, encoding="utf-8", xml_declaration=True)


def check_package_licenses(artifacts: Path) -> Path:
    """Check actual archives, including the wheel rebuilt from the sdist by uv."""
    wheels = list(artifacts.glob("*.whl"))
    sources = list(artifacts.glob("*.tar.gz"))
    require(len(wheels) == 1, "Build must produce exactly one wheel")
    require(len(sources) == 1, "Source distribution is missing")
    with tarfile.open(sources[0]) as source, zipfile.ZipFile(wheels[0]) as wheel:
        for kind, names, read, suffix, expected in (
            ("sdist", source.getnames(), lambda n: source.extractfile(n).read(),
             "/PKG-INFO", "AGPL-3.0-only AND MIT AND CC-BY-SA-4.0 AND GPL-2.0-or-later"),
            ("wheel", wheel.namelist(), wheel.read, "/METADATA",
             "AGPL-3.0-only AND MIT AND CC-BY-SA-4.0"),
        ):
            metadata_paths = [n for n in names if n.endswith(suffix)]
            require(len(metadata_paths) == 1, f"{kind}: missing or ambiguous package metadata")
            metadata_path = metadata_paths[0]
            metadata = BytesParser().parsebytes(read(metadata_path))
            require(metadata["License-Expression"] == expected,
                    f"{kind}: License-Expression does not describe its contents")
            if kind == "sdist":
                require("License-Expression" in metadata.get_all("Dynamic", []),
                        "sdist: archive-dependent licence must remain dynamic")
            for name in ("LICENSE", "LICENSES/GPL-2.0-or-later.txt",
                         "LICENSES/MIT.txt", "LICENSES/CC-BY-SA-4.0.txt"):
                require(name in metadata.get_all("License-File", []),
                        f"{kind}: missing licence file metadata: {name}")
                prefix = metadata_path.rsplit("/", 1)[0]
                path = f"{prefix}/{'licenses/' if kind == 'wheel' else ''}{name}"
                require(path in names and read(path) == (ROOT / name).read_bytes(),
                        f"{kind}: missing or changed licence text: {name}")
    return wheels[0]


def check_installed_wheel(uv: str, artifacts: Path, temporary: Path) -> None:
    """Install only the built wheel, then run its CLI outside the source checkout."""
    wheel = check_package_licenses(artifacts)
    expected_files = package_file_inventory(ROOT / "src/color_measurement")
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist()
                 if name.startswith("color_measurement/") and not name.endswith("/")]
        require(len(names) == len(set(names)), "Wheel contains duplicate package entries")
        actual_files = {name.removeprefix("color_measurement/"):
                        hashlib.sha256(archive.read(name)).hexdigest() for name in names}
    require(actual_files == expected_files,
            "Wheel package inventory differs from current build source")
    environment = temporary / "installed environment"
    run([uv, "venv", "--python", sys.executable, str(environment)], cwd=temporary)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    executable = scripts / ("color-measurement.exe" if os.name == "nt" else "color-measurement")
    run(
        [uv, "pip", "install", "--python", str(python), "--no-index", "--no-deps", str(wheel)],
        cwd=temporary,
    )
    # The installed prefix alone cannot distinguish a current build from an
    # old package already present there. Bind it to this build's source.
    imports = json.loads(run(
        [str(python), "-I", "-B", str(ROOT / "tools" / "check_core_imports.py"),
         "--installed-prefix", str(environment),
         "--expected-source-root", str(ROOT / "src"),
         "--pyproject", str(ROOT / "pyproject.toml")],
        cwd=temporary, capture=True,
    ))
    require(Path(imports["origin"]).resolve().is_relative_to(environment.resolve()),
            "Import walk did not use the isolated installed wheel")
    require(imports.get("source_files") == expected_files
            and imports.get("expected_source_root") == str((ROOT / "src").resolve()),
            "Installed core import proof is not bound to current build source")
    expected_modules = set(inventory_module_paths(expected_files))
    require(expected_modules == set(imports["modules"]),
            "Installed core import walk omitted or added wheel modules")
    require(set(imports["module_origins"]) == expected_modules
            and all(Path(origin).resolve().is_relative_to(environment.resolve())
                    for origin in imports["module_origins"].values()),
            "Installed core import walk contains foreign or missing module origins")
    required_canaries = {"write flags", "optional import", "subprocess", "network",
                         "file mutation", "low-level thread", "Thread.start"}
    expected_optional = sorted(optional_import_roots(ROOT / "pyproject.toml"))
    require(required_canaries <= set(imports["canaries"])
            and imports["optional_imports"] == expected_optional,
            "Installed core import walk or effect canaries are incomplete")

    def cli(*arguments: str, expected: int = 0, include_stderr: bool = False) -> str:
        return run([str(executable), *arguments], cwd=temporary, expected=expected,
                   capture=True, include_stderr=include_stderr,
                   extra_env={"CM_ARGYLL_BUILD_REPORT": ""})

    require("acquire" in cli("--help"), "Installed CLI help is missing acquire")
    devices = json.loads(cli("devices"))
    require(devices["provenance"] == "simulated", "Device listing lost simulation label")
    # A NAME TABLE, and it must say so: a client reading "instruments" as devices
    # found would report 43 attached meters on a machine with none. 38 stock
    # names plus the five contributed CR/PR types, and only those depend on a
    # build that composed the contribution.
    require(len(devices["instruments"]) == 43,
            f"Device listing names {len(devices['instruments'])} instruments, expected 43")
    contributed = {entry["name"] for entry in devices["instruments"]
                   if entry.get("requires_contribution") is True}
    require(contributed == {"instCR250", "instCR100", "instCR300", "instPR655", "instPR670"}
            and all(type(entry.get("requires_contribution")) is bool
                    for entry in devices["instruments"]),
            f"Only contributed CR/PR types may require the contribution: {sorted(contributed)}")
    require("static name table" in devices["instruments_are"]
            and "not queried" in devices["instruments_are"],
            "The instrument listing must say it is names, not discovery or availability")
    require(any(entry["instrument"] == "X-Rite i1 Pro 3" for entry in devices["instruments"]),
            "The instrument listing must carry resolved names, not only enum spellings")
    # The one thing that must never happen by accident: naming an instrument
    # without a report must REFUSE. The caller's report authority is cleared
    # above so this software-only check cannot inherit a physical worker.
    refusal = cli("acquire", "--device", "X-Rite DTP41", expected=1, include_stderr=True)
    require("No build report given" in refusal,
            f"Naming an instrument without --build-report must refuse explicitly: {refusal!r}")
    record_path = temporary / "reading café.json"
    stdout = cli(
        "acquire",
        "--device",
        "simulator",
        "--sample-id",
        "smoke 測定",
        "--white",
        "80",
        "100",
        "120",
        "--output",
        str(record_path),
    )
    record = json.loads(stdout)
    require(record == json.loads(record_path.read_bytes()), "Saved JSON differs from stdout")
    require(record["provenance"] == "simulated", "Observation lost simulation label")
    require(
        record["backend_reported"]["xyz"]["values"] == [20.0, 25.0, 10.0],
        "Unexpected synthetic native XYZ",
    )
    require(record["backend_reported"]["spectrum"] is None, "Simulator invented a spectrum")
    require(
        record["project_computed"]["lab"]["reference_white"] == [80.0, 100.0, 120.0],
        "Lab reference white was not retained",
    )

    csv_path = temporary / "reading.csv"
    exported = json.loads(
        cli("export", str(record_path), "--output", str(csv_path), "--format", "csv")
    )
    reloaded = json.loads(
        cli(
            "export",
            str(csv_path),
            "--input-format",
            "csv",
            "--output",
            str(temporary / "roundtrip.json"),
        )
    )
    require(record == exported == reloaded, "Installed JSON/CSV export changed the observation")

    original_bytes = record_path.read_bytes()
    failed_stdout = cli(
        "acquire", "--device", "simulator", "--output", str(record_path), expected=2
    )
    failed_record = json.loads(failed_stdout)
    require(record_path.read_bytes() == original_bytes, "Save failure overwrote the original file")
    require(
        failed_record["observation_id"] != record["observation_id"],
        "Separate acquisitions reused an observation ID",
    )
    recovery = temporary / "recovery.json"
    recovery.write_text(failed_stdout, encoding="utf-8")
    retried_path = temporary / "retried.json"
    retried = json.loads(cli("export", str(recovery), "--output", str(retried_path)))
    require(
        failed_record == retried == json.loads(retried_path.read_bytes()),
        "Save recovery did not retain the same completed observation",
    )
    # Exercise the installed acquisition surface with no native helper selected,
    # which also catches missing wheel package files. Two halves: the Demo
    # Demo drives a whole operation end to end without an instrument, and the
    # generic descriptor is built for several capability words so the capability
    # tables, models and correction reader all have to be present.
    run(
        [
            str(python),
            "-c",
            """
import json
import time
from color_measurement.argyll_capability import MODE_FLAGS, MODE_ROUTES
from color_measurement.demo import demo_frontends
from color_measurement.instrument import Instrument
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.operation_records import result_from_json

front = next(f for f in demo_frontends()
                 if json.loads(f.describe_json())["device_id"] == "demo:i1pro3")
snapshot = json.loads(front.describe_json())
device = snapshot["device_id"]
for mode, workflow, ids, layout in (
    ("reflective_spot", "spot", ["wheel 測定"], {}),
    ("reflective_strip", "strip", ["wheel first", "wheel middle", "wheel last"],
     {}),
):
    request = dict(schema_version=1, device_id=device,
                   capability_id=snapshot["capability_id"], mode=mode,
                   workflow=workflow, patch_ids=ids, options={}, layout=layout)
    operation = front.prepare(json.dumps(request))
    try:
        operation.dispatch()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            text = operation.poll_event_json()
            if not text:
                time.sleep(.001)
                continue
            event = json.loads(text)
            if event["kind"] == "instruction_required":
                operation.respond(operation.operation_id,
                                  event["payload"]["request_id"], "accept")
            if event["kind"] == "terminal":
                break
        else:
            raise RuntimeError("Installed Demo acquisition did not terminate")
        result = result_from_json(operation.result_json())
        if (result.status != "completed" or result.provenance != "simulated"
                or result.mode != mode or len(result.patches) != len(ids)):
            raise RuntimeError("Installed Demo acquisition changed mode or patch count")
    finally:
        operation.close()

# One descriptor per capability word, with no instrument opened: the offer is
# DERIVED, so this is the whole per-device contract the wheel has to carry.
meter = Instrument(helper="/nonexistent", instrument="X-Rite i1 Pro 3")
for word, expected in (
    (MODE_ROUTES["ref_spot"], "reflective_spot"),
    (MODE_ROUTES["emis_spot"] | MODE_FLAGS["emis_nonadaptive"], "emissive_spot_nonadaptive"),
    (MODE_ROUTES["trans_spot"], "transmission_spot"),
):
    front = InstrumentFrontend(meter, mode_capabilities=word, trigger_capabilities=0,
                               filter_capabilities=0, provenance="simulated")
    offered = json.loads(front.describe_json())
    names = [entry["mode"] for entry in offered["modes"]]
    if expected not in names:
        raise RuntimeError(f"Installed descriptor lost {expected}: {names}")
    if offered["contract"] != "argyll_generic_instrument_v1":
        raise RuntimeError("Installed descriptor lost its contract marker")
""",
        ],
        cwd=temporary,
    )
    print(
        "Installed CLI: labelled acquisition, JSON/CSV round trip, overwrite refusal, "
        "and save recovery passed. "
        "Installed Demo spot and strip acquisition and the generic descriptor passed.",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    from color_measurement.native_receipt import native_worker_name

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--argyll-archive",
        type=Path,
        help="Also run the offline platform native check on this pinned source ZIP",
    )
    parser.add_argument(
        "--argyll-contribution", type=Path,
        help="Compose this repository's driver contribution in the native stage; "
             "requires --argyll-archive and the broad build",
    )
    parser.add_argument(
        "--native-output", type=Path,
        help="Keep the native stage at this unused path for packaging; requires --argyll-archive",
    )
    parser.add_argument(
        "--generic-stub", type=Path,
        help="Run the generic-ladder pytest with an existing trusted "
             "generic-session-stub-worker",
    )
    parser.add_argument(
        "--chart-stub", type=Path,
        help="Run the generic-ladder chart pytest with an existing trusted "
             "generic-chart-stub-worker",
    )
    parser.add_argument(
        "--jobs", type=int, default=os.cpu_count() or 1,
        help="Test modules run at once in the pytest stage (default: CPU count)",
    )
    parser.add_argument(
        "--require-mat", action="store_true",
        help="Require actual MAT export test coverage; install the optional mat extra first",
    )
    args = parser.parse_args([] if argv is None else argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.argyll_contribution and not args.argyll_archive:
        parser.error("--argyll-contribution requires --argyll-archive")
    if args.native_output and not args.argyll_archive:
        parser.error("--native-output requires --argyll-archive")
    if args.native_output and args.native_output.exists():
        parser.error("--native-output must be an unused path")

    # The generic ladder's substituted workers. The rule is unchanged:
    # a matching basename does not authenticate a worker, so the name is required
    # and the build digests what it produced.
    generic_stubs = {}
    for name, worker in STUB_WORKERS:
        key = f"COLOR_MEASUREMENT_{name.upper()}_STUB"
        supplied = getattr(args, f"{name}_stub") or os.environ.get(key)
        path = Path(supplied).resolve() if supplied else None
        if path is not None and (path.name != native_worker_name(worker)
                                 or not path.is_file()):
            parser.error(f"Generic integration requires an existing dedicated {worker}")
        if path is not None:
            try:
                authenticate_stub(path)
            except RuntimeError as error:
                parser.error(str(error))
        generic_stubs[name] = path
    test_environment = {}
    for name, path in generic_stubs.items():
        if path is not None:
            test_environment[f"COLOR_MEASUREMENT_{name.upper()}_STUB"] = str(path)
    if args.argyll_archive:
        test_environment["CM_ARGYLL_ARCHIVE"] = str(args.argyll_archive.resolve())
    tree_at_start = tree_state()
    # Retain the initial checkout reading even if a stage is interrupted.
    # Later edits can change what stages see; this does not freeze their inputs.
    print(f"Tree at start: {tree_identity(tree_at_start, tree_at_start)}", flush=True)
    print(
        f"Hardware-free software checks | Python {platform.python_version()} | "
        f"{platform.system()} {platform.machine()}",
        flush=True,
    )
    uv = shutil.which("uv")
    if uv is None:
        print(
            "FAIL: uv is required. Run: uv run --locked python tools/test_all.py", file=sys.stderr
        )
        return 1
    stage = "temporary workspace"
    try:
        stage = "Required renderer dependencies"
        require_renderer_dependencies()
        stage = "temporary workspace"
        with tempfile.TemporaryDirectory(prefix="color-measurement-checks-") as directory:
            temporary = Path(directory)
            native_directory = (args.native_output.resolve() if args.native_output
                                else temporary / "native")
            artifacts = temporary / "dist"
            pytest_report = temporary / "pytest.xml"
            steps = [
                ("Tests", []),  # run_tests: one pytest process per module
                ("Lint", [sys.executable, "-m", "ruff", "check", "."]),
                (
                    "Documentation agrees with the tree",
                    [
                        sys.executable,
                        "tools/check_doc_snippets.py",
                        "--check",
                        "docs",
                        "--require",
                        "xyz_to_xyy",
                        "--require",
                        "xyy_to_xyz",
                        "--require",
                        "xyz_to_lab",
                        "--require",
                        "lab_to_xyz",
                        "--require",
                        "reflectance_fraction",
                        "--require",
                        "delta_e_2000",
                        "--require",
                        "xyz_to_uvy",
                        "--require",
                        "compare_spectra",
                        "--require",
                        "compare_xyz",
                    ],
                ),
                ("Build source distribution and wheel", [uv, "build", "--out-dir", str(artifacts)]),
            ]
            if args.argyll_archive:
                # First: the Tests stage uses the substituted workers it builds.
                steps.insert(
                    0,
                    (
                        "Offline Argyll native feasibility",
                        [
                            sys.executable,
                            "tools/argyll_native_check.py",
                            "--archive",
                            str(args.argyll_archive.resolve()),
                            "--output",
                            str(native_directory),
                            *(["--contribution", str(args.argyll_contribution.absolute())]
                              if args.argyll_contribution else []),
                        ],
                    )
                )
            # The installed-wheel stage below runs after the steps, so it is the +1.
            total = len(steps) + 1
            for index, (stage, command) in enumerate(steps, start=1):
                print(f"\n[{index}/{total}] {stage}", flush=True)
                start = time.monotonic()
                if stage == "Tests":
                    # Native proof tests compile and run many inert probes. The
                    # complete suite needs headroom on slower or shared hosts.
                    run_tests(pytest_report, test_environment, args.jobs)
                else:
                    run(command, cwd=ROOT, extra_env=source_environment(),
                        timeout=(NATIVE_STAGE_SECONDS
                                 if stage == "Offline Argyll native feasibility" else 300))
                if stage == "Offline Argyll native feasibility":
                    # An unsupplied worker comes from this build of this checkout,
                    # authenticated like a supplied one: no second build, and no
                    # worker older than the sources under test.
                    for name, worker in STUB_WORKERS:
                        built = native_directory / native_worker_name(worker)
                        if generic_stubs[name] is None and built.is_file():
                            authenticate_stub(built)
                            generic_stubs[name] = built
                            test_environment[f"COLOR_MEASUREMENT_{name.upper()}_STUB"] = str(
                                built)
                    # The joined renderer leaves bind this build's report and its
                    # two substituted workers; only a CR/PR composition has them.
                    if args.argyll_contribution:
                        native_report = native_directory / "report.json"
                        require(native_report.is_file(),
                                "The CR/PR native build left no report for the renderer")
                        test_environment["CM_RENDERER_NATIVE_REPORT"] = str(native_report)
                if stage == "Tests":
                    renderer_summary = renderer_coverage(
                        pytest_report,
                        native="CM_RENDERER_NATIVE_REPORT" in test_environment)
                    generic_summaries = [
                        native_stub_coverage(pytest_report,
                                             requested=generic_stubs[name] is not None,
                                             family=name)
                        for name in ("generic", "chart")
                    ]
                    mat_summary = mat_coverage(pytest_report, requested=args.require_mat)
                    skipped = skipped_cases(pytest_report)
                print(f"PASS: {stage} ({time.monotonic() - start:.1f}s)", flush=True)
            stage = "Installed wheel and CLI recovery"
            print(f"\n[{total}/{total}] {stage}", flush=True)
            check_installed_wheel(uv, artifacts, temporary)
            print(f"PASS: {stage}", flush=True)
    except KeyboardInterrupt:
        print(f"\nCANCELLED during {stage}; full check incomplete.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(
            f"\nFAIL: {stage}: {error}\nFull check incomplete; see output above.", file=sys.stderr
        )
        return 1
    print(f"\nPASS: all {total} software checks completed on this Python/OS.", flush=True)
    for summary in generic_summaries:
        print(summary)
    print(renderer_summary)
    print(mat_summary)
    # Every other skipped case by name: the native alert/menu cases skip without
    # the app extra (Node and the locked builder dependencies are required above,
    # before any stage), and a run that never executed them is not the full form
    # however green it looks.
    print("Skipped: none" if not skipped else
          f"Skipped: {len(skipped)} case(s): "
          + "; ".join(f"{identity} ({reason})" for identity, reason in skipped))
    if args.argyll_archive:
        print("Native-stub bridge: EXERCISED by the selected offline native checker.")
    if not args.argyll_archive:
        print("Native Argyll build check not selected; add --argyll-archive PATH.")
    # One line a receipt can quote, derived from what ran rather than remembered:
    # a five-stage run was once reported as the full gate. A supplied worker is an
    # exercised one, and so is required MAT: a skipped requested case failed the
    # run above.
    missing = [flag for flag, given in (
        ("--argyll-archive", args.argyll_archive),
        ("--argyll-contribution", args.argyll_contribution),
        ("--generic-stub", generic_stubs["generic"]),
        ("--chart-stub", generic_stubs["chart"]),
        ("--require-mat", args.require_mat),
    ) if not given]
    print(f"Tree: {tree_identity(tree_at_start, tree_state())}")
    # A receipt says how parallel its pytest stage ran: at the core count the
    # stage saturates a shared machine and wall-clock tests can trip.
    print(f"Jobs: {args.jobs} pytest module processes at a time")
    if missing:
        scope = "Scope: PARTIAL gate, not the full-form gate; not supplied: " + ", ".join(missing)
    elif skipped:
        scope = ("Scope: PARTIAL gate, not the full-form gate; skipped: "
                 + ", ".join(identity for identity, _ in skipped))
    else:
        scope = ("Scope: FULL-FORM gate (native stage with the contribution, both substituted "
                 "workers, required MAT, no skipped case).")
    print(scope)
    print("No hardware was accessed. Physical device behavior remains untested.", flush=True)
    return 0


def tree_state() -> tuple[str, str, str] | None:
    """HEAD, status and a content fingerprint; None if the checkout cannot be read.

    Status alone misses successive edits to an already dirty file. Include the
    index and tracked/nonignored untracked files, without following symlinks.
    This is an endpoint comparison, not an atomic or isolated execution snapshot.
    """
    def git(*arguments: str) -> bytes:
        return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True,
                              timeout=30, check=True).stdout

    try:
        head = git("rev-parse", "HEAD").decode("ascii").strip()
        status = os.fsdecode(git("status", "--porcelain"))
        digest = hashlib.sha256(git("ls-files", "--stage", "-z"))
        names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        for name in sorted(set(names.split(b"\0")) - {b""}):
            path = ROOT / os.fsdecode(name)
            digest.update(name + b"\0")
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                digest.update(b"missing\0")
                continue
            digest.update(str(mode).encode("ascii") + b"\0")
            if stat.S_ISLNK(mode):
                digest.update(hashlib.sha256(os.fsencode(os.readlink(path))).digest())
            elif stat.S_ISREG(mode):
                with path.open("rb") as source:
                    digest.update(hashlib.file_digest(source, "sha256").digest())
            else:
                # Do not read devices/FIFOs or pretend to snapshot a submodule.
                return None
        return head, status, digest.hexdigest()
    except (OSError, subprocess.SubprocessError):
        return None


def tree_identity(start: tuple[str, str, str] | None, end: tuple[str, str, str] | None) -> str:
    """The commit this run checked, compared at its start and its end.

    One reading at the end cannot say what the stages saw: a commit or an edit
    made while they ran leaves it describing a tree nothing was tested against.
    Two content readings detect differing endpoints, including files that stay
    dirty. Changes restored between readings and ignored inputs are not covered.
    """
    if start is None or end is None:
        return "unknown (checkout state unavailable)"
    if start != end:
        return (f"changed during the run (start {start[0]}"
                f"{' with uncommitted changes' if start[1] else ' clean'}, end {end[0]}"
                f"{' with uncommitted changes' if end[1] else ' clean'})")
    return f"{end[0]} {'with uncommitted changes' if end[1] else 'clean'}"

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
