"""Keep ColorMunki shared dark banks paired with their measured exposures.

The shared-dark optimization is safe only before either nonadaptive display
mode permutes its three banks. Selected siblings retain their own calibration;
normal get_n_a_cals requests renew an expired dark. The exact exposure/gain
matching compares the trigger's exact hardware counts, not a time tolerance.
"""
from __future__ import annotations

import io
import json
import platform
import re
import shutil
import subprocess
import zipfile
from collections import Counter
from pathlib import Path

from native_argyll import munki_ledtemp_slope as previous
from native_argyll import sanitizers
from native_argyll.munki_nonadaptive_calibration import extracted, mode_state
from native_argyll.proof_inputs import before_source, production_proof, production_text

REPAIR_ID = 'recipe:munki-dark-copy'

SOURCE_PATH = "spectro/munki_imp.c"


# The bank matrix fails at these exact scenario/check pairs before the repair.
BASELINE_FAILURES = Counter({
    ("source0-target0", "canonical third exposure preserved"): 2,
    ("source0-target1", "copy only between canonical bank orders"): 1,
    ("source0-target1", "current bank recorded at the current exposure"): 2,
    ("source0-target1", "canonical third exposure preserved"): 2,
    ("source0-target1", "unshared destination still needs its own expired dark"): 1,
    ("source0-target1", "read exposure matches measured dark"): 1,
    ("source0-target2", "copy only between canonical bank orders"): 1,
    ("source0-target2", "current bank recorded at the current exposure"): 2,
    ("source0-target2", "unshared destination still needs its own expired dark"): 1,
    ("source0-target2", "read exposure matches measured dark"): 1,
    ("source1-target0", "copy only between canonical bank orders"): 1,
    ("source1-target0", "current bank recorded at the current exposure"): 2,
    ("source1-target0", "bank 3 recorded at dark_int_time3"): 2,
    ("source1-target0", "canonical third exposure preserved"): 2,
    ("source1-target0", "unshared destination still needs its own expired dark"): 1,
    ("source1-target0", "read exposure matches measured dark"): 1,
    ("source1-target1", "copy only between canonical bank orders"): 1,
    ("source1-target1", "bank 3 recorded at dark_int_time3"): 2,
    ("source1-target1", "canonical third exposure preserved"): 2,
    ("source1-target1", "unshared destination still needs its own expired dark"): 1,
    ("source1-target2", "copy only between canonical bank orders"): 1,
    ("source1-target2", "current bank recorded at the current exposure"): 2,
    ("source1-target2", "bank 3 recorded at dark_int_time3"): 2,
    ("source1-target2", "unshared destination still needs its own expired dark"): 1,
    ("source1-target2", "read exposure matches measured dark"): 1,
    ("cold-sharing", "cold sibling gets a valid dark"): 1,
    ("cold-sharing", "third dark keeps its own exposure"): 2,
    ("cold-sharing", "shared cold calibration admits the read"): 1,
    ("cold-sharing", "cold read matches its bank"): 1,
})


def _validate_result(label: str, row: dict, stderr: str, returncode: int) -> None:
    checks = re.findall(
        r"^\s*\[([^]]+)\]\s+(.+?)\s+expected\s+\S+\s+actual\s+\S+\s+(PASS|FAIL)$",
        stderr, re.M,
    )
    failed = Counter((case, check) for case, check, result in checks if result == "FAIL")
    expected = BASELINE_FAILURES if label == "baseline" else Counter()
    count = 354 if label == "baseline" else 375
    if label == "patched" and failed:
        raise AssertionError(f"Repaired shared-dark transitions failed: {dict(failed)}")
    if (row["checks"] != count or len(checks) != count
            or row["failures"] != sum(failed.values()) or row["tripwires"]
            or returncode != int(bool(expected))):
        raise AssertionError(f"{label}: unexpected probe accounting, exit or acquisition")
    if failed != expected:
        message = ("Baseline failures differ from crossed dark-bank expectations"
                   if label == "baseline" else "Repaired shared-dark transitions failed")
        raise AssertionError(f"{message}: {dict(failed)}")


@production_proof
def prove(archive: Path, output: Path, *, compiler: str = "cc", sanitize: bool = False,
          source: Path | None = None) -> dict:
    data = archive.read_bytes()
    if previous.digest(data) != previous.ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 mismatch")
    output.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        numsup = zipped.read(previous.PREFIX + "numlib/numsup.c").decode()
        headers = output / "upstream"
        for name in zipped.namelist():
            if name.startswith(previous.PREFIX) and name.endswith(".h"):
                path = headers / name.removeprefix(previous.PREFIX)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(zipped.read(name))
        (headers / "sa_config.h").write_bytes(zipped.read(previous.PREFIX + "h/aconfig.h"))
    baseline = before_source("munki_dark_copy", SOURCE_PATH, archive)
    patched = production_text(source, SOURCE_PATH)
    production_numsup = production_text(source, "numlib/numsup.c")
    base_harness = Path(__file__).with_name("munki_nonadaptive_probe.c").read_text()
    if base_harness.count("int main(") != 1:
        raise ValueError("Missing nonadaptive probe entry point")
    harness = base_harness.replace("int main(", "int retained_main(")
    harness += "\n" + Path(__file__).with_name("munki_dark_copy_probe.c").read_text()
    report = dict(status="incomplete", archive_sha256=previous.digest(data),
                  baseline_sha256=previous.digest(baseline.encode()),
                  patched_sha256=previous.digest(patched.encode()),
                  hardware_tested=False, cache_file_io=False,
                  scope="Twelve sharing scenarios through extracted calibration, "
                        "with inert acquisition, actual needed-calibration checks and bank pairing",
                  sanitize=sanitize, commands=[], results={})
    try:
        for label, text in (("baseline", baseline), ("patched", patched)):
            directory = output / label
            directory.mkdir()
            (directory / "probe.c").write_text(harness)
            support = numsup if label == "baseline" else production_numsup
            (directory / "extracted.inc").write_text(extracted(text, support))
            (directory / "mode_state.inc").write_text(mode_state(text))
            includes = [headers, directory, *[headers / p for p in
                        ("spectro", "numlib", "rspl", "xicc", "cgats", "icc", "h")]]
            executable = directory / ("probe.exe" if platform.system() == "Windows" else "probe")
            flags = (["-DNT", "-DEN_LIBUSB0"] if platform.system() == "Windows"
                     else ["-DUNIX", "-pthread"])
            command = [shutil.which(compiler) or compiler, "-std=gnu99", "-O1", "-g",
                       "-DSALONEINSTLIB", "-DENABLE_USB", *flags, *sanitizers.flags(sanitize),
                       *["-I" + str(p) for p in includes], str(directory / "probe.c"),
                       "-lm", "-o", str(executable)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (directory / "compile.log").write_text(run.stdout + run.stderr)
            report["commands"].append(command)
            run.check_returncode()
            run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            (directory / "run.log").write_text(run.stdout + run.stderr)
            sanitizers.refuse(run.stderr, label)
            row = json.loads(run.stdout)
            _validate_result(label, row, run.stderr, run.returncode)
            report["results"][label] = row
        report["status"] = "passed"
        return report
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
