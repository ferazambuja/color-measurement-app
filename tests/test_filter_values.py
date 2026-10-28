"""Custom filters the interpolators cannot evaluate are refused; valid ones convert as before."""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from native_argyll import filter_values as proof  # noqa: E402

# Independent regression anchors from Graeme W. Gill's GPL-2.0-or-later Argyll source.
CONVERTER_CHECK = (
    '\t/* The filter must be evaluable, with finite positive divisors and finite */\n'
    '\t/* quotients before any patch changes: an unusable filter is refused */\n'
    '\t/* whole rather than applied to some bands or some patches. */\n'
    '\tif (!inst_valid_custom_filter(filt))\n'
    '\t\treturn 2;\n'
    '\tfor (i = 0; i < nvals; i++) {\n'
    '\t\tif ((   vals[i].mtype != inst_mrt_emission\n'
    '\t\t     && vals[i].mtype != inst_mrt_ambient\n'
    '\t\t     && vals[i].mtype != inst_mrt_emission_flash\n'
    '\t\t     && vals[i].mtype != inst_mrt_ambient_flash)\n'
    '\t\t || vals[i].sp.spec_n <= 0) {\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
    '\t\tfor (j = 0; j < vals[i].sp.spec_n; j++) {\n'
    '\t\t\tdouble nm = XSPECT_XWL(&vals[i].sp, j);\n'
    '\t\t\tdouble fw, quotient;\n'
    '\t\t\tgetval_xspec(filt, &fw, nm);\n'
    '\t\t\t/* Refuse the divisor before dividing by it. */\n'
    '\t\t\tif (!inst_double_finite(fw) || fw <= 0.0)\n'
    '\t\t\t\treturn 2;\n'
    '\t\t\tquotient = vals[i].sp.spec[j] / fw;\n'
    '\t\t\tif (!inst_double_finite(quotient))\n'
    '\t\t\t\treturn 2;\n'
    '\t\t}\n'
    '\t}\n'
    '\n'
)
XYZ_CHECK = (
    '\t/* Check the corrected XYZ on scratch spectra before changing any patch. */\n'
    '\t/* Finite individual bands can still overflow during CIE integration. The */\n'
    '\t/* check converts unclamped: clamping would turn -inf into a finite 0. */\n'
    '\tif (conv != NULL) {\n'
    '\t\txsp2cie *check = new_xsp2cie(icxIT_none, 0.0, NULL, icxOT_CIE_1931_2,\n'
    '\t\t                             NULL, icSigXYZData, icxNoClamp);\n'
    '\t\tif (check == NULL) {\n'
    '\t\t\tconv->del(conv);\n'
    '\t\t\treturn 1;\n'
    '\t\t}\n'
    '\t\tfor (i = 0; i < nvals; i++) {\n'
    '\t\t\tdouble xyz[3];\n'
    '\t\t\tif (!vals[i].XYZ_v\n'
    '\t\t\t || (vals[i].mtype != inst_mrt_emission\n'
    '\t\t\t  && vals[i].mtype != inst_mrt_ambient\n'
    '\t\t\t  && vals[i].mtype != inst_mrt_emission_flash\n'
    '\t\t\t  && vals[i].mtype != inst_mrt_ambient_flash)\n'
    '\t\t\t || vals[i].sp.spec_n <= 0)\n'
    '\t\t\t\tcontinue;\n'
    '\t\t\ttmp = vals[i].sp;\n'
    '\t\t\tfor (j = 0; j < tmp.spec_n; j++) {\n'
    '\t\t\t\tdouble fw;\n'
    '\t\t\t\tgetval_xspec(filt, &fw, XSPECT_XWL(&tmp, j));\n'
    '\t\t\t\ttmp.spec[j] /= fw;\n'
    '\t\t\t}\n'
    '\t\t\tcheck->convert(check, xyz, &tmp);\n'
    '\t\t\tif (!inst_double_finite(xyz[0]) || !inst_double_finite(xyz[1])\n'
    '\t\t\t || !inst_double_finite(xyz[2])) {\n'
    '\t\t\t\tcheck->del(check);\n'
    '\t\t\t\tconv->del(conv);\n'
    '\t\t\t\treturn 2;\n'
    '\t\t\t}\n'
    '\t\t}\n'
    '\t\tcheck->del(check);\n'
    '\t}\n'
    '\n'
)


@pytest.mark.parametrize("sanitize", [False, True])
def test_original_fabricates_and_repair_refuses_without_touching_patches(
        argyll_archive, prepared_production, tmp_path, sanitize):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    result = proof.prove(argyll_archive, tmp_path / "proof", source=prepared_production,
                         sanitize=sanitize)
    assert result["status"] == "passed"
    baseline = {r["case"]: r for r in result["results"]["baseline"]["converter"]}
    patched = {r["case"]: r for r in result["results"]["patched"]["converter"]}
    # The defect, recorded: fabricated values from positive files and norm 0.
    assert baseline["zero_inside"]["band_415"] == "inf"
    assert baseline["negative_inside"]["band_415"] < 0
    assert baseline["zero_norm"]["band_380"] == 0.0
    assert baseline["subnormal_norm"]["band_380"] == 0.0
    assert baseline["subnormal_norm"]["xyz1_y"] == 0.0
    # A late patch's finite corrected bands overflow only in real CIE integration.
    assert baseline["late_integral_overflow"]["all_finite"] == 1
    assert baseline["late_integral_overflow"]["xyz_finite"] == 0
    # Clamping hides the same overflow when negative: -inf becomes a finite zero.
    assert baseline["clamped_integral_overflow"]["xyz_finite"] == 1
    assert baseline["clamped_integral_overflow"]["xyz1_y"] == 0.0
    assert all(patched[c]["rc"] == 2 and not patched[c]["changed"] for c in proof.CONV_REFUSE)
    assert all(patched[c]["rc"] == 0 for c in proof.CONV_ACCEPT)
    assert all(patched[c]["rc"] == 1 and not patched[c]["changed"]
               for c in proof.CONV_ALLOC_FAIL)
    # Three samples intentionally use the current linear fallback; the old cubic
    # evaluator read a fourth sample. Its numerical contract is checked below.
    assert all(patched[c]["patch_bytes"] == baseline[c]["patch_bytes"]
               for c in proof.CONV_ACCEPT - {"three_cubic"})
    assert all(r["live"] == 0 and r["allocations"] == r["releases"] for r in patched.values())
    assert result["results"]["patched"]["setter_cases"] == 105


def test_three_sample_filter_uses_exact_linear_interpolation(
    argyll_archive, prepared_production, tmp_path, monkeypatch
):
    """Exercise a nonconstant three-point filter, including both clamped ends."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    main = proof.CONVERTER_MAIN
    samples = "three[3] = {1,1,1};"
    assert main.count(samples) == 1
    main = main.replace(samples, "three[3] = {1,2,4};", 1)
    observation = '        number("band_415", after[0].sp.spec[7], ",");\n'
    assert main.count(observation) == 1
    linear_outputs = r'''
        if (!strcmp(cases[c].name, "three_cubic")) {
            for (i = 0; i < 2; i++) for (j = 0; j < 71; j++) {
                char key[32];
                snprintf(key, sizeof key, "linear_%d_%d", i, j);
                number(key, after[i].sp.spec[j], ",");
            }
        }
'''
    monkeypatch.setattr(proof, "CONVERTER_MAIN", main.replace(
        observation, observation + linear_outputs, 1))
    result = proof.prove(argyll_archive, tmp_path / "linear", source=prepared_production,
                         variants=("patched",))
    row = next(r for r in result["results"]["patched"]["converter"]
               if r["case"] == "three_cubic")
    assert row["rc"] == 0 and row["all_finite"] == row["xyz_finite"] == 1
    for patch in range(2):
        for band in range(71):
            wavelength = 380 + 5 * band
            if wavelength <= 400:
                divisor = 1.0
            elif wavelength < 410:
                divisor = 1.0 + (wavelength - 400) / 10.0
            elif wavelength < 420:
                divisor = 2.0 + (wavelength - 410) / 5.0
            else:
                divisor = 4.0
            expected = (1.0 + 0.01 * band + patch) / divisor
            assert row[f"linear_{patch}_{band}"] == pytest.approx(expected, rel=0, abs=1e-14)


@pytest.mark.parametrize("mutation", [
    "setter_guard_missing_ss", "divisor_sign_unchecked", "divisor_finite_unchecked",
    "quotient_unchecked",
    "non_transactional", "xyz_unchecked", "xyz_check_clamped",
    "check_allocation_leaks", "unused_sample_rejected"])
def test_compiled_regressions_fail_for_their_behavior(
        argyll_archive, tmp_path, mutation, mutate_production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    divisor = "if (!inst_double_finite(fw) || fw <= 0.0)\n"
    quotient = "\t\t\tif (!inst_double_finite(quotient))\n\t\t\t\treturn 2;\n"

    def regressed(text, path):
        fixed = text
        if mutation == "setter_guard_missing_ss":
            if path == "spectro/ss.c":
                guard = ("\t\t} else if (!inst_valid_custom_filter(sp)) {\n"
                         "\t\t\treturn inst_bad_parameter;"
                         "\t/* Not evaluable by the interpolators */\n")
                assert fixed.count(guard) == 1
                return fixed.replace(guard, "", 1)
            return fixed
        if path != "spectro/inst.c":
            return fixed
        if mutation == "unused_sample_rejected":
            anchor = "if (!inst_double_finite(sp->spec[i]))"
            assert fixed.count(anchor) == 1
            return fixed.replace(anchor,
                                 "if (!inst_double_finite(sp->spec[i]) || sp->spec[i] <= 0.0)")
        if mutation == "divisor_sign_unchecked":
            assert fixed.count(divisor) == 1
            return fixed.replace(divisor, "if (!inst_double_finite(fw))\n")
        if mutation == "divisor_finite_unchecked":
            assert fixed.count(divisor) == 1
            return fixed.replace(divisor, "if (fw <= 0.0)\n")
        if mutation == "quotient_unchecked":
            assert fixed.count(quotient) == 1
            return fixed.replace(quotient, "")
        if mutation == "xyz_unchecked":
            assert fixed.count(XYZ_CHECK) == 1
            return fixed.replace(XYZ_CHECK, "", 1)
        if mutation == "xyz_check_clamped":
            unclamped = "icSigXYZData, icxNoClamp);"
            assert fixed.count(unclamped) == 1
            return fixed.replace(unclamped, "icSigXYZData, (icxClamping)clamp);")
        if mutation == "check_allocation_leaks":
            release = "\t\t\tconv->del(conv);\n\t\t\treturn 1;\n"
            assert fixed.count(release) == 1
            return fixed.replace(release, "\t\t\treturn 1;\n")
        # Validate inside the apply loop instead of before it: refusal after mutation.
        loop = CONVERTER_CHECK[CONVERTER_CHECK.index("\tfor (i = 0; i < nvals"):]
        assert fixed.count(loop) == 1
        fixed = fixed.replace(loop, "\n")
        old = ("\t\t\tgetval_xspec(filt, &fw, nm);\t\t/* Gets interp/extrap normalized value */\n"
               "\t\t\tvals[i].sp.spec[j] /= fw;\n")
        assert fixed.count(old) == 1
        return fixed.replace(old, old.replace("\t\t\tvals[i].sp.spec[j] /= fw;\n",
            "\t\t\tif (!inst_double_finite(fw) || fw <= 0.0\n"
            "\t\t\t || !inst_double_finite(vals[i].sp.spec[j] / fw)) {\n"
            "\t\t\t\tif (conv != NULL) conv->del(conv);\n"
            "\t\t\t\treturn 2;\n\t\t\t}\n\t\t\tvals[i].sp.spec[j] /= fw;\n"))

    source = mutate_production(proof.PATHS, regressed)
    with pytest.raises(AssertionError, match="repaired filter-values result differs"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
    assert json.loads((tmp_path / "regressed/report.json").read_text())["status"] == "incomplete"


def test_repaired_converter_refuses_an_applied_zero_before_dividing(
        argyll_archive, prepared_production, tmp_path, mutate_production):
    """The original divides by an applied zero (zero_inside, band 415), so only the repaired
    converter is built with float-divide-by-zero, which the shared sanitizer set does not
    enable. It stays silent; with the division moved ahead of the divisor check it reports it."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    flags = proof.sanitizers.FLOAT_DIVIDE_BY_ZERO
    report = proof.prove(argyll_archive, tmp_path / "checked", variants=("patched",),
                         converter_flags=flags, source=prepared_production)
    assert report["status"] == "passed" and set(report["results"]) == {"patched"}
    zero = next(r for r in report["results"]["patched"]["converter"]
                if r["case"] == "zero_inside")
    assert zero["rc"] == 2 and not zero["changed"]
    checked = ("\t\t\tif (!inst_double_finite(fw) || fw <= 0.0)\n\t\t\t\treturn 2;\n"
               "\t\t\tquotient = vals[i].sp.spec[j] / fw;\n")
    divided_first = ("\t\t\tquotient = vals[i].sp.spec[j] / fw;\n"
                     "\t\t\tif (!inst_double_finite(fw) || fw <= 0.0)\n\t\t\t\treturn 2;\n")

    def regressed(text, path):
        fixed = text
        if path != "spectro/inst.c":
            return fixed
        assert fixed.count(checked) == 1
        return fixed.replace(checked, divided_first)

    source = mutate_production(proof.PATHS, regressed)
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in patched/converter"):
        proof.prove(argyll_archive, tmp_path / "divided-first", variants=("patched",),
                    converter_flags=flags, source=source)


@pytest.mark.parametrize("fault", ["duplicate", "missing", "unknown"])
@pytest.mark.parametrize("kind", ["converter", "setter"])
def test_observation_census_requires_each_case_once(fault, kind, monkeypatch):
    # Numerical results cannot rescue an incomplete or duplicated observation set.
    conv = [{"case": name} for name in proof.CONV_REFUSE | proof.CONV_ACCEPT
            | proof.CONV_ALLOC_FAIL]
    sett = [{"driver": Path(path).stem, "case": case} for path in proof.SETTERS
            for case in proof.SET_DISABLE | proof.SET_VALID | proof.SET_INVALID]
    rows = conv if kind == "converter" else sett
    if fault == "duplicate":
        rows.append(dict(rows[0]))
    elif fault == "missing":
        rows.pop()
    else:
        rows[-1]["case"] = "unknown"
    # Reach the setter census with valid converter observations from an independent
    # acceptance fixture; every converter below returns the unchanged unit result.
    if kind == "setter":
        monkeypatch.setattr(proof, "CONV_ACCEPT", {"unit"})
        monkeypatch.setattr(proof, "CONV_REFUSE", set())
        monkeypatch.setattr(proof, "CONV_ALLOC_FAIL", set())
        conv = [{"case": "unit", "rc": 0, "all_finite": 1, "xyz_finite": 1,
                 "band_380": 1.0, "live": 0, "allocations": 1, "releases": 1}]
    with pytest.raises(AssertionError, match="census"):
        proof._validate(conv, sett, repaired=True, consts={})
