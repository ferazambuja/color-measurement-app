"""Preserve SpectroScan communication errors and unsigned wire words.

The complete pinned ss_imp.c is linked only to scripted serial replies and the
composed numeric converters (numsup.c with its IEEE 754 special-value repair,
ieee754_special.py). No transport or instrument is opened.
"""

from __future__ import annotations

import io
import json
import math
import platform
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.munki_ledtemp_slope import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import _function

REPAIR_ID = 'recipe:ss-protocol'

SOURCE_HASHES = {
    "spectro/ss_imp.c": "22533a5cadbc2bce29aed1aacef671548f8d1dfb8e9d53a12f8acd0a70734fdb",
    "spectro/ss_imp.h": "9860268d0a59c798e758a0d43b22f8c4d3d19a73727d88140804de84dd4c258d",
}


COMM_ERRORS = {
    "01": 0x90000,
    "02": 0x90000,
    "03": 0x90000,
    "04": 0x90000,
    "06": 0x90000,
    "07": 0x90000,
    "08": 0x150000,
    "10": 0x190000,
}
MOVE = tuple(":D1A0" + c for c in COMM_ERRORS) + (
    ":D18000",
    ":D18001",
    ":D1A0",
    ":D1A0ZZ",
)
COMMAND = (":2603", ":D1A008", ":0000")
DOUBLES = (
    "0000803F",
    "000080BF",
    "0000C842",
    "0000C8C2",
    "0000807F",
    "000080FF",
    "FFFF7F7F",
    "FFFF7FFF",
    "01000080",
    "00000000",
    "00000080",
    "0000C07F",
)
INTS = ("FFFFFF7F", "00000080", "FFFFFFFF", "78563412")
CASES = tuple(
    (kind, arg)
    for kind, args in (("moveabs", MOVE), ("command", COMMAND), ("double", DOUBLES), ("int", INTS))
    for arg in args
)


def validate(rows: list[dict], *, repaired: bool, sanitize: bool) -> None:
    """Validate every command outcome and decoded value, including control cases."""
    if [(r["kind"], r["arg"]) for r in rows] != list(CASES):
        raise AssertionError("SpectroScan protocol case census changed")
    for row in rows:
        kind, arg = row["kind"], row["arg"]
        name = f"{kind} {arg}"
        negative_word = kind in ("double", "int") and int(arg[6], 16) >= 8
        if not repaired and sanitize and negative_word:
            if not (row["returncode"] and row["diagnostic"] and "left shift" in row["diagnostic"]):
                raise AssertionError(f"{name}: baseline signed shift no longer reproduced")
            continue
        if row["returncode"] or row["diagnostic"]:
            raise AssertionError(f"{name}: process or sanitizer failure")
        if kind == "moveabs":
            if arg.startswith(":D1A0"):
                if not repaired:
                    error, group = 0xF7, 0x90000
                elif arg == ":D1A0":
                    error, group = 0xF6, 0x90000
                elif arg == ":D1A0ZZ":
                    error, group = 0xF8, 0x90000
                else:
                    error, group = 0x80 + int(arg[5:], 16), COMM_ERRORS[arg[5:]]
            elif arg == ":D18000":
                error, group = 0, 0
            else:
                error, group = 0x61, 0x150000
            want = dict(
                snerr=hex(error),
                inst_class=hex(group),
                inst_code=hex(group | error),
                writes=1,
                sent=";D0000064006400",
            )
        elif kind == "command":
            error = 0x83 if arg == ":2603" else 0x88 if repaired and arg == ":D1A008" else 0
            want = dict(snerr=hex(error), remaining="" if error else arg[1:])
        elif kind == "int":
            want = dict(
                snerr="0x0",
                bits="0x" + bytes.fromhex(arg)[::-1].hex(),
                value=int.from_bytes(bytes.fromhex(arg), "little"),
            )
        else:
            value = struct.unpack("<f", bytes.fromhex(arg))[0]
            if not math.isfinite(value):
                # The composed helper (ieee754_special.py) decodes these words as
                # the specials they encode; the probe prints them as strings.
                value = "nan" if math.isnan(value) else ("-inf" if value < 0 else "inf")
            want = dict(snerr="0x0", consumed=8, value=value, negative=int(negative_word))
            if value == "nan":
                # The shared converter preserves NaN classification, not its
                # payload or sign. Signed zero and infinities remain checked.
                want.pop("negative")
        for field, value in want.items():
            if row.get(field) != value:
                raise AssertionError(f"{name}: {field} {row.get(field)!r} != {value!r}")


@production_proof
def prove(archive: Path, output: Path, *, compiler: str = "cc", sanitize: bool = False,
          source: Path | None = None) -> dict:
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 mismatch")
    output.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        originals = {p: before_source("ss_protocol", p, archive) for p in SOURCE_HASHES}
        # Exercise the numeric converters compiled in the current production tree.
        numsup = production_text(source, "numlib/numsup.c")
        headers = output / "upstream"
        for name in zipped.namelist():
            if name.startswith(PREFIX) and name.endswith(".h"):
                path = headers / name.removeprefix(PREFIX)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(zipped.read(name))
        (headers / "sa_config.h").write_bytes(zipped.read(PREFIX + "h/aconfig.h"))
    patched = {p: production_text(source, p) for p in SOURCE_HASHES}
    report = dict(
        status="incomplete",
        archive_sha256=digest(data),
        original_sha256={p: digest(t.encode()) for p, t in originals.items()},
        patched_sha256={p: digest(t.encode()) for p, t in patched.items()},
        hardware_tested=False,
        transport=False,
        sanitize=sanitize,
        scope="Complete ss_imp.c with scripted serial replies; composed numeric converters",
        commands=[],
        results={},
    )
    try:
        for label, texts in (("baseline", originals), ("patched", patched)):
            directory = output / label
            directory.mkdir()
            for path, text in texts.items():
                (directory / Path(path).name).write_text(text)
            # ss.h includes ss_imp.h relative to itself. Keep that inclusion
            # beside the variant's declaration rather than the pristine header.
            (directory / "ss.h").write_bytes((headers / "spectro/ss.h").read_bytes())
            (directory / "probe.c").write_bytes(
                Path(__file__).with_name("ss_protocol_probe.c").read_bytes()
            )
            (directory / "numsup_extract.inc").write_text(
                "\n".join(
                    _function(numsup, name) for name in (
                        "IEEE754_special_value", "IEEE754todouble", "doubletoIEEE754")
                )
            )
            includes = [
                directory,
                headers,
                *[headers / p for p in ("spectro", "numlib", "rspl", "xicc", "cgats", "icc", "h")],
            ]
            executable = directory / ("probe.exe" if platform.system() == "Windows" else "probe")
            flags = (
                ["-DNT", "-DEN_LIBUSB0"]
                if platform.system() == "Windows"
                else ["-DUNIX", "-pthread"]
            )
            command = [
                shutil.which(compiler) or compiler,
                "-std=gnu99",
                "-O1",
                "-g",
                "-DSALONEINSTLIB",
                "-DENABLE_USB",
                *flags,
                *sanitizers.flags(sanitize),
                *["-I" + str(p) for p in includes],
                str(directory / "ss_imp.c"),
                str(directory / "probe.c"),
                "-lm",
                "-o",
                str(executable),
            ]
            run = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (directory / "compile.log").write_text(run.stdout + run.stderr)
            report["commands"].append(command)
            run.check_returncode()
            rows = []
            with (directory / "run.log").open("w") as log:
                for kind, arg in CASES:
                    run = subprocess.run(
                        [str(executable), kind, arg], capture_output=True, text=True, timeout=30
                    )
                    log.write(f"{kind} {arg}: exit {run.returncode}\n{run.stdout}{run.stderr}\n")
                    # The pristine high-bit decoder intentionally demonstrates
                    # its signed shift. Every other diagnostic is a proof error;
                    # validate() checks the precise expected baseline failure.
                    expected_shift = (
                        label == "baseline" and sanitize
                        and kind in ("double", "int") and int(arg[6], 16) >= 8
                    )
                    if not expected_shift:
                        sanitizers.refuse(run.stderr, f"{label}/{kind}/{arg}")
                    row = dict(
                        kind=kind,
                        arg=arg,
                        returncode=run.returncode,
                        diagnostic=sanitizers.diagnostic(run.stderr),
                    )
                    if run.stdout.strip():
                        row.update(json.loads(run.stdout))
                    rows.append(row)
            report["results"][label] = rows
            validate(rows, repaired=label == "patched", sanitize=sanitize)
        report["status"] = "passed"
        return report
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
