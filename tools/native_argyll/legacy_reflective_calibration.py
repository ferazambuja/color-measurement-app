"""Execute pinned reflective-scan calibration control flow with inert IO hooks.

Extracted functions retain ArgyllCMS/Graeme W. Gill's GPL-2.0-or-later terms.
The probe checks warning boundaries and masks, not physical calibration.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
from pathlib import Path

from native_argyll.legacy_nonadaptive_calibration import (
    ARCHIVE_SHA256,
    SOURCE_PATH,
    _function,
    digest,
)
from native_argyll.proof_inputs import production_text


def prove(archive: Path, source: Path, output: Path, *, compiler: str) -> dict:
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Expected the pinned Argyll 3.5.0 archive")
    text = production_text(source, SOURCE_PATH)
    output.mkdir(parents=True, exist_ok=False)
    (output / "sa_config.h").write_bytes((source / "h/aconfig.h").read_bytes())
    fragments = {}
    for name, signature in (
        ("calibrate.inc", "i1pro_code i1pro_imp_calibrate(\n"),
        ("status.inc", "i1pro_code i1pro_imp_get_n_a_cals("),
        ("measurement_count.inc", "int i1pro_comp_nummeas(\n"),
    ):
        fragment = _function(text, signature)
        (output / name).write_text(fragment)
        fragments[name] = digest(fragment.encode())
    definitions = []
    for name in ("DISP_INTT", "DISP_INTT2", "DISP_INTT3", "DISP_INTT4",
                 "WLCALTOUT", "DCALTOUT", "DCALTOUT2", "WCALTOUT"):
        matches = re.findall(r"^#define " + name + r"\s+[^\n]+", text, re.M)
        if len(matches) != 1:
            raise ValueError(f"Unexpected native definition: {name}")
        definitions.append(matches[0])
    (output / "definitions.inc").write_text("\n".join(definitions) + "\n")
    harness = Path(__file__).with_name("legacy_reflective_calibration_probe.c")
    report = {
        "status": "incomplete", "hardware_tested": False,
        "instrument_runtime_linked": False, "cache_file_io": False,
        "archive_sha256": ARCHIVE_SHA256, "compiled_source_sha256": digest(text.encode()),
        "fragment_sha256": fragments, "harness_sha256": digest(harness.read_bytes()),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "scope": "reflective_scan_calibration_control_flow_with_inert_IO",
        "limitations": [
            "Synthetic initialized state; no transport, optical or cache-restoration proof.",
            "White plausibility and factor computation are substituted in this control-flow proof.",
            "Upstream warning text does not establish transmission geometry for reflection.",
        ],
        "commands": [], "results": [],
    }

    def run(argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        log = f"command-{len(report['commands']):02d}.log"
        (output / log).write_text(result.stdout + result.stderr)
        report["commands"].append(dict(argv=argv, returncode=result.returncode, log=log))
        if result.returncode:
            raise RuntimeError(f"Reflective calibration proof failed: {output / log}")
        return result.stdout

    windows = platform.system() == "Windows"
    flags = ["-std=gnu99", "-O1", "-DSALONEINSTLIB", "-DENABLE_USB",
             *(["-DNT", "-DEN_LIBUSB0"] if windows else ["-DUNIX"]),
             f"-I{output}", f"-I{source}",
             *[f"-I{source / p}" for p in ("h", "numlib", "rspl", "cgats", "xicc", "spectro")]]
    executable = output / ("probe.exe" if windows else "probe")
    try:
        run([compiler, *flags, str(harness), "-lm", "-o", str(executable)])
        for model in ("i1pro", "i1pro2"):
            for case in ("ordinary", "saturation", "below6", "at6", "above6"):
                report["results"].append(json.loads(run([str(executable), model, case])))
        report["case_count"] = len(report["results"])
        report["status"] = "passed"
    finally:
        report["products_sha256"] = {
            p.name: digest(p.read_bytes()) for p in sorted(output.iterdir())
            if p.is_file() and p.name != "report.json"
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
