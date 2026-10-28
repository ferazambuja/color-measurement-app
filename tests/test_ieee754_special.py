"""IEEE 754 special encodings decode as specials; every other word is unchanged."""
import json
import shutil
import subprocess
import sys
from ctypes import c_ulong, sizeof
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import ieee754_special as proof  # noqa: E402
from native_argyll import ss_protocol  # noqa: E402
from native_argyll.proof_inputs import before_source  # noqa: E402
from native_argyll.spyder_calibration_slots import _function  # noqa: E402


@pytest.fixture(scope="module")
def original(argyll_archive):
    return before_source("ieee754_special", proof.PATH, argyll_archive)


@pytest.mark.parametrize("sanitize", [False, True])
def test_actual_converters_decode_specials_and_keep_every_control(
        argyll_archive, tmp_path, prepared_production, sanitize):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    result = proof.prove(argyll_archive, tmp_path / "proof", source=prepared_production,
                         sanitize=sanitize)
    assert result["status"] == "passed"
    assert result["results"]["baseline"]["cases"] == len(proof.WORDS) == 30
    assert result["results"]["patched"]["cases"] == 30
    baseline, patched = (result["results"][k]["specials"] for k in ("baseline", "patched"))
    # The defect, recorded: finite single-precision decodes, infinities for 64-bit NaN words.
    assert baseline["32/7F800000"] == "3.4028236692093846e+38"
    assert baseline["32/7FC00000"] == "5.104235503814077e+38"
    assert baseline["64/7FF8000000000000"] == "inf"
    assert patched == {
        "32/7F800000": "inf", "32/FF800000": "-inf", "32/7FC00000": "nan", "32/7F800001": "nan",
        "32/7FFFFFFF": "nan", "32/FFFFFFFF": "nan",
        "64/7FF0000000000000": "inf", "64/FFF0000000000000": "-inf",
        "64/7FF8000000000000": "nan", "64/7FF0000000000001": "nan",
        "64/7FFFFFFFFFFFFFFF": "nan", "64/FFFFFFFFFFFFFFFF": "nan",
    }
    # Decoding then re-encoding, as the i1Pro EEPROM checksum does.
    encoded = result["results"]["patched"]["encodings"]
    assert {k: encoded[k] for k in patched} == {
        "32/7F800000": "7F800000", "32/FF800000": "FF800000", "32/7FC00000": "7FC00000",
        "32/7F800001": "7FC00000", "32/7FFFFFFF": "7FC00000", "32/FFFFFFFF": "7FC00000",
        "64/7FF0000000000000": "7FF0000000000000", "64/FFF0000000000000": "FFF0000000000000",
        "64/7FF8000000000000": "7FF8000000000000", "64/7FF0000000000001": "7FF8000000000000",
        "64/7FFFFFFFFFFFFFFF": "7FF8000000000000", "64/FFFFFFFFFFFFFFFF": "7FF8000000000000",
    }
    pristine = result["results"]["baseline"]["encodings"]
    # The pristine 64-bit decoder already returns infinity, which its encoder
    # cannot take; the probe skips those executions instead of running them.
    assert all(pristine[k] is None for k in patched if k.startswith("64/"))
    # Below 2^-971 the pristine 64-bit scale overflows; the exact encoder runs them.
    assert all(pristine[f"{w}/{word}"] is None and encoded[f"{w}/{word}"] == word
               for w, word in proof.ENCODE_UNDEFINED)
    # Every finite control round-trips; -0 keeps the encoder's +0 convention.
    for w, word, _, kind in proof.WORDS:
        if kind == "control":
            negative_zero = int(word, 16) == 1 << (w - 1)
            assert encoded[f"{w}/{word}"] == ("0" * (w // 4) if negative_zero else word)
    # The pristine log/floor path gave the smallest single subnormal a normal exponent.
    assert pristine["32/00000001"] == "00800001"
    assert result["finite_corrections"]["32/00000001"] == {
        "pristine": "00800001", "repaired": "00000001"}


def test_production_helper_and_safety_classification_accept_comments_and_spacing(
        argyll_archive, tmp_path, prepared_production, mutate_production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")

    def comment(text, path):
        helper = _function(text, "IEEE754_special_value")
        anchor = "\tdouble one = 1.0, value;"
        assert helper.count(anchor) == 1
        changed_helper = helper.replace(anchor, "\t/* Current production helper. */\n" + anchor)
        text = text.replace(helper, changed_helper, 1)
        changes = {
            "if (d != d)": "if\t( d /* classify NaN */ != d )",
            "if (d > DBL_MAX)": "if ( d /* classify infinity */ > DBL_MAX )",
            "d = frexp(d, &be);": "d = frexp( /* bounded fraction */ d, &be );",
            "d = ldexp(d, be + 52 - ee);": "d = ldexp( d, be + 52 /* scale */ - ee );",
        }
        for old, new in changes.items():
            assert old in text
            text = text.replace(old, new)
        return text

    source = mutate_production([proof.PATH], comment)
    report = proof.prove(argyll_archive, tmp_path / "commented", source=source, sanitize=True)
    assert report["status"] == "passed"
    assert report["patched_sha256"] == proof.digest((source / proof.PATH).read_bytes())
    assert report["patched_sha256"] != proof.digest((prepared_production / proof.PATH).read_bytes())
    assert report["results"]["patched"]["encodings"]["64/0000000000000001"] == "0000000000000001"


@pytest.mark.parametrize(("mutation", "symptom"), [
    ("encoders_unguarded", "at 32/7F800000: None is not 7F800000"),
    ("finite_log_path", "at 32/00000001: 00800001 is not 00000001"),
    ("nan_encoded_as_inf", "at 32/7FC00000: 7F800000 is not 7FC00000"),
    ("infinity_sign_dropped", "at 32/FF800000: 7F800000 is not FF800000"),
    ("only_32_encoder", "at 64/7FF0000000000000: None is not 7FF0000000000000"),
])
def test_encoder_regressions_fail_for_their_behavior(
        argyll_archive, tmp_path, mutate_production, original, mutation, symptom):
    """Each fault fails on the word it gets wrong. A missing exit is not run on
    a non-finite input (that is the undefined behaviour), so its word is absent;
    an exit that encodes wrongly runs and its wrong word is reported."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    nan32 = "\t\treturn 0x7fc00000;\n"
    inf32 = "\t\treturn (sn << 31) | 0x7f800000;\n"

    def regressed(text, path):
        fixed = text
        if mutation == "encoders_unguarded":
            for name in ("doubletoIEEE754", "doubletoIEEE754_64"):
                fixed = fixed.replace(_function(fixed, name), _function(original, name), 1)
        elif mutation == "finite_log_path":
            for name in ("doubletoIEEE754", "doubletoIEEE754_64"):
                current, baseline = _function(fixed, name), _function(original, name)
                marker = "\tif (d != 0.0) {"
                assert current.count(marker) == baseline.count(marker) == 1
                unguarded = current[:current.index(marker)] + baseline[baseline.index(marker):]
                fixed = fixed.replace(current, unguarded, 1)
        elif mutation == "nan_encoded_as_inf":
            fixed = fixed.replace(nan32, "\t\treturn 0x7f800000;\n")
        elif mutation == "infinity_sign_dropped":
            fixed = fixed.replace(inf32, "\t\treturn 0x7f800000;\n")
        else:
            fixed = fixed.replace(_function(fixed, "doubletoIEEE754_64"),
                                  _function(original, "doubletoIEEE754_64"), 1)
        assert fixed != text
        return fixed

    source = mutate_production([proof.PATH], regressed)
    with pytest.raises(AssertionError, match="repaired IEEE754 encoding differs") as raised:
        proof.prove(argyll_archive, tmp_path / "regressed", source=source, sanitize=True)
    assert symptom in str(raised.value)
    assert json.loads((tmp_path / "regressed/report.json").read_text())["status"] == "incomplete"


@pytest.mark.parametrize("repaired", [False, True])
def test_pristine_encoder_is_undefined_for_decoded_specials(
        prepared_production, original, tmp_path, repaired):
    """The defect the encoder guards exist for: once the decoder returns
    infinity or NaN, the pristine encoder's int cast is undefined (UBSan)."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    text = (prepared_production / proof.PATH).read_text()
    if not repaired:
        text = text.replace(_function(text, "doubletoIEEE754"),
                            _function(original, "doubletoIEEE754"), 1)
    source = ("#include <math.h>\n#include <float.h>\n#include <stdio.h>\n#include <string.h>\n"
              "typedef unsigned int ORD32; typedef unsigned long long ORD64;\n"
              + _function(text, "IEEE754_special_value") + "\n"
              + _function(text, "IEEE754todouble") + "\n"
              + _function(text, "doubletoIEEE754") + "\n"
              + "int main(void) {\n"
                "  printf(\"%08X %08X\\n\", doubletoIEEE754(IEEE754todouble(0x7fc00000u)),\n"
                "         doubletoIEEE754(IEEE754todouble(0xff800000u)));\n"
                "  return 0;\n}\n")
    unit, binary = tmp_path / "encode.c", tmp_path / "encode"
    unit.write_text(source)
    built = subprocess.run(["cc", "-std=c99", "-O1", *proof.sanitizers.flags(True), str(unit),
                            "-lm", "-o", str(binary)], capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    if repaired:
        assert (run.returncode, run.stdout, proof.sanitizers.diagnostic(run.stderr)) == (
            0, "7FC00000 FF800000\n", None)
    else:
        assert run.returncode != 0
        assert "outside the range of representable values" in run.stderr


@pytest.mark.parametrize("mutation", ["missing", "nan_as_inf", "sign_dropped", "only_32"])
def test_compiled_regressions_fail_for_their_behavior(
        argyll_archive, tmp_path, original, mutate_production, mutation):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    assignment = "op = IEEE754_special_value(ma != 0);"

    def regressed(text, path):
        if mutation == "missing":
            for name in ("IEEE754todouble", "IEEE754_64todouble"):
                text = text.replace(_function(text, name), _function(original, name), 1)
            return text
        fixed = text
        if mutation == "nan_as_inf":
            return fixed.replace(assignment, "op = IEEE754_special_value(0);")
        if mutation == "sign_dropped":
            return fixed.replace(assignment, assignment + " sn = 0;")
        return fixed.replace(_function(fixed, "IEEE754_64todouble"),
                             _function(original, "IEEE754_64todouble"), 1)

    source = mutate_production([proof.PATH], regressed)
    with pytest.raises(AssertionError, match="repaired IEEE754 result differs"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
    assert json.loads((tmp_path / "regressed/report.json").read_text())["status"] == "incomplete"


def test_preparation_records_the_current_imported_numeric_source_once(argyll_archive, tmp_path):
    source, report = native.prepare_with_proof_inputs(argyll_archive, tmp_path / "build")
    compiled = (source / proof.PATH).read_text()
    rows = [r for r in report["contribution"]["entries"] if r["path"] == proof.PATH]
    assert len(rows) == 1
    assert rows[0]["post_sha256"] == native.digest(compiled.encode())


@pytest.mark.parametrize("finite_sentinel", [False, True])
def test_c89_decoder_without_c99_macros(prepared_production, tmp_path, finite_sentinel):
    if shutil.which("cc") is None or sizeof(c_ulong) != 8:
        pytest.skip("C89 compiler and 64-bit unsigned long required")
    repaired = (prepared_production / proof.PATH).read_text()
    source = ("#include <math.h>\n#include <float.h>\n#include <string.h>\n"
              "#undef INFINITY\n#undef NAN\n"
              + ("#undef HUGE_VAL\n#define HUGE_VAL DBL_MAX\n" if finite_sentinel else "")
              + "typedef unsigned int ORD32; typedef unsigned long ORD64; "
                "typedef signed long INR64;\n"
              + _function(repaired, "IEEE754_special_value") + "\n"
              + _function(repaired, "IEEE754todouble") + "\n"
              + _function(repaired, "IEEE754_64todouble") + "\n"
              + _function(repaired, "doubletoIEEE754") + "\n"
              + _function(repaired, "doubletoIEEE754_64") + "\n"
              + "int main(void) {\n"
                "  double pos = IEEE754todouble(0x7f800000UL);\n"
                "  double neg = IEEE754todouble(0xff800000UL);\n"
                "  double nan32 = IEEE754todouble(0x7fc00000UL);\n"
                "  double nan64 = IEEE754_64todouble(0x7ff8000000000000UL);\n"
                "  if (sizeof(ORD64) != 8) return 7;\n"
                "  if (doubletoIEEE754(nan32) != 0x7fc00000UL\n"
                "   || doubletoIEEE754(neg) != 0xff800000UL\n"
                "   || doubletoIEEE754_64(nan64) != 0x7ff8000000000000UL\n"
                "   || doubletoIEEE754_64(neg) != 0xfff0000000000000UL) return 6;\n"
                "  return !(pos > DBL_MAX && neg < -DBL_MAX && nan32 != nan32 "
                "&& nan64 != nan64);\n}\n")
    unit = tmp_path / "c89.c"
    binary = tmp_path / "c89"
    unit.write_text(source)
    compile_result = subprocess.run(
        ["cc", "-std=c89", "-pedantic-errors", "-Wall", "-Wextra", "-Werror",
         str(unit), "-lm", "-o", str(binary)], capture_output=True, text=True)
    assert compile_result.returncode == 0, compile_result.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_finite_huge_val_with_directed_rounding(prepared_production, tmp_path):
    if shutil.which("cc") is None or sizeof(c_ulong) != 8:
        pytest.skip("C compiler and 64-bit unsigned long required")
    repaired = (prepared_production / proof.PATH).read_text()
    source = ("#include <math.h>\n#include <float.h>\n#include <string.h>\n"
              "#include <fenv.h>\n#undef HUGE_VAL\n#define HUGE_VAL DBL_MAX\n"
              "typedef unsigned int ORD32; typedef unsigned long ORD64; "
              "typedef signed long INR64;\n"
              + _function(repaired, "IEEE754_special_value") + "\n"
              + _function(repaired, "IEEE754todouble") + "\n"
              + _function(repaired, "IEEE754_64todouble") + "\n"
              + "int main(void) {\n"
                "  double pos, nan32, nan64;\n"
                "  if (fesetround(FE_TOWARDZERO) != 0) return 7;\n"
                "  feclearexcept(FE_ALL_EXCEPT);\n"
                "  pos = IEEE754todouble(0x7f800000UL);\n"
                "  nan32 = IEEE754todouble(0x7fc00000UL);\n"
                "  nan64 = IEEE754_64todouble(0x7ff8000000000000UL);\n"
                "  if (fetestexcept(FE_INVALID | FE_OVERFLOW) != 0) return 8;\n"
                "  return !(pos > DBL_MAX && nan32 != nan32 && nan64 != nan64);\n}\n")
    unit = tmp_path / "rounding.c"
    binary = tmp_path / "rounding"
    unit.write_text(source)
    compile_result = subprocess.run(
        ["cc", "-std=c99", "-O0", str(unit), "-lm", "-o", str(binary)],
        capture_output=True, text=True)
    assert compile_result.returncode == 0, compile_result.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_spectroscan_nan_classification_ignores_nan_sign(
        argyll_archive, tmp_path, prepared_production, mutate_production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler required for the source-bound SpectroScan probe")
    baseline = ss_protocol.prove(argyll_archive, tmp_path / "normal", source=prepared_production)
    quiet_nan = "\t\t\t\tbits |= (ORD64)1 << 51;"
    negative_nan = "\t\t\t\tbits |= ((ORD64)1 << 51) | ((ORD64)1 << 63);"

    def flip_nan_sign(text, path):
        assert text.count(quiet_nan) == 1
        changed = text.replace(quiet_nan, negative_nan)
        assert changed != text
        return changed

    source = mutate_production([proof.PATH], flip_nan_sign)
    changed = ss_protocol.prove(argyll_archive, tmp_path / "negative", source=source)
    assert baseline["status"] == changed["status"] == "passed"
    before = {row["arg"]: row for row in baseline["results"]["patched"]
              if row["kind"] == "double"}
    after = {row["arg"]: row for row in changed["results"]["patched"]
             if row["kind"] == "double"}
    nan_words = [arg for arg, row in before.items() if row["value"] == "nan"]
    assert nan_words
    assert all(before[arg]["negative"] != after[arg]["negative"] for arg in nan_words)
    assert all(before[arg] == after[arg] for arg in before if arg not in nan_words)


def test_encoders_scale_the_normalized_fraction_with_a_bounded_exponent(
        prepared_production, tmp_path):
    """A restricted ldexp exposes raw-magnitude scaling even on a permissive CRT."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    repaired = (prepared_production / proof.PATH).read_text()
    source = (
        "#include <math.h>\n#include <float.h>\n#include <stdio.h>\n#include <string.h>\n"
        "typedef unsigned int ORD32; typedef unsigned long long ORD64;\n"
        "static int limit, bad_scale;\n"
        "static double bounded_ldexp(double value, int exponent) {\n"
        "  if (exponent > limit) { bad_scale++; return 0.0; }\n"
        "  return ldexp(value, exponent);\n"
        "}\n#define ldexp bounded_ldexp\n"
        + _function(repaired, "doubletoIEEE754") + "\n"
        + _function(repaired, "doubletoIEEE754_64") + "\n"
        + r'''
int main(void) {
    static const ORD32 words32[] = {
        0x00000001u, 0x007fffffu, 0x00800000u, 0x3f800000u, 0x7f7fffffu,
        0x80000001u, 0x807fffffu, 0x80800000u
    };
    static const ORD64 words64[] = {
        0x0000000000000001ull, 0x000fffffffffffffull, 0x0010000000000000ull,
        0x3ff0000000000000ull, 0x7fefffffffffffffull,
        0x8000000000000001ull, 0x800fffffffffffffull, 0x8010000000000000ull
    };
    size_t i;
    limit = 24;
    for (i = 0; i < sizeof(words32) / sizeof(words32[0]); i++) {
        float value;
        memcpy(&value, &words32[i], sizeof(value));
        if (doubletoIEEE754((double)value) != words32[i] || bad_scale) {
            printf("unbounded or incorrect binary32 scale at %08X\n", words32[i]);
            return 1;
        }
    }
    limit = 53;
    for (i = 0; i < sizeof(words64) / sizeof(words64[0]); i++) {
        double value;
        memcpy(&value, &words64[i], sizeof(value));
        if (doubletoIEEE754_64(value) != words64[i] || bad_scale) {
            printf("unbounded or incorrect binary64 scale at %016llX\n", words64[i]);
            return 1;
        }
    }
    return 0;
}
'''
    )
    unit, binary = tmp_path / "bounded-scale.c", tmp_path / "bounded-scale"
    unit.write_text(source)
    built = subprocess.run(
        ["cc", "-std=c99", "-O1", str(unit), "-lm", "-o", str(binary)],
        capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout


def test_repaired_converters_round_trip_a_wide_sample_without_undefined_behavior(
        prepared_production, tmp_path):
    """Every 4093rd single-precision word and a million 64-bit words decode and
    re-encode exactly under UBSan: NaN words give the canonical NaN and -0 gives
    +0 (the encoder's existing convention); every other word is unchanged."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    repaired = (prepared_production / proof.PATH).read_text()
    source = ("#include <math.h>\n#include <float.h>\n#include <stdio.h>\n#include <string.h>\n"
              "typedef unsigned int ORD32; typedef unsigned long long ORD64;\n"
              "typedef long long INR64;\n"
              + "".join(_function(repaired, name) + "\n" for name in (
                  "IEEE754_special_value", "IEEE754todouble", "IEEE754_64todouble",
                  "doubletoIEEE754", "doubletoIEEE754_64"))
              + r'''
static int expected32(ORD32 w, ORD32 got) {
	if ((w & 0x7f800000u) == 0x7f800000u && (w & 0x7fffffu)) return got == 0x7fc00000u;
	return got == (w == 0x80000000u ? 0u : w);
}
static int expected64(ORD64 w, ORD64 got) {
	ORD64 e = (ORD64)0x7ff << 52, m = ((ORD64)1 << 52) - 1;
	if ((w & e) == e && (w & m)) return got == (e | ((ORD64)1 << 51));
	return got == (w == (ORD64)1 << 63 ? 0 : w);
}
int main(void) {
	ORD64 w64 = 0x9e3779b97f4a7c15ull, i, checked = 0;
	for (i = 0; i <= 0xffffffffull; i += 4093, checked++) {
		ORD32 w = (ORD32)i, got = doubletoIEEE754(IEEE754todouble(w));
		if (!expected32(w, got)) { printf("32 %08X -> %08X\n", w, got); return 1; }
	}
	for (i = 0; i < 1000000; i++, checked++) {
		ORD64 got;
		w64 = w64 * 6364136223846793005ull + 1442695040888963407ull;
		got = doubletoIEEE754_64(IEEE754_64todouble(w64));
		if (!expected64(w64, got)) {
			printf("64 %016llX -> %016llX\n", w64, got); return 1;
		}
	}
	printf("%llu\n", checked);
	return 0;
}
''')
    unit, binary = tmp_path / "sweep.c", tmp_path / "sweep"
    unit.write_text(source)
    built = subprocess.run(["cc", "-std=c99", "-O1", *proof.sanitizers.flags(True), str(unit),
                            "-lm", "-o", str(binary)], capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
    assert proof.sanitizers.diagnostic(run.stderr) is None, run.stderr
    assert run.returncode == 0, run.stdout
    assert int(run.stdout) == 0xFFFFFFFF // 4093 + 1 + 1000000
