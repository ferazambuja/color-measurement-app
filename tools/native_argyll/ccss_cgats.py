"""Installed .ccss files: bound the band count and read each column as its type.

Display-type listing reads every installed calibration spectral sample set.
inst_creat_disptype_list asks list_iccss for them (inst.c:956), which reads each
file found under the ArgyllCMS data directories (inst.c:1341) through
read_ccss_cgats. That reader trusts the file in four ways:

- SPECTRAL_BANDS comes from atoi unchecked (:350). The field loop writes one
  index per band into ``int spi[XSPECT_MAX_BANDS]`` (:264, :379) and the sample
  loop one value per band into the 601-element ``xspect.spec`` (:405,
  xspect.h:81,:88). More than 601 bands writes past both; a count of zero or
  less returns a set whose spectra have no bands.
- One band divides by ``spec_n - 1`` (ccss.c:374), so the nearest-nm conversion casts
  NaN to int. The consumer also divides by n-1 (xspect.c:4347); fixing only the
  field-name computation would not make a single-band calibration usable. A start
  or end wavelength outside int range makes that cast undefined for any band count.
- SPECTRAL_NORM is taken as written (:365). Every consumer divides by it
  (xspect.c getval_xspec), so zero, a negative or a nonfinite norm turns the
  correction into infinities or sign-flipped values.
- Each value is read through ``double *`` (:405) whatever the column's type.
  CGATS stores an all-integer column as 4-byte ints (cgats.c:731-739), so the
  reader returns the integer's bit pattern as a denormal and reads past the
  allocation; a string column is read the same way.

This application's own parser (colorimeter_correction._parse_ccss, with
cgats.Table.numbers) already validates explicit corrections. This
repair rejects numeric prefixes and fractional band counts, then applies these
rules to installed files: 2 to 601 bands and a positive ordered wavelength grid
within int range, a positive finite norm, and numeric columns read as the type CGATS
stored. It also requires every sample divided by the norm to remain finite, which
refuses a nonfinite sample as well as an overflowing quotient.
A file outside
them is refused with a message, as the reader's other refusals are, and
list_iccss already skips a file that fails to read (inst.c:1341-1343). Accepted
valid files with real-typed columns read exactly as before. Positive wavelengths
are the existing application policy, not an independently demonstrated upstream
minimum; finite negative sample values remain permitted.

The proof compiles the pristine and repaired readers with the reader's own
CGATS, xspect and numsup sources and reads crafted files through
ccss.read_ccss. Given the composed tree, the repaired reader is built from that
tree's units, as the workers compile them, and also reads the cases only the
final stage's parser refuses. The installed-file search is replaced by a probe
stand-in that lists nothing. Cases whose pristine reading is memory-unsafe or undefined are
run only on the repaired reader here; tests/test_ccss_cgats.py demonstrates the
pristine faults under the sanitizers.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.proof_inputs import before_source, production_proof, production_text

REPAIR_ID = 'recipe:ccss-cgats'

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
PREFIX = "Argyll_V3.5.0/"
PATH = "xicc/ccss.c"


# Each case: a reader input and the result both readers must give. Values are
# the first sample's bands. A "refused" expectation needs the stated message.
HEADER = ('CCSS\n\nDESCRIPTOR "probe"\nDISPLAY "probe display"\nTECHNOLOGY "LCD"\n'
          'SPECTRAL_BANDS "{bands}"\nSPECTRAL_START_NM "{start}"\nSPECTRAL_END_NM "{end}"\n'
          '{norm}\nNUMBER_OF_FIELDS {count}\nBEGIN_DATA_FORMAT\n{names}\nEND_DATA_FORMAT\n\n'
          'NUMBER_OF_SETS 3\nBEGIN_DATA\n{rows}\nEND_DATA\n')
REAL_ROWS = ("0.25 0.5 0.75 1.25", "1.5 1.75 2.25 2.5", "2.75 3.25 3.5 3.75")


def _ccss(bands="4", start="400.0", end="700.0", norm='SPECTRAL_NORM "1.0"\n',
          names="SPEC_400 SPEC_500 SPEC_600 SPEC_700", rows=REAL_ROWS) -> str:
    return HEADER.format(bands=bands, start=start, end=end, norm=norm,
                         count=len(names.split()), names=names, rows="\n".join(rows))


CASES = {
    # Pristine and repaired agree on these.
    "real_columns": (_ccss(), {"bands": 4, "first": [0.25, 0.5, 0.75, 1.25]}, True),
    "absent_norm": (_ccss(norm=""), {"bands": 4, "norm": 1.0}, True),
    # The pristine reader accepts these; the repaired reader refuses them.
    "zero_bands": (_ccss(bands="0"), "SPECTRAL_BANDS 0 is outside 2 to 601", True),
    "negative_bands": (_ccss(bands="-3"), "SPECTRAL_BANDS -3 is outside 2 to 601", True),
    "zero_norm": (_ccss(norm='SPECTRAL_NORM "0"\n'), "SPECTRAL_NORM is not a positive", True),
    "negative_norm": (_ccss(norm='SPECTRAL_NORM "-2"\n'), "SPECTRAL_NORM is not a positive",
                      True),
    "nan_norm": (_ccss(norm='SPECTRAL_NORM "nan"\n'), "SPECTRAL_NORM is not a positive", True),
    # Pristine reading is memory-unsafe or undefined: repaired reader only.
    "over_max_bands": (_ccss(bands="602", start="400.0", end="400.0", names="SPEC_400",
                             rows=("1.0", "2.0", "3.0")),
                       "SPECTRAL_BANDS 602 is outside 2 to 601", False),
    "max_bands": (_ccss(bands="601", start="400.0", end="1000.0",
                        names=" ".join(f"SPEC_{nm}" for nm in range(400, 1001)),
                        rows=tuple(" ".join([value] * 601) for value in ("1.0", "2.0", "3.0"))),
                  {"bands": 601, "first": [1.0] * 601},
                  False),
    "single_band": (_ccss(bands="1", start="550.0", end="550.0", names="SPEC_550",
                          rows=("1.0", "2.0", "3.0")),
                    "SPECTRAL_BANDS 1 is outside 2 to 601", False),
    "nan_start": (_ccss(start="nan"), "spectral wavelength range is not usable", False),
    "huge_end": (_ccss(end="1e300"), "spectral wavelength range is not usable", False),
    "integer_columns": (_ccss(rows=("7 8 9 10", "11 12 13 14", "15 16 17 18")),
                        {"bands": 4, "first": [7.0, 8.0, 9.0, 10.0]}, False),
    "string_column": (_ccss(rows=('0.25 0.5 0.75 "x"', '1.5 1.75 2.25 "y"',
                                  '2.75 3.25 3.5 "z"')),
                      "field SPEC_700 is not numeric", False),
    "numeric_keyword_forms": (_ccss(bands="4.0", start="4e2", end=" 700.0 ",
        norm='SPECTRAL_NORM " +1.0 "\n'), {"bands": 4, "norm": 1.0}, True),
    "wrapped_band_count": (_ccss(bands="4294967297"), "SPECTRAL_BANDS", False),
    "fractional_band_count": (_ccss(bands="4.5"), "SPECTRAL_BANDS", False),
    "band_count_suffix": (_ccss(bands="1garbage"), "SPECTRAL_BANDS is not a number", False),
    "start_keyword_suffix": (_ccss(start="400garbage"), "start wavelength is not a number", True),
    "end_keyword_suffix": (_ccss(end="700garbage"), "end wavelength is not a number", True),
    "norm_keyword_suffix": (_ccss(norm='SPECTRAL_NORM "1garbage"\n'),
        "SPECTRAL_NORM is not a number", True),
    "descending_grid": (_ccss(start="700", end="400",
        names="SPEC_700 SPEC_600 SPEC_500 SPEC_400"), "spectral grid", True),
    "flat_grid": (_ccss(start="400", end="400", names="SPEC_400",
        rows=("1.0", "2.0", "3.0")), "spectral grid", True),
    "zero_grid": (_ccss(start="0", end="300", names="SPEC_000 SPEC_100 SPEC_200 SPEC_300"),
        "spectral grid", True),
    "nonfinite_sample": (_ccss(rows=("1e309 0.5 0.75 1.25", *REAL_ROWS[1:])),
        "normalized spectral sample 0 band 0 is not finite", True),
    "tiny_norm_overflow": (_ccss(norm='SPECTRAL_NORM "1e-320"\n'),
        "normalized spectral sample 0 band 0 is not finite", True),
    "tiny_norm_safe": (_ccss(norm='SPECTRAL_NORM "1e-320"\n',
        rows=("1e-320 1e-320 1e-320 1e-320",) * 3),
        {"bands": 4, "norm": 1e-320, "first": [1e-320] * 4}, True),
    # The composed cubic helper falls back to linear interpolation below four
    # samples in the imported xicc/xspect.c, so coarse spacing is read.
    "coarse_two_bands": (_ccss(bands="2", start="400", end="700",
        names="SPEC_400 SPEC_700", rows=("0.5 1.0", "1.5 2.0", "2.5 3.0")),
        {"bands": 2, "first": [0.5, 1.0]}, True),
    "coarse_three_bands": (_ccss(bands="3", start="400", end="700",
        names="SPEC_400 SPEC_550 SPEC_700", rows=("0.5 1.0 1.5", "1.5 2.0 2.5", "2.5 3.0 3.5")),
        {"bands": 3, "first": [0.5, 1.0, 1.5]}, True),
    "fine_two_bands": (_ccss(bands="2", start="400", end="404",
        names="SPEC_400 SPEC_404", rows=("0.5 1.0", "1.5 2.0", "2.5 3.0")),
        {"bands": 2, "first": [0.5, 1.0]}, True),
    "negative_finite_sample": (_ccss(rows=("-0.25 0.5 0.75 1.25", *REAL_ROWS[1:])),
        {"bands": 4, "first": [-0.25, 0.5, 0.75, 1.25]}, True),
}
# Refused by the current imported cgats/cgats.c parser, not
# by ccss.c: pristine cgats.c stores this all-integer column's first value as
# -2147483648, which the reader would accept as a finite sample.
# The composed parser also refuses a real field that overflows (package round
# 11), so this file stops there instead of at the reader's finite-sample check.
COMPOSED_REFUSALS = {"nonfinite_sample": "Field 0 value in set 0 is out of range"}
COMPOSED_CASES = {
    "integer_out_of_range": (_ccss(rows=("2147483648 8 9 10", "11 12 13 14",
                                         "15 16 17 18")),
                             "Field 0 value in set 0 is out of range"),
}
# Sources the reader needs under SALONEINSTLIB; xdg_bds is the probe's stand-in.
SUPPORT = ("numlib/numsup.c", "spectro/sa_conv.c", "cgats/cgats.c", "cgats/pars.c",
           "xicc/xspect.c", "spectro/disptechs.c")
INCLUDES = ("spectro", "numlib", "xicc", "cgats", "h", "rspl")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _system_flags() -> list[str]:
    # numsup.c asks CoreFoundation for the macOS version (numsup.c initMacOSVersion).
    if platform.system() == "Darwin":
        return ["-framework", "CoreFoundation", "-lm", "-pthread"]
    if platform.system() == "Windows":
        return ["-lm"]
    return ["-lm", "-pthread"]


def _check(label: str, name: str, row: dict, expected, *, repaired: bool) -> None:
    if not repaired and name in {"zero_bands", "negative_bands", "zero_norm",
                                 "negative_norm", "nan_norm", "start_keyword_suffix",
                                 "end_keyword_suffix", "norm_keyword_suffix", "descending_grid",
                                 "flat_grid", "zero_grid", "nonfinite_sample",
                                 "tiny_norm_overflow"}:
        # The pristine reader accepts each of these.
        if row["rc"] != 0:
            raise AssertionError(f"{label}/{name}: pristine reader refused {row}")
        return
    if isinstance(expected, str):
        if row["rc"] == 0 or expected not in row.get("message", ""):
            raise AssertionError(f"{label}/{name}: expected refusal {expected!r}, got {row}")
        return
    if row["rc"] != 0:
        raise AssertionError(f"{label}/{name}: expected acceptance, got {row}")
    for key, value in expected.items():
        if row[key] != value:
            raise AssertionError(f"{label}/{name}: {key} is {row[key]!r}, not {value!r}")


def compile_reader(text: str, archive: zipfile.ZipFile, directory: Path,
                   compiler: str, sanitize: bool, composed: Path | None = None) -> Path:
    tree = directory / "source"
    # Whole directories: cgats.c and pars.c include their own .c companions.
    # A composed tree supplies every one of them as the workers compile it.
    for member in archive.namelist():
        name = member.removeprefix(PREFIX)
        if name.split("/", 1)[0] in INCLUDES and not member.endswith("/"):
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_bytes((composed / name).read_bytes() if composed
                                      else archive.read(member))
    (tree / "sa_config.h").write_bytes(archive.read(PREFIX + "h/aconfig.h"))
    (tree / PATH).parent.mkdir(parents=True, exist_ok=True)
    (tree / PATH).write_text(text, encoding="utf-8", newline="\n")
    exe = directory / ("reader" + (".exe" if platform.system() == "Windows" else ""))
    argv = [compiler, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB",
            "-DNT" if platform.system() == "Windows" else "-DUNIX",
            *sanitizers.flags(sanitize), f"-I{tree}", *(f"-I{tree / d}" for d in INCLUDES),
            str(Path(__file__).with_name("ccss_cgats_probe.c")), str(tree / PATH),
            *(str(tree / name) for name in SUPPORT), *_system_flags(), "-o", str(exe)]
    run = subprocess.run(argv, capture_output=True, text=True, timeout=240)
    (directory / "compile.log").write_text(run.stdout + run.stderr)
    run.check_returncode()
    return exe


def read_case(exe: Path, directory: Path, label: str, name: str, text: str) -> dict:
    case = directory / f"{name}.ccss"
    case.write_text(text, encoding="utf-8", newline="\n")
    run = subprocess.run([str(exe), str(case)], capture_output=True, text=True, timeout=30)
    (directory / f"{name}.log").write_text(run.stdout + run.stderr)
    sanitizers.refuse(run.stderr, f"{label}/{name}")
    run.check_returncode()
    return json.loads(run.stdout)


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    output.mkdir(parents=True, exist_ok=False)
    report = dict(status="incomplete", archive_sha256=ARCHIVE_SHA256, path=PATH,
                  proof_sha256=digest(Path(__file__).read_bytes()),
                  probe_sha256=digest(Path(__file__).with_name("ccss_cgats_probe.c")
                                      .read_bytes()),
                  sanitize=sanitize, hardware_tested=False, support="pristine",
                  scope="exact read_ccss_cgats through ccss.read_ccss on crafted files; "
                        "installed-file search replaced by a stand-in listing nothing",
                  )
    readings: dict[str, dict[str, str]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            (output / "Argyll-License2.txt").write_bytes(z.read(PREFIX + "License2.txt"))
            before = before_source("ccss_cgats", PATH, archive)
            after = production_text(source, PATH)
            report["before_sha256"] = digest(before.encode())
            report["patched_sha256"] = digest(after.encode())
            for label, text in (("baseline", before), ("patched", after)):
                directory = output / label
                directory.mkdir()
                composed = source if label == "patched" else None
                exe = compile_reader(text, z, directory, cc, sanitize, composed)
                if composed:
                    report["support"] = "composed"
                    report["support_sha256"] = {
                        name: digest((directory / "source" / name).read_bytes())
                        for name in SUPPORT}
                results = {}
                cases = {name: (case, expected, pristine_safe)
                         for name, (case, expected, pristine_safe) in CASES.items()
                         if label == "patched" or pristine_safe}
                if composed:
                    cases |= {name: (case, expected, False)
                              for name, (case, expected) in COMPOSED_CASES.items()}
                for name, (case, expected, _) in cases.items():
                    if composed:
                        expected = COMPOSED_REFUSALS.get(name, expected)
                    row = read_case(exe, directory, label, name, case)
                    _check(label, name, row, expected, repaired=label == "patched")
                    results[name] = row.get("message") or "accepted"
                readings[label] = results
        report["results"] = readings
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
