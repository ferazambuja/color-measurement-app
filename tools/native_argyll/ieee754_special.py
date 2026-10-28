"""Decode IEEE 754 special encodings as specials, not as finite numbers.

ArgyllCMS 3.5.0's shared converters treat an all-ones exponent field as an
ordinary power of two. ``IEEE754todouble`` (numlib/numsup.c:3500-3517) turns
every single-precision infinity or NaN word into a finite double of magnitude
[2^128, 2^129): +inf becomes 2^128 and a quiet NaN 1.5 * 2^128. Its
double-precision sibling ``IEEE754_64todouble`` (:3564-3583) multiplies by
pow(2, 1024), so its infinities come out right but every NaN word becomes an
infinity. The single-precision helper decodes SpectroScan spot and X-Y values
(ss_imp.c:322-331), SMCube and Spyder 2024 readings and stored calibration.
The read_FLT32 wrappers also serve i1Pro3 timing, temperature and stored tables.
The double-precision helper decodes Klein K10 CalMan calibration matrices
(kleink10.c:680-778), Spyder 4 calibration spectra and OEM archive spectra. After
the finite conversion no downstream finiteness check can see the encoding;
the worker's patch_valid in session.c, the Python models and the MATLAB
projection all accept the value.

The repair adds one branch to each decoder: an all-ones exponent yields
infinity, signed by the existing sign step, when the mantissa is zero and NaN
otherwise. A shared C89 helper constructs specials from verified native IEEE
bits, including on systems whose HUGE_VAL is finite.
Every other word decodes bit-for-bit as before. The existing
finiteness checks are then the refusal; no caller and no validity policy
changes.

Decoding specials makes them reachable by the encoders. ``doubletoIEEE754``
(:3461-3494) and ``doubletoIEEE754_64`` (:3525-3558) take
``(int)floor(log(d)/log(2.0))`` of any nonzero input, which is undefined for
NaN and infinity. The i1Pro EEPROM checksum re-encodes every decoded double
(spectro/i1pro_imp.c:13474) when the instrument is opened. Both encoders now
return the canonical quiet NaN or the signed infinity encoding before any
exponent work. Their finite exponent selection used floor(log(d)/log(2.0)) and
pow(), which gave single subnormals a normal exponent and overflowed below
2^-971 for doubles; C89 frexp/ldexp replace it. Scaling the normalized fraction
instead of the original magnitude bounds a positive exponent to 24 or 53,
even for subnormals. This avoids a Windows CRT overflow from ldexp(d, 1074),
so every finite word round-trips. Truncation and the +0 encoding of -0 are
unchanged. A NaN payload
is not preserved, so a stored non-canonical NaN fails that checksum. The dormant
write-back encoder use, i1data_ser_doubles (:12915), is compiled out by
``#undef ENABLE_WRITE`` (:108), which argyll_native_check keeps disabled.
Extracts retain Graeme W. Gill's GPL-2.0-or-later notice.
"""
from __future__ import annotations

import io
import json
import platform
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.munki_ledtemp_slope import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.no_termination import _code
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import _function

REPAIR_ID = 'recipe:ieee754-special'

PATH = "numlib/numsup.c"
# The encoders take log() of the magnitude and convert it to int: undefined for
# NaN and infinity. Once the decoders return specials, a decode/re-encode such
# as the i1Pro EEPROM checksum (spectro/i1pro_imp.c:13474) reaches that cast.
# Specials are encoded before any exponent work.
ENCODER_NAMES = ("doubletoIEEE754", "doubletoIEEE754_64")
# The pristine finite path selects the exponent with floor(log(d)/log(2.0)),
# which misclassifies exact powers of two near a boundary, gives single
# subnormals a normal exponent, and scales with pow(0.5, ee - 52), which
# overflows below 2^-971 (numsup.c:3475-3480, :3539-3544). frexp and ldexp are
# exact C89 operations. Scaling the normalized fraction avoids a large
# positive ldexp exponent on subnormals; "too small" tests the scaled mantissa.
# Truncation and the encoded zero sign (-0 encodes as +0) are unchanged.
FINITE64_REQUIRED = (
    "int ee, be;",
    "d = frexp(d, &be); ee = be - 1;",
    "d = ldexp(d, be + 52 - ee);",
    "if (d < (double)((ORD64)1 << 52))",
)
#: The conditions that make an encoder's special-value exits present.
ENCODER_EXITS = ("\tif (d != d)", "\tif (d > DBL_MAX)")
#: Finite controls the pristine 64-bit encoder cannot encode. Below 2^-971 its
#: pow(0.5, ee - 52) scale overflows (numsup.c:3541-3542) and the (ORD64) cast of
#: infinity is undefined. Execute them only with the bounded finite-scale path.
ENCODE_UNDEFINED = {(64, "0010000000000000"), (64, "0000000000000001")}
#: The canonical encodings of the decoded specials, per width.
SPECIAL_ENCODINGS = {
    32: {"inf": "7F800000", "-inf": "FF800000", "nan": "7FC00000"},
    64: {"inf": "7FF0000000000000", "-inf": "FFF0000000000000", "nan": "7FF8000000000000"},
}
#: (width, word, label, kind): six all-ones-exponent words and nine controls per width.
WORDS = (
    (32, "7F800000", "+inf", "special"), (32, "FF800000", "-inf", "special"),
    (32, "7FC00000", "quiet NaN", "special"), (32, "7F800001", "signalling NaN", "special"),
    (32, "7FFFFFFF", "NaN max mantissa", "special"), (32, "FFFFFFFF", "NaN all ones", "special"),
    (32, "7F7FFFFF", "FLT_MAX", "control"), (32, "FF7FFFFF", "-FLT_MAX", "control"),
    (32, "00800000", "FLT_MIN", "control"), (32, "00000001", "min subnormal", "control"),
    (32, "00000000", "+0", "control"), (32, "80000000", "-0", "control"),
    (32, "3F800000", "1.0", "control"), (32, "3F000000", "0.5", "control"),
    (32, "42C80000", "100.0", "control"),
    (64, "7FF0000000000000", "+inf", "special"), (64, "FFF0000000000000", "-inf", "special"),
    (64, "7FF8000000000000", "quiet NaN", "special"),
    (64, "7FF0000000000001", "signalling NaN", "special"),
    (64, "7FFFFFFFFFFFFFFF", "NaN max mantissa", "special"),
    (64, "FFFFFFFFFFFFFFFF", "NaN all ones", "special"),
    (64, "7FEFFFFFFFFFFFFF", "DBL_MAX", "control"),
    (64, "FFEFFFFFFFFFFFFF", "-DBL_MAX", "control"),
    (64, "0010000000000000", "DBL_MIN", "control"),
    (64, "0000000000000001", "min subnormal", "control"),
    (64, "0000000000000000", "+0", "control"), (64, "8000000000000000", "-0", "control"),
    (64, "3FF0000000000000", "1.0", "control"), (64, "3FE0000000000000", "0.5", "control"),
    (64, "4059000000000000", "100.0", "control"),
)


def _compact_code(text: str) -> str:
    """Ignore C comments, literals and whitespace when classifying unsafe controls."""
    return "".join(_code(text).split())


def _probe_source(text: str) -> str:
    encoders = {name: _compact_code(_function(text, name)) for name in ENCODER_NAMES}
    finite64 = all(_compact_code(fragment) in encoders["doubletoIEEE754_64"]
                   for fragment in FINITE64_REQUIRED)
    try:
        helper = _function(text, "IEEE754_special_value") + "\n"
    except ValueError:
        # The frozen predecessor has no helper. A present but malformed symbol
        # must not masquerade as that deliberate baseline.
        if "IEEE754_special_value" in _code(text):
            raise
        helper = ""
    rows = ",\n".join(
        f'\t\t{{{width}, 0x{word}{"u" if width == 32 else "ull"}, "{label}", "{kind}", '
        f'{int((width, word) not in ENCODE_UNDEFINED or finite64)}}}'
        for width, word, label, kind in WORDS)
    # Re-encode every decoded value, as the i1Pro checksum does. The pristine
    # encoders' int cast is undefined for a non-finite input, so an encoder
    # without both special-value exits is not executed on one. A guard that
    # exists but encodes wrongly still runs, so its wrong word is what fails.
    guarded = {name: int(all(_compact_code(exit) in body for exit in ENCODER_EXITS))
               for name, body in encoders.items()}
    return ("/* Exact ArgyllCMS converters, Graeme W. Gill, GPL-2.0-or-later. */\n"
            "#include <math.h>\n#include <float.h>\n#include <stdio.h>\n#include <string.h>\n"
            "typedef unsigned int ORD32; typedef unsigned long long ORD64;\n"
            "typedef long long INR64;\n"
            f"#define GUARDED32 {guarded['doubletoIEEE754']}\n"
            f"#define GUARDED64 {guarded['doubletoIEEE754_64']}\n"
            + helper
            + _function(text, "IEEE754todouble") + "\n" + _function(text, "IEEE754_64todouble")
            + "\n" + _function(text, "doubletoIEEE754") + "\n"
            + _function(text, "doubletoIEEE754_64")
            + r'''
static void number(double v) {
	if (isnan(v)) printf("\"nan\"");
	else if (isinf(v)) printf("\"%sinf\"", v < 0 ? "-" : "");
	else printf("\"%.17g\"", v);
}

int main(void) {
	static const struct { int width; ORD64 word; const char *label; const char *kind;
		int encode; } cases[] = {
@@ROWS@@
	};
	size_t i, n = sizeof(cases) / sizeof(cases[0]);
	puts("[");
	for (i = 0; i < n; i++) {
		double pinned, native;
		if (cases[i].width == 32) {
			ORD32 w = (ORD32)cases[i].word; float f;
			pinned = IEEE754todouble(w); memcpy(&f, &w, sizeof f); native = (double)f;
		} else {
			ORD64 w = cases[i].word;
			pinned = IEEE754_64todouble(w); memcpy(&native, &w, sizeof native);
		}
		printf("\t{\"width\":%d,\"word\":\"%0*llX\",\"label\":\"%s\",\"kind\":\"%s\",",
			cases[i].width, cases[i].width / 4, (unsigned long long)cases[i].word,
			cases[i].label, cases[i].kind);
		printf("\"value\":");
		number(pinned);
		printf(",\"finite\":%d,\"signbit\":%d,\"native\":",
			isfinite(pinned) != 0, signbit(pinned) != 0);
		number(native);
		printf(",\"native_signbit\":%d,\"encoded\":", signbit(native) != 0);
		if (!cases[i].encode || (!isfinite(pinned)
			&& !(cases[i].width == 32 ? GUARDED32 : GUARDED64)))
			printf("null");
		else if (cases[i].width == 32)
			printf("\"%08X\"", (unsigned int)doubletoIEEE754(pinned));
		else
			printf("\"%016llX\"", (unsigned long long)doubletoIEEE754_64(pinned));
		printf("}%s\n", i + 1 < n ? "," : "");
	}
	puts("]");
	return 0;
}
'''.replace("@@ROWS@@", rows))


def _formula32(word: int) -> float:
    """The pristine single-precision arithmetic, restated independently."""
    sn, ep, ma = (word >> 31) & 1, (word >> 23) & 0xFF, word & 0x7FFFFF
    op = ma / 2.0**23 * 2.0**-126 if ep == 0 else (ma | 1 << 23) / 2.0**23 * 2.0 ** (ep - 127)
    return -op if sn else op


def _validate(rows: list, *, repaired: bool) -> None:
    label = "repaired" if repaired else "original"
    if [(r["width"], r["word"]) for r in rows] != [(w, word) for w, word, _, _ in WORDS]:
        raise AssertionError(f"{label} IEEE754 case census changed")
    for row in rows:
        width, word, kind = row["width"], int(row["word"], 16), row["kind"]
        sign = (word >> (width - 1)) & 1
        mantissa = word & ((1 << (23 if width == 32 else 52)) - 1)
        if kind == "control" or repaired:
            # Round-trippable finite values and signed zero match the native
            # reinterpretation. Infinity retains its sign. NaN payload and sign
            # are not part of the decoder's classification contract.
            want = dict(value=row["native"], signbit=row["native_signbit"],
                        finite=int(kind == "control"))
        elif width == 32:
            want = dict(finite=1, signbit=sign)
            if not row["finite"] or float(row["value"]) != _formula32(word):
                raise AssertionError(f"{label} IEEE754 result differs at {width}/{row['word']}: "
                                     f"{row['value']} is not the pristine finite value")
        else:
            # pow(2.0, 1024.0) is HUGE_VAL, so the pristine 64-bit helper
            # already yields a signed infinity for every all-ones exponent.
            want = dict(value="-inf" if sign else "inf", finite=0, signbit=sign)
        if kind == "special" and repaired:
            want["value"] = "nan" if mantissa else ("-inf" if sign else "inf")
            if mantissa:
                want.pop("signbit")
        mismatch = {k: (row.get(k), v) for k, v in want.items() if row.get(k) != v}
        if mismatch:
            raise AssertionError(
                f"{label} IEEE754 result differs at {width}/{row['word']}: {mismatch}")
        encoded = row.get("encoded")
        expected = None
        if kind == "special" and repaired:
            expected = SPECIAL_ENCODINGS[width][want["value"]]
        elif repaired:
            # Every finite control round-trips; -0 keeps the encoder's +0 convention.
            expected = "0" * (width // 4) if word == 1 << (width - 1) else row["word"]
        if expected is not None:
            if encoded != expected:
                raise AssertionError(f"{label} IEEE754 encoding differs at {width}/{row['word']}: "
                                     f"{encoded} is not {expected}")
        elif (width, row["word"]) in ENCODE_UNDEFINED or (kind == "special" and width == 64):
            if encoded is not None:
                raise AssertionError(f"{label} IEEE754 encoder executed an undefined input "
                                     f"at {width}/{row['word']}")
        elif not isinstance(encoded, str):
            raise AssertionError(f"{label} IEEE754 encoding missing at {width}/{row['word']}")


def _finite_encodings(rows: list) -> dict:
    return {f"{r['width']}/{r['word']}": r["encoded"] for r in rows if r["kind"] == "control"}


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        notice = z.read(PREFIX + "License2.txt")
    before = before_source("ieee754_special", PATH, archive)
    after = production_text(source, PATH)
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(notice)
    report = dict(status="incomplete", archive_sha256=ARCHIVE_SHA256, path=PATH,
                  before_sha256=digest(before.encode()), patched_sha256=digest(after.encode()),
                  proof_sha256=digest(Path(__file__).read_bytes()), sanitize=sanitize,
                  scope="exact IEEE754todouble, IEEE754_64todouble and both encoders, "
                        "decoding then re-encoding each word; no caller, transport or device",
                  hardware_tested=False, commands=[], results={})
    finite: dict[str, dict] = {}
    try:
        for label, text in (("baseline", before), ("patched", after)):
            unit = output / f"{label}.c"
            unit.write_text(_probe_source(text), encoding="utf-8", newline="\n")
            exe = output / (label + (".exe" if platform.system() == "Windows" else ""))
            argv = [cc, "-std=c99", "-O1", "-g", *sanitizers.flags(sanitize), str(unit), "-lm",
                    "-o", str(exe)]
            run = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            (output / f"{label}-compile.log").write_text(run.stdout + run.stderr)
            report["commands"].append({"argv": argv, "returncode": run.returncode})
            run.check_returncode()
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
            (output / f"{label}.log").write_text(run.stdout + run.stderr)
            sanitizers.refuse(run.stderr, label)
            run.check_returncode()
            if run.stderr.strip():
                raise RuntimeError(f"Unexpected IEEE754 proof diagnostic: {label}")
            rows = json.loads(run.stdout)
            _validate(rows, repaired=label == "patched")
            report["results"][label] = {
                "cases": len(rows), "sha256": digest(run.stdout.encode()),
                "specials": {f"{r['width']}/{r['word']}": r["value"]
                             for r in rows if r["kind"] == "special"},
                # Recorded, not asserted for NaN: the sign a canonical NaN carries
                # is the compiler's, and a regression must be able to see it move.
                "signbits": {f"{r['width']}/{r['word']}": r["signbit"]
                             for r in rows if r["kind"] == "special"},
                "encodings": {f"{r['width']}/{r['word']}": r["encoded"] for r in rows}}
            finite[label] = _finite_encodings(rows)
        # Recorded: the pristine finite results the exact encoder corrects.
        report["finite_corrections"] = {
            k: {"pristine": v, "repaired": finite["patched"][k]}
            for k, v in finite["baseline"].items() if v != finite["patched"][k]}
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
