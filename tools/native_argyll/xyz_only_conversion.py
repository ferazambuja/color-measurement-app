"""XYZ-only reads convert before the spectrum is dropped: i1Pro, i1Pro3, ColorMunki.

Each driver's spectral-to-XYZ transfer clears a patch's spectrum inside its loop
when spectral output was not requested (i1pro_imp.c:10513-10514,
i1pro3_imp.c:4881-4882, munki_imp.c:8145-8146) and only after the loop applies
the requested calibration-standard conversion (:10522, :4890, :8155) and the
custom filter (:10526, :4894, :8159). Both converters skip a patch without a
spectrum (xrga.c:200-202, inst.c:2198-2202), so on an XYZ-only read neither is
applied, no error is returned, and the driver still reports the requested
standard as effective (i1pro.c:848, i1pro3.c:912, munki.c:849). Upstream's other
converting drivers fetch the spectrum whenever a conversion needs it
(dtp20.c:555-556, dtp22.c:597-598, dtp41.c:622-623, ss.c:1287-1289).

The repair drops the spectrum once, after both conversions. XYZ-only reads carry
the converted XYZ and, as before, no spectrum; spectral reads are unchanged.

Each predecessor is an authenticated frozen fixture. The proof compares it with
the actual current production transfer and its current XRGA/custom-filter helpers,
over the same reflectance table. It checks that the predecessor skips where
production converts, and preserves the same finite output controls.
"""

from __future__ import annotations

import difflib
import io
import json
import platform
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.legacy_nonadaptive_calibration import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import _function
from native_argyll.ss_xy_metadata import xrga_conversion

REPAIR_ID = 'recipe:xyz-only-conversion'

DEBUG_DISCARDED = ("/* Proof-only: debug calls are discarded before any argument is evaluated. */\n"
                   "#define a1logd(log, level, fmt, ...) ((void)(fmt))\n")


@dataclass(frozen=True)
class Driver:
    name: str
    path: str
    function: str


DRIVERS = (
    Driver("i1pro", "spectro/i1pro_imp.c", "i1pro_conv2XYZ"),
    Driver("i1pro3", "spectro/i1pro3_imp.c", "i1pro3_conv2XYZ"),
    Driver("munki", "spectro/munki_imp.c", "munki_conv2XYZ"),
)
BY_NAME = {driver.name: driver for driver in DRIVERS}
# What each text must show over 24 reflectances: the i1Pro has four native/target
# pairs (Rev A-D GMDI, Rev E XRGA), the others two; the filter cases run once.
# Counts are (frozen predecessor, current production). Production's custom-filter
# guard adds one unclamped scratch converter per filtered request: two batched
# reads plus 24 public single-patch references. These 26 requests each add a check.
# Every production filter is looked up in preflight, scratch validation and apply.
EXPECTED = {
    "i1pro": {"checks": (1393, 1424), "lookups": (1728, 7776),
              "failed": {"xyz_only_target_converted": 96, "xyz_only_filter_applied": 24}},
    "i1pro3": {"checks": (779, 808), "lookups": (1728, 7776),
               "failed": {"xyz_only_target_converted": 48, "xyz_only_filter_applied": 24}},
    "munki": {"checks": (779, 808), "lookups": (1728, 7776),
              "failed": {"xyz_only_target_converted": 48, "xyz_only_filter_applied": 24}},
}
SUPPORT = ("spectro/xrga.c", "spectro/inst.c", "xicc/xspect.c")


def preamble(text: str) -> str:
    """The driver's own include block, through xrga.h, so the extract compiles as it does."""
    marker = '#include "xrga.h"\n'
    return text[:text.index(marker) + len(marker)]


def fragment(text: str, driver: Driver) -> str:
    """The optical weights and the transfer, with a #line to the driver's own text."""
    start = text.index("/* Optical adjustment weights */")
    function = _function(text, driver.function)
    end = text.index(function, start) + len(function)
    return f'#line {text.count(chr(10), 0, start) + 1} "{driver.path}"\n' + text[start:end]


def samples(xspect: str) -> str:
    """xicc/xspect.c's EBU TLCI ColorChecker table, exactly as pinned."""
    start = xspect.index("/* EBU TLCI ColorChecker samples */\n")
    return xspect[start:xspect.index("\n};\n", start) + len("\n};\n")]


def custom_filter(inst: str) -> str:
    """Extract the supplied filter and the validity helpers its current source declares."""
    helpers = "".join(
        _function(inst, name) for name in ("inst_double_finite", "inst_valid_custom_filter")
        if re.search(r"^(?:static )?int " + name + r"\(", inst, re.M))
    return helpers + _function(inst, "ipatch_convert_custom_filter")


def _validate(results: dict) -> None:
    for name, expected in EXPECTED.items():
        baseline, patched = results[name]["baseline"], results[name]["patched"]
        if patched["failures"] or patched["returncode"]:
            raise AssertionError(f"patched result for {name}: {sorted(patched['failed'])}")
        # A known failing assertion exits 1. A crash after emitting JSON is
        # not an expected failure and must not authenticate the baseline.
        if baseline["returncode"] != 1:
            raise AssertionError(f"baseline process for {name}: exit {baseline['returncode']}")
        if baseline["failed"] != expected["failed"]:
            raise AssertionError(f"baseline result for {name}: failed {baseline['failed']}")
        if ((baseline["checks"], patched["checks"]) != expected["checks"]
                or (baseline["lookups"], patched["lookups"]) != expected["lookups"]):
            raise AssertionError(f"counts for {name}: checks {baseline['checks']}/"
                                 f"{patched['checks']}, lookups {baseline['lookups']}/"
                                 f"{patched['lookups']}")
        if (baseline["unclamped_integrators"], patched["unclamped_integrators"]) != (0, 26):
            raise AssertionError(f"Custom-filter scratch converter coverage differs for {name}")
        reads = baseline["xyz_only_target"]["skipped"]
        if reads != expected["failed"]["xyz_only_target_converted"] or baseline[
                "xyz_only_target"]["converted"] or patched["xyz_only_target"]["converted"] != reads:
            raise AssertionError(f"conversion observations for {name} disagree with the checks")
        filtered = baseline["xyz_only_filter"]["skipped"]
        if filtered != expected["failed"]["xyz_only_filter_applied"] or baseline[
                "xyz_only_filter"]["applied"] or patched["xyz_only_filter"]["applied"] != filtered:
            raise AssertionError(f"filter observations for {name} disagree with the checks")


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    """Compare frozen predecessors and production over the same reflectances.

    Each variant uses its own source transfer, XRGA conversion and custom filter;
    the spectral-to-XYZ integrator and the filter lookup are stand-ins, and the
    raw spectra are upstream's own ColorChecker reflectances. Nothing links a
    transport or runs an instrument.
    """
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    headers_by_path: dict[Path, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        originals = {driver.path: zipped.read(PREFIX + driver.path).decode() for driver in DRIVERS}
        support = {path: zipped.read(PREFIX + path).decode() for path in SUPPORT}
        for entry in zipped.namelist():
            if not entry.startswith(PREFIX):
                continue
            relative = Path(entry.removeprefix(PREFIX))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe upstream path")
            if entry.endswith(".h") or relative.as_posix() == "License2.txt":
                headers_by_path[relative] = zipped.read(entry)
        headers_by_path[Path("sa_config.h")] = zipped.read(PREFIX + "h/aconfig.h")
    texts = {}
    for driver in DRIVERS:
        texts[driver.name] = (before_source("xyz_only_conversion", driver.path, archive),
                              production_text(source, driver.path))
    production_support = {path: production_text(source, path) for path in SUPPORT}
    production_headers = {
        path: production_text(source, "h/aconfig.h" if path == Path("sa_config.h")
                              else path.as_posix()).encode()
        for path in headers_by_path}
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    for relative, content in headers_by_path.items():
        (headers / relative).parent.mkdir(parents=True, exist_ok=True)
        (headers / relative).write_bytes(content)
    current_headers = output / "production-headers"
    for relative, content in production_headers.items():
        target = current_headers / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    harness = Path(__file__).with_name("xyz_only_conversion_probe.c")
    extracts = {
        label: {"conversion": xrga_conversion(inputs["spectro/xrga.c"]),
                "filter": custom_filter(inputs["spectro/inst.c"]),
                "samples": samples(inputs["xicc/xspect.c"])}
        for label, inputs in (("baseline", support), ("patched", production_support))}
    report: dict = dict(
        status="incomplete", archive_sha256=ARCHIVE_SHA256,
        original_sha256={driver.path: digest(originals[driver.path].encode())
                         for driver in DRIVERS},
        baseline_sha256={driver.path: digest(texts[driver.name][0].encode())
                         for driver in DRIVERS},
        patched_sha256={driver.path: digest(texts[driver.name][1].encode())
                        for driver in DRIVERS},
        transfer_sha256={driver.name: {"baseline": digest(
            _function(texts[driver.name][0], driver.function).encode()), "patched": digest(
            _function(texts[driver.name][1], driver.function).encode())} for driver in DRIVERS},
        support_sha256={path: digest(text.encode()) for path, text in support.items()},
        production_support_sha256={path: digest(text.encode())
                                   for path, text in production_support.items()},
        extract_sha256={label: {name: digest(text.encode()) for name, text in parts.items()}
                        for label, parts in extracts.items()},
        patch_sha256={}, proof_source_sha256=digest(Path(__file__).read_bytes()),
        harness_sha256=digest(harness.read_bytes()), platform=platform.platform(),
        hardware_tested=False, instrument_runtime_linked=False,
        scope="actual i1pro_conv2XYZ, i1pro3_conv2XYZ and munki_conv2XYZ with xrga.c's "
              "conversion and inst.c's custom filter, over upstream's ColorChecker "
              "reflectances; stand-in integrator and filter lookup",
        limitations=[
            "The spectral-to-XYZ integrator is a deterministic linear stand-in, so no XYZ "
            "value here is colorimetric. Equal projections alone do not identify a spectrum; "
            "the two XYZ-only comparisons also check every active input band and grid.",
            "The filter lookup returns the nearest band and the filter spectrum is "
            "synthetic; the custom filter's own arithmetic is upstream's.",
            "The requested standard is stored as the option handlers store it "
            "(i1pro.c:830, i1pro3.c:895, munki.c:831); the handlers are not compiled.",
            "Standard-resolution grids only; high-resolution transfers share the code.",
            "A read's spectrum comes from the reflectance table, not an instrument, and "
            "no accuracy of the XRGA equations is claimed.",
        ],
        sanitize=sanitize, commands=[], results={}, products_sha256={},
    )
    try:
        system = platform.system()
        for driver in DRIVERS:
            baseline, patched = texts[driver.name]
            diff = "".join(difflib.unified_diff(
                baseline.splitlines(True), patched.splitlines(True),
                fromfile=f"composed/{driver.path}", tofile=f"xyz-only-converted/{driver.path}"))
            (output / f"{driver.name}.patch").write_text(diff)
            report["patch_sha256"][driver.name] = digest(diff.encode())
            report["results"][driver.name] = {}
            for label, text in (("baseline", baseline), ("patched", patched)):
                directory = output / driver.name / label
                directory.mkdir(parents=True)
                (directory / "extracted.inc").write_text(
                    preamble(text) + DEBUG_DISCARDED + fragment(text, driver))
                for name, content in extracts[label].items():
                    (directory / (name + ".inc")).write_text(content)
                (directory / "probe.c").write_bytes(harness.read_bytes())
                executable = directory / ("probe.exe" if system == "Windows" else "probe")
                variant_headers = headers if label == "baseline" else current_headers
                includes = [variant_headers, directory, *[variant_headers / p for p in
                            ("spectro", "numlib", "rspl", "xicc", "cgats", "icc", "h")]]
                command = [cc, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB", "-DENABLE_USB",
                           f"-DCM_DRIVER_{driver.name.upper()}",
                           f"-DCM_PRODUCTION={int(label == 'patched')}",
                           *(["-DNT", "-DEN_LIBUSB0"] if system == "Windows"
                             else ["-DUNIX", "-pthread"]),
                           *sanitizers.flags(sanitize),
                           *["-I" + str(p) for p in includes], str(directory / "probe.c"),
                           "-lm", "-o", str(executable)]
                run = subprocess.run(command, capture_output=True, text=True, timeout=120)
                report["commands"].append({"command": command, "returncode": run.returncode})
                (directory / "build.log").write_text(run.stdout + run.stderr)
                if run.returncode:
                    raise RuntimeError(f"{driver.name} {label} probe failed to compile; see log")
                run = subprocess.run([str(executable)], capture_output=True, text=True,
                                     timeout=60)
                (directory / "main.log").write_text(run.stderr)
                sanitizers.refuse(run.stderr, f"{driver.name}/{label}")
                report["results"][driver.name][label] = dict(json.loads(run.stdout),
                                                             returncode=run.returncode)
                report["products_sha256"].update({
                    str(p.relative_to(output)): digest(p.read_bytes())
                    for p in sorted(directory.iterdir()) if p.is_file()})
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    return report
