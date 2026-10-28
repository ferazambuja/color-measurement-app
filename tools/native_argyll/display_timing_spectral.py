"""Source-bound finite-value proofs for three Argyll spectral delay methods.

The probe compares authenticated frozen predecessors with actual production
method bodies using inert transport. It does not generate or reverse source.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.proof_inputs import fixture_manifest, production_text
from native_argyll.spyder_calibration_slots import _function, digest

REPAIR_ID = 'recipe:display-timing-spectral'

SOURCES = {
    "spectro/i1pro_imp.c": ("i1pro_imp_meas_delay", "I1PRO"),
    "spectro/i1pro3_imp.c": ("i1pro3_imp_meas_delay", "I1PRO3"),
    "spectro/munki_imp.c": ("munki_imp_meas_delay", "MUNKI"),
}


def _require_context(text: str, path: str) -> None:
    """The extracted probe's duration constant must match the actual driver."""
    values = re.findall(r"^#define NDMXTIME\s+(\S+)", text, flags=re.MULTILINE)
    if values != ["2.0"]:
        raise ValueError(f"Unreviewed spectral delay duration: {path}")


def prove(texts: dict[str, str], output: Path, compiler: str = "cc", *,
          source: Path, sanitize: bool = False) -> dict:
    """Execute authenticated methods with synthetic data, never device code.

    Only repaired variants are sanitized. The baseline deliberately leaks on two
    error paths; its nonfinite and out-of-range clock casts are not executed at
    all because converting these values to int is undefined in C.
    """
    predecessors = fixture_manifest()["proofs"]["display_timing_spectral"]
    if set(texts) != set(SOURCES) or set(predecessors) != set(SOURCES):
        raise ValueError("Spectral timing predecessor paths differ from the declared proof")
    for path, text in texts.items():
        if digest(text.encode()) != predecessors[path]["sha256"]:
            raise ValueError(f"Spectral timing predecessor differs from its digest: {path}")
    production = {path: production_text(source, path) for path in SOURCES}
    for path in SOURCES:
        _require_context(texts[path], path)
        _require_context(production[path], path)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    results: dict[str, dict[str, dict[str, list[int]]]] = {}
    report = {"status": "incomplete", "hardware_tested": False,
              "instrument_runtime_linked": False, "sanitize_repaired": sanitize,
              "method_sha256": {
                  path: digest(_function(production[path], name).encode())
                  for path, (name, _) in SOURCES.items()},
              "source_sha256": {path: digest(text.encode()) for path, text in texts.items()},
              "compiled_source_sha256": {
                  path: digest(text.encode()) for path, text in production.items()},
              "baseline_excluded": {
                  "nan_clock": "Nonfinite float-to-int conversion is undefined",
                  "large_stamp": "Out-of-range float-to-int conversion is undefined",
                  "reaction_only": "Out-of-range float-to-int conversion is undefined",
                  "inttime_zero": "Division by zero before integer conversion is undefined",
                  "inttime_nan": "Nonfinite float-to-int conversion is undefined",
                  "inttime_large": "One-sample baseline was not qualified",
                  "readback_zero": "The original never checks the read-back interval",
                  "readback_inf": "The original never checks the read-back interval"},
              "results": results}
    receipt = output / "report.json"
    receipt.write_text(json.dumps(report, indent=2) + "\n")
    cases = ("control", "overflow", "missing_stamp", "flat", "read_error",
             "nan_clock", "alloc_fail", "large_stamp", "reaction_only",
             "inttime_zero", "inttime_nan", "inttime_large", "readback_zero", "readback_inf")
    for path, (name, _) in SOURCES.items():
        predecessor = texts[path]
        driver = name.split("_imp")[0]
        results[driver] = {}
        for variant, contents in (("baseline", predecessor), ("repaired", production[path])):
            folder = output / driver / variant
            folder.mkdir(parents=True)
            (folder / "method.inc").write_text(_function(contents, name))
            command = [compiler, "-std=gnu89", "-O1", "-g", f"-I{folder}",
                       *sanitizers.flags(sanitize and variant == "repaired"),
                       *(["-DTIMING_PROOF_REPAIRED"] if variant == "repaired" else []),
                       '-DMETHOD_INCLUDE="method.inc"', f"-DTEST_{driver.upper()}",
                       str(Path(__file__).with_name("display_timing_spectral_probe.c")),
                       "-lm", "-o", str(folder / "probe")]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            (folder / "compile.log").write_text(built.stdout + built.stderr)
            built.check_returncode()
            results[driver][variant] = {}
            for case in cases:
                if variant == "baseline" and case in report["baseline_excluded"]:
                    continue
                run = subprocess.run([str(folder / "probe"), case], capture_output=True,
                                     text=True, timeout=10)
                (folder / f"{case}.log").write_text(run.stdout + run.stderr)
                sanitizers.refuse(run.stderr, f"{driver}/{variant}/{case}")
                if run.returncode:
                    raise AssertionError(f"{driver}/{variant}/{case} exited {run.returncode}")
                results[driver][variant][case] = [int(x) for x in run.stdout.split()]
        old, new = results[driver]["baseline"], results[driver]["repaired"]
        # Fields: driver code, delay, reaction, matrices made/freed, samples
        # made/freed, and whether the driver wrapper reports a failure.
        notrans, internal = 0x41, 0x71
        malloc = 0x62 if driver == "munki" else 0x63
        if not (old["control"] == new["control"] == [0, 1010, 0, 1, 1, 1, 1, 0]
                and old["overflow"][0] == 0
                and new["overflow"] == [notrans, -88, 0, 1, 1, 1, 1, 1]
                # Pristine returns an inst_code here, which the wrapper masks
                # to inst_ok, and leaks both buffers.
                and old["missing_stamp"][0] == 0x060000
                and old["missing_stamp"][4::2] + old["missing_stamp"][7:] == [0, 0, 0]
                and new["missing_stamp"] == [internal, -88, 0, 1, 1, 1, 1, 1]
                and old["alloc_fail"][4] == 0
                and new["alloc_fail"] == [malloc, -88, 0, 1, 1, 0, 0, 1]
                and new["read_error"] == old["read_error"] == [9, -88, 0, 1, 1, 1, 1, 1]
                and new["flat"] == old["flat"] == [notrans, -88, 0, 1, 1, 1, 1, 1]
                and new["nan_clock"] == [internal, -88, 0, 0, 0, 0, 0, 1]
                and new["large_stamp"] == [internal, -88, 0, 1, 1, 1, 1, 1]
                and new["reaction_only"] == [internal, -88, 0, 1, 1, 1, 1, 1]
                and all(new[case] == [internal, -88, 0, 0, 0, 0, 0, 1]
                        for case in ("inttime_zero", "inttime_nan", "inttime_large"))
                and all(new[case] == [internal, -88, 0, 1, 1, 1, 1, 1]
                        for case in ("readback_zero", "readback_inf"))):
            raise AssertionError(f"Spectral timing result drift: {driver}: {results[driver]}")
    report["status"] = "passed"
    receipt.write_text(json.dumps(report, indent=2) + "\n")
    return report
