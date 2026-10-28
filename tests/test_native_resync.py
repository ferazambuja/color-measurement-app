"""Frozen calibration failures checked against current production refusal/cleanup.

Scripted helper probes establish changed branch behavior; they do not operate
instruments, exercise calibration-cache I/O or validate optical calibration.
"""

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import native_resync_proof as proof  # noqa: E402
from native_argyll.proof_inputs import fixture_manifest  # noqa: E402


@pytest.fixture(scope="module")
def composition(argyll_archive, tmp_path_factory):
    output = tmp_path_factory.mktemp("resync-composition") / "build"
    source, report = native.prepare_with_proof_inputs(argyll_archive, output)
    stage = report["native_resync_repair"]
    before = {p: (output / stage["inputs"] / p).read_text() for p in stage["before_sha256"]}
    return source, output, report, before


def _replace_once(text: str, old: str, new: str) -> str:
    """Mutate one actual source anchor, refusing a missing or ambiguous target."""
    assert text.count(old) == 1, old
    assert old != new
    return text.replace(old, new, 1)


def test_frozen_baselines_match_manifest_and_conversion_order_is_preserved(composition):
    source, _, _, before = composition
    expected = fixture_manifest()["proofs"]["native_resync"]
    assert set(before) == set(expected)
    assert {path: native.digest(text.encode()) for path, text in before.items()} == {
        path: entry["sha256"] for path, entry in expected.items()
    }
    # The broad source keeps its moved spectral clearing after the same final
    # finite guard. An anchored edit must preserve this exact conversion order.
    i1pro = (source / "spectro/i1pro_imp.c").read_text()
    filter_end = ("\t/* Apply custom filter compensation */\n"
                  "\tif (m->custfilt_en &&\n"
                  "\t    ipatch_convert_custom_filter(vals, nvals, &m->custfilt, clamp) != 0)\n"
                  "\t\treturn I1PRO_INT_CIECONVFAIL;\n\n")
    final_guard = ("\tfor (i = 0; i < nvals; i++)\n"
                   "\t\tif (!cm_i1pro_patch_finite(&vals[i]))\n"
                   "\t\t\treturn I1PRO_RD_READINCONS;\n\n")
    moved_clear = ("\t/* Don't return spectral if not asked for. "
                   "CM: after the conversions above, */\n")
    assert i1pro.count(filter_end + final_guard + moved_clear) == 1


def test_harmless_production_comment_is_accepted_and_recorded(composition, tmp_path):
    source, _, _, before = composition
    path = "spectro/inst.c"
    original = (source / path).read_text()
    commented = original + "\n/* Harmless production comment. */\n"
    try:
        (source / path).write_bytes(commented.encode())
        result = proof.prove(source, before, tmp_path / "comment", groups=("custom-filter",))
        assert result["status"] == "passed"
        assert result["compiled_source_sha256"][path] == native.digest(commented.encode())
        assert result["compiled_source_sha256"][path] != native.digest(original.encode())
        assert result["before_sha256"] == {
            name: native.digest(text.encode()) for name, text in before.items()
        }
    finally:
        (source / path).write_bytes(original.encode())


@pytest.mark.parametrize("mutation", ["missing", "extra", "changed"])
def test_frozen_baseline_mutation_is_refused_before_compilation(composition, tmp_path, mutation):
    source, _, _, before = composition
    changed = dict(before)
    path = "spectro/inst.c"
    if mutation == "missing":
        del changed[path]
    elif mutation == "extra":
        changed["spectro/unexpected.c"] = "/* Not a frozen input. */\n"
    else:
        changed[path] += "\n/* Changed frozen failure fixture. */\n"
    output = tmp_path / mutation
    with pytest.raises(ValueError, match="Native resync frozen baseline"):
        proof.prove(source, changed, output, groups=("custom-filter",))
    assert not output.exists()


def test_composed_custom_filter_matches_the_short_spectrum_evaluator(composition, tmp_path):
    source, _, _, before = composition
    result = proof.prove(source, before, tmp_path / "short-filter", sanitize=True,
                         groups=("custom-filter",))
    assert result["status"] == "passed"
    assert "4 failures" in result["results"]["baseline/custom-filter"]["stdout"]
    assert "0 failures" in result["results"]["repaired/custom-filter"]["stdout"]


@pytest.mark.parametrize("family", ["i1pro", "i1pro3", "munki"])
def test_converter_refuses_nonfinite_publication_in_every_patch(composition, tmp_path, family):
    source, _, _, before = composition
    group = "converter-" + family
    result = proof.prove(source, before, tmp_path / group, sanitize=True, groups=(group,))
    assert result["status"] == "passed"
    assert "12 failures" in result["results"]["baseline/" + group]["stdout"]
    assert "0 failures" in result["results"]["repaired/" + group]["stdout"]


def test_munki_scaled_publication_preserves_signed_and_zero_controls(composition, tmp_path):
    source, _, _, before = composition
    result = proof.prove(source, before, tmp_path / "scaled-munki", sanitize=True,
                         groups=("scaled-munki",))
    assert result["status"] == "passed"
    assert "4 failures" in result["results"]["baseline/scaled-munki"]["stdout"]
    assert "0 failures" in result["results"]["repaired/scaled-munki"]["stdout"]


def test_scaled_i1pro_output_refuses_overflow_and_preserves_finite_controls(composition, tmp_path):
    source, _, _, before = composition
    result = proof.prove(source, before, tmp_path / "output", sanitize=True, groups=("output",))
    assert result["status"] == "passed"
    assert set(result["results"]) == {
        f"{stage}/output-{family}-{case}"
        for stage in ("baseline", "repaired")
        for family, cases in (("i1pro", ("equal", "scale_flash", "scale_spot")),
                              ("i1pro3", ("sat10", "scale_flash")))
        for case in cases
    }
    for name, row in result["results"].items():
        output = json.loads(row["stdout"])
        if "/output-i1pro-" in name:
            assert output["live_matrices"] == output["live_vectors"] == 0
        else:
            assert output["live_buffers"] == output["live_vectors"] == 0
            assert output["packed_aux_copy_attempts"] == 0


def test_debug_curves_preserve_normal_arithmetic_and_show_adjusted_black(composition, tmp_path):
    source, _, _, before = composition
    result = proof.prove(source, before, tmp_path / "black", sanitize=True, groups=("black-debug",))
    for case in ("neutral", "drift"):
        baseline = json.loads(result["results"]["baseline/black-default-" + case]["stdout"])
        ordinary = json.loads(result["results"]["repaired/black-default-" + case]["stdout"])
        plotted = json.loads(result["results"]["repaired/black-plot-" + case]["stdout"])
        assert baseline == ordinary
        assert baseline["plot_calls"] == 0 and plotted["plot_calls"] == 2
        assert plotted["adjusted_errors"] == plotted["failures"] == 0
        assert (baseline["first"], baseline["last"]) == (plotted["first"], plotted["last"])


def test_complete_debug_predicates_compile_actual_consumed_units(composition, tmp_path):
    source, _, _, before = composition
    result = proof.prove(source, before, tmp_path / "plot", groups=("plot-debug",))
    assert result["status"] == "passed"
    objects = [row for row in result["results"].values() if "compile_returncode" in row]
    assert len(objects) == 36 and all(row["executed"] is False for row in objects)
    for selector in proof.PLOT_SELECTORS["spectro/i1pro3_imp.c"]:
        baseline = result["results"]["baseline/i1pro3_imp-" + selector]
        repaired = result["results"]["repaired/i1pro3_imp-" + selector]
        assert baseline["compile_returncode"] != 0 and repaired["compile_returncode"] == 0
        assert baseline["enabled_definitions"] == (2 if selector == "PLOT_FILLINS" else 1)
        assert repaired["enabled_definitions"] == 1
        assert baseline["active_plot_calls"] == repaired["active_plot_calls"]


@pytest.mark.parametrize("selector", ["PLOT_FILLINS", "UPSAMPLE_PLOT", "DEBUG_UVFILT"])
def test_plot_activation_refuses_restored_local_reset(composition, selector):
    path = "spectro/i1pro3_imp.c"
    text = (composition[0] / path).read_text()
    pattern = rf"(?m)^#undef[ \t]+{selector}[^\n]*\n"
    # Moving the repaired top-level setting back below header selection keeps
    # the same count but cancels normal top-level activation of the later code.
    local = re.sub(pattern, "", text, count=1)
    header = local.index("# include <plot.h>\n") + len("# include <plot.h>\n")
    local = local[:header] + "#undef " + selector + "\n" + local[header:]
    with pytest.raises(ValueError, match="Debug selector activation changed"):
        proof._enable_plot_selector(local, path, selector, baseline=False)


def test_fillin_plot_fragments_release_both_owned_matrices(composition, tmp_path):
    source, _, _, before = composition
    result = proof.prove(source, before, tmp_path / "fillins", sanitize=True,
                         groups=("plot-fillins",))
    assert result["status"] == "passed"
    assert "2 failures" in result["results"]["baseline/plot-fillins"]["stdout"]
    assert "0 failures" in result["results"]["repaired/plot-fillins"]["stdout"]


@pytest.mark.parametrize("caller", ["i1pro3_strip_refl_meas", "i1pro3_pol_strip_refl_meas"])
def test_fillin_plot_cleanup_oracle_rejects_each_missing_release(
    composition, tmp_path, caller
):
    source, _, _, before = composition
    path = "spectro/i1pro3_imp.c"
    original = (source / path).read_text()
    function = proof._function(original, caller)
    mutated = _replace_once(function, "\t\tfree_dmatrix(plot, 0, 2, 0, npos-1);\n", "")
    try:
        (source / path).write_bytes(_replace_once(original, function, mutated).encode())
        with pytest.raises(
            AssertionError, match="Resync behavior differs at repaired/plot-fillins"
        ):
            proof.prove(source, before, tmp_path / caller, groups=("plot-fillins",))
        stdout = (tmp_path / caller / "repaired/plot-fillins/run.log").read_text()
        name = "polarized-strip-reflective" if "pol_strip" in caller else "strip-reflective"
        assert "FAIL " + name + " plot matrix retained\n" in stdout
    finally:
        (source / path).write_bytes(original.encode())


@pytest.mark.parametrize("family", ["i1pro", "i1pro3", "munki"])
def test_converter_second_patch_detects_first_patch_only_correction_guard(
    composition, tmp_path, family
):
    source, _, _, before = composition
    path = f"spectro/{family}_imp.c"
    original = (source / path).read_text()
    guard = ("\tfor (i = 0; i < nvals; i++)\n"
             f"\t\tif (!cm_{family}_patch_finite(&vals[i]))")
    try:
        (source / path).write_bytes(_replace_once(
            original, guard, guard.replace("i < nvals", "i < 1")).encode())
        with pytest.raises(AssertionError, match="Resync behavior differs at repaired/converter"):
            proof.prove(source, before, tmp_path / family, groups=("converter-" + family,))
        stdout = (tmp_path / family / "repaired" / ("converter-" + family) / "run.log").read_text()
        assert "FAIL post-xrga-overflow-second" in stdout
        assert "FAIL post-filter-overflow-second" in stdout
    finally:
        (source / path).write_bytes(original.encode())


def test_munki_second_scaled_row_detects_first_row_only_guard(composition, tmp_path):
    source, _, _, before = composition
    path = "spectro/munki_imp.c"
    original = (source / path).read_text()
    function = proof._function(original, "cm_munki_scaled_finite")
    mutated = _replace_once(function, "i < count", "i < 1")
    try:
        (source / path).write_bytes(_replace_once(original, function, mutated).encode())
        with pytest.raises(
            AssertionError, match="Resync behavior differs at repaired/scaled-munki"
        ):
            proof.prove(source, before, tmp_path / "scaled", groups=("scaled-munki",))
        stdout = (tmp_path / "scaled/repaired/scaled-munki/run.log").read_text()
        assert "FAIL caller2-overflow-second" in stdout
        assert "FAIL caller2a-overflow-second" in stdout
    finally:
        (source / path).write_bytes(original.encode())


def test_drifting_black_curve_refuses_original_dark_as_adjusted_curve(
    composition, tmp_path
):
    source, _, _, before = composition
    path = "spectro/i1pro_imp.c"
    original = (source / path).read_text()
    mutated = _replace_once(
        original, "do_plot6(xx, in, sub, asub, res,", "do_plot6(xx, in, sub, sub, res,")
    try:
        (source / path).write_bytes(mutated.encode())
        with pytest.raises(
            AssertionError, match="Resync behavior differs at repaired/black-plot-drift"
        ):
            proof.prove(source, before, tmp_path / "black-mutant", groups=("black-debug",))
        neutral = json.loads(
            (tmp_path / "black-mutant/repaired/black-plot-neutral/run.log").read_text()
        )
        drift = json.loads(
            (tmp_path / "black-mutant/repaired/black-plot-drift/run.log").read_text()
        )
        assert neutral["failures"] == 0
        assert drift["adjusted_errors"] == 4 and drift["failures"] == 8
    finally:
        (source / path).write_bytes(original.encode())


def test_sanitized_real_branches_fail_before_and_pass_after(composition, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _, _, before = composition
    # The package port's groups run in test_native_resync_package.
    groups = ("white", "hr", "state", "misc", "cleanup", "selectors", "followup")
    result = proof.prove(source, before, tmp_path / "proof", sanitize=True, groups=groups)
    assert result["status"] == "passed" and len(result["results"]) == 25
    for text in ("baseline", "repaired"):
        for family in ("i1pro", "i1pro3"):
            assert f"{text}/matrix-{family}" in result["results"]
    assert all(
        r["returncode"] == (1 if name.startswith("baseline/") and name != "baseline/selectors"
                            else 0)
        for name, r in result["results"].items()
    )
    # 94 white-path exits, plus two from the re-measured white after a scan
    # gain switch.
    assert "96 exits exercised" in result["results"]["repaired/white"]["stdout"]
    assert result["compiled_source_sha256"] == {
        p: native.digest((source / p).read_bytes()) for p in before
    }


@pytest.mark.parametrize("projection", ["repairs", "cr_pr"])
def test_selector_capacity_is_independent_of_constructor_text(
        composition, prepared_production, tmp_path, projection):
    source, _, _, before = composition
    if projection == "cr_pr":
        source = prepared_production
    path = source / "spectro/inst.c"
    original = path.read_text()
    body = proof._function(original, "inst_creat_disptype_list")
    commented = _replace_once(original, body, body.replace(
        "{", "{\n/* Harmless constructor comment. */", 1))
    try:
        path.write_bytes(commented.encode())
        result = proof.prove(source, before, tmp_path / "comment", groups=("selectors",))
        assert result["status"] == "passed"
        assert result["selector_contract"]["baseline"]["empty"] == 1
        assert result["selector_contract"]["repaired"] == {
            "empty": 1 if projection == "cr_pr" else 10,
            "registry_sha256": native.digest((source / "spectro/insttypes.h").read_bytes()),
        }
        assert all(row["returncode"] == 0 for row in result["results"].values())
        # A smaller production alphabet must fail, even though it compiles and
        # could otherwise be mistaken for a different supported projection.
        pool = "asels2" if projection == "cr_pr" else "asels"
        assignment = re.search(r'static char \*' + pool + r' = "([^"]+)";', commented)
        assert assignment is not None
        shortened = _replace_once(commented, assignment[0], assignment[0].replace(
            '"' + assignment[1] + '"', '"' + assignment[1][:-1] + '"'))
        path.write_bytes(shortened.encode())
        with pytest.raises(AssertionError, match="Resync behavior differs at repaired/selectors"):
            proof.prove(source, before, tmp_path / "capacity-mutant", groups=("selectors",))
    finally:
        path.write_bytes(original.encode())


# Each defect is compiled on the same authenticated inputs. Component selection
# keeps regressions focused instead of rebuilding all unrelated probes per fault.
MUTATIONS = [
    ("white", "spectro/i1pro_imp.c", "i1pro_imp_calibrate", "white_valid"),
    ("hr", "spectro/i1pro_imp.c", "i1pro_create_hr_calfactors", "hr_return"),
    ("hr", "spectro/i1pro_imp.c", "i1pro_compute_white_cal", "hr_zero_factor"),
    ("cleanup", "spectro/i1pro_imp.c", "i1pro_imp_calibrate", "wlraw"),
    ("cleanup", "spectro/i1pro3_imp.c", "i1pro3_imp_calibrate", "mode_restore"),
    ("misc", "spectro/i1pro_imp.c", "i1pro_extract_patches_multimeas", "curve_points"),
    ("misc", "spectro/i1pro3_imp.c", "i1pro3_locate_patches", "curve_object"),
    ("misc", "spectro/i1pro3_imp.c", "i1pro3_imp_calibrate", "exposure"),
    ("misc", "spectro/munki_imp.c", "munki_ledtemp_whitemeasure", "quiet_debug"),
    ("state", "spectro/kleink10.c", "k10_calibrate", "klein_mask"),
    ("state", "spectro/spydX.c", "spydX_calibrate", "x_mask"),
    ("state", "spectro/spydX2.c", "spydX2_calibrate", "x2_mask"),
    ("state", "spectro/spyd2.c", "spyd2_calibrate", "unsupported"),
    ("state", "spectro/spyd2.c", "spyd2_set_refr_rate", "refresh_valid"),
    ("state", "spectro/dtp92.c", "dtp92_get_refr_rate", "stored_output"),
    ("selectors", "spectro/inst.c", "inst_creat_disptype_list", "primary"),
    ("selectors", "spectro/inst.c", "inst_creat_disptype_list", "suffix"),
    ("selectors", "spectro/inst.c", "inst_creat_disptype_list", "exhaustion"),
    ("followup", "spectro/i1pro3_imp.c", "i1pro3_imp_calibrate", "pro3_ref_valid"),
    ("followup", "spectro/i1pro3_imp.c", "i1pro3_imp_calibrate", "pro3_trans_valid"),
    ("followup", "spectro/munki_imp.c", "munki_imp_calibrate", "munki_white_valid"),
    ("followup", "spectro/i1pro_imp.c", "i1pro_restore_refspot_cal", "restore_finite"),
    ("followup", "spectro/i1pro_imp.c", "i1pro_restore_refspot_cal", "restore_max"),
    ("followup", "spectro/i1pro_imp.c", "i1pro_restore_refspot_cal", "restore_order"),
    ("followup", "spectro/i1pro_imp.c", "i1pro_imp_init", "device_max"),
    ("followup", "spectro/i1pro.c", "i1pro_interp_error", "pro_filter_error"),
    ("followup", "spectro/i1pro3.c", "i1pro3_interp_error", "pro3_filter_error"),
    ("followup", "spectro/munki.c", "munki_interp_error", "munki_filter_error"),
    ("custom-filter", "spectro/inst.c", "inst_valid_custom_filter", "short_filter"),
    ("converter-i1pro", "spectro/i1pro_imp.c", "i1pro_conv2XYZ", "pro_converter_emissive"),
    ("converter-i1pro", "spectro/i1pro_imp.c", "i1pro_conv2XYZ", "pro_converter_reflectance"),
    ("converter-i1pro", "spectro/i1pro_imp.c", "i1pro_conv2XYZ", "pro_converter_integrated"),
    ("converter-i1pro", "spectro/i1pro_imp.c", "i1pro_conv2XYZ", "pro_converter_corrected"),
    ("converter-i1pro3", "spectro/i1pro3_imp.c", "i1pro3_conv2XYZ", "pro3_converter_emissive"),
    ("converter-i1pro3", "spectro/i1pro3_imp.c", "i1pro3_conv2XYZ", "pro3_converter_reflectance"),
    ("converter-i1pro3", "spectro/i1pro3_imp.c", "i1pro3_conv2XYZ", "pro3_converter_integrated"),
    ("converter-i1pro3", "spectro/i1pro3_imp.c", "i1pro3_conv2XYZ", "pro3_converter_corrected"),
    ("converter-munki", "spectro/munki_imp.c", "munki_conv2XYZ", "munki_converter_emissive"),
    ("converter-munki", "spectro/munki_imp.c", "munki_conv2XYZ", "munki_converter_reflectance"),
    ("converter-munki", "spectro/munki_imp.c", "munki_conv2XYZ", "munki_converter_integrated"),
    ("converter-munki", "spectro/munki_imp.c", "munki_conv2XYZ", "munki_converter_corrected"),
]


@pytest.mark.parametrize(
    ("group", "path", "function", "name"), MUTATIONS, ids=[m[3] for m in MUTATIONS]
)
def test_each_changed_behavior_has_a_semantic_regression(
    composition, tmp_path, group, path, function, name
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _, _, before = composition
    text = (source / path).read_text()
    original = proof._function(text, function)
    if name == "white_valid":
        changed = _replace_once(original,
            "\t\t\ts->cal_valid = 0;\t\t/* White data and factors are being replaced. */\n", ""
        )
    elif name == "hr_return":
        changed = _replace_once(original, "\treturn rv;\n}\n", "\treturn ev;\n}\n")
    elif name == "hr_zero_factor":
        changed = _replace_once(original, "new_emis[i] <= 0.0", "new_emis[i] < 0.0")
    elif name == "wlraw":
        block = proof._block(original, "\t\t\tif ((ev = i1pro2_wl_measure(")
        changed = _replace_once(original, block, _replace_once(
            block, "\t\t\t\tfree_dvector(wlraw, -1, m->nraw-1);\n", ""))
    elif name == "mode_restore":
        block = proof._block(original, "\t\t\tif ((ev = i1pro3_wl_measure(")
        changed = _replace_once(original, block, _replace_once(
            block, "\t\t\t\tm->mmode = mmode;\t\t\t/* Restore actual mode */\n", ""))
    elif name in ("curve_points", "curve_object"):
        block = proof._block(original, "if (points == NULL || pcurve == NULL)")
        release = ("\t\t\tfree(points);\n" if name == "curve_points" else
                   "\t\t\tif (pcurve != NULL)\n\t\t\t\tpcurve->del(pcurve);\n")
        changed = _replace_once(original, block, _replace_once(block, release, ""))
    elif name == "exposure":
        changed = _replace_once(original,
            "\t\t\t\tif (maxval <= 0.0)\t/* Match the adaptive exposure floor. */\n"
            "\t\t\t\t\tmaxval = 1.0;\n",
            "",
        )
    elif name == "quiet_debug":
        changed = _replace_once(original, "if (p->log->debug >= 3) {", "{")
    elif name.endswith("mask"):
        changed = _replace_once(original,
            "*calt &= ~inst_calt_emis_offset;", "*calt &= ~inst_calc_man_em_dark;"
        )
    elif name == "unsupported":
        changed = _replace_once(original,
            "\t/* See if it's a calibration we understand */\n"
            "\tif (*calt & ~available & inst_calt_all_mask) {\n"
            "\t\treturn inst_unsupported;\n\t}\n",
            "",
        )
    elif name == "refresh_valid":
        changed = _replace_once(original, "\t\tp->refrvalid = 0;\n", "")
    elif name == "stored_output":
        changed = _replace_once(original, "\tif (ref_rate != NULL)\n\t\t*ref_rate = refrate;\n", "")
    elif name == "primary":
        changed = _replace_once(original,
            "\t\tif (list[i].sel[0] != '\\000')\n"
            "\t\t\tcontinue;\t/* Preserve selectors assigned in the earlier passes */\n",
            "",
        )
    elif name == "suffix":
        changed = _replace_once(original, "\t\t\tlist[i].sel[1] = '\\000';\n", "")
    elif name == "exhaustion":
        changed = _replace_once(original, "\t\t\t\tlist[i].sel[0] = '\\000';\n", "")
    elif name in ("pro3_ref_valid", "pro3_trans_valid"):
        kind = "initial reflective" if name == "pro3_ref_valid" else "transmission"
        log = f'\t\t\ta1logd(p->log,2,"\\nDoing {kind} white calibration\\n");\n'
        line = "\t\t\ts->cal_valid = 0;\t\t/* White calibration data will be overwritten. */\n"
        changed = _replace_once(original, log + line, log)
    elif name == "munki_white_valid":
        line = "\t\t\ts->cal_valid = 0;\t\t/* White calibration data will be overwritten. */\n\n"
        anchor = "\t\t\t/* Until we're done */\n"
        changed = _replace_once(original, line + anchor, anchor)
    elif name == "restore_finite":
        changed = _replace_once(original, "!isfinite(dp[0]) || ", "")
    elif name == "restore_max":
        changed = _replace_once(original, " || dp[0] > m->max_int_time", "")
    elif name == "restore_order":
        anchor = "\t/* Ignore unusable saved calibration, as if none was stored. */\n"
        changed = _replace_once(original, anchor, "\ts->inttime = dp[0];\n" + anchor)
    elif name == "device_max":
        block = proof._block(original,
            "\t\tif (!isfinite(m->max_int_time) || m->max_int_time < m->min_int_time)")
        changed = _replace_once(original, block, "")
    elif name == "short_filter":
        changed = _replace_once(original,
            "\tif (!inst_double_finite(spcg) || spcg <= 0.0)\n",
            "\tif (!inst_double_finite(spcg) || spcg <= 0.0 || (spcg >= 5.01 && sp->spec_n < 4))\n")
    elif "_converter_" in name:
        family = Path(path).stem.removesuffix("_imp")
        suffix = name.rsplit("_", 1)[1]
        guard = f"if (!cm_{family}_patch_finite(&vals[i]))"
        error = family.upper() + "_RD_READINCONS"
        if suffix in ("emissive", "reflectance"):
            scale = "1.0" if suffix == "emissive" else "100.0"
            anchor = f"\t\t\tvals[i].sp.norm = {scale};\n"
            check = (f"\t\t\t{guard} {{\n\t\t\t\tconv->del(conv);\n"
                     f"\t\t\t\treturn {error};\n\t\t\t}}\n")
            changed = _replace_once(original, anchor + check, anchor)
        elif suffix == "integrated":
            check = (f"\t\t{guard} {{\n\t\t\tconv->del(conv);\n"
                     f"\t\t\treturn {error};\n\t\t}}\n")
            changed = _replace_once(original, check, "")
        else:
            assert suffix == "corrected"
            check = (f"\tfor (i = 0; i < nvals; i++)\n\t\t{guard}\n"
                     f"\t\t\treturn {error};\n\n")
            changed = _replace_once(original, check, "")
    elif name.endswith("filter_error"):
        changed = _replace_once(original,
            'return "Spectral to CIE conversion or custom filter compensation failed";',
            'return "Creating spectral to CIE converted failed";')
    else:
        raise AssertionError(name)
    assert changed != original, "Mutation must alter the input"
    mutated = tmp_path / "source"
    support = (
        "spectro/inst.h",
        "spectro/insttypes.h",
        "spectro/i1pro_imp.h",
        "spectro/i1pro3_imp.h",
        "spectro/munki_imp.h",
        "spectro/dtp92.h",
        "numlib/numsup.c",
        "spectro/disptechs.h",
        "spectro/disptechs.c",
        "icc/icc_util.c",
        "xicc/xspect.h",
    )
    for filename in (*before, *support):
        (mutated / filename).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / filename, mutated / filename)
    (mutated / path).write_bytes(_replace_once(text, original, changed).encode())
    with pytest.raises(AssertionError, match="Resync behavior differs at repaired/"):
        proof.prove(mutated, before, tmp_path / "mutant", groups=(group,))
    assert json.loads((tmp_path / "mutant/report.json").read_text())["status"] == "incomplete"
    if name.startswith(("pro_converter_", "munki_converter_")):
        stdout = (tmp_path / "mutant/repaired" / group / "run.log").read_text()
        faults = {
            "emissive": ("nonfinite-input",),
            "reflectance": ("reflectance-scale-overflow",),
            "integrated": ("integration-overflow", "xyz-percent-overflow"),
            "corrected": ("post-xrga-overflow", "post-filter-overflow"),
        }[name.rsplit("_", 1)[1]]
        for fault in faults:
            assert "FAIL " + fault + "\n" in stdout
            assert "FAIL " + fault + "-second\n" in stdout


def test_actual_calibration_diagnostic_census_and_direct_limits_header(argyll_archive, composition):
    """The upstream census is independent of the repair's edited-path table."""
    import re

    source, _, _, _ = composition
    pointer_log = re.compile(r'a1logd\(p->log,4,"[^"\n]*calibrate: doing calt 0x%x\\n",calt\);')
    with zipfile.ZipFile(argyll_archive) as z:
        affected = {
            n.removeprefix("Argyll_V3.5.0/"): z.read(n).decode()
            for n in z.namelist()
            if n.startswith("Argyll_V3.5.0/" + "spectro/")
            and n.endswith(".c")
            and pointer_log.search(z.read(n).decode())
        }
    assert len(affected) == 14 and "spectro/huey.c" not in affected
    for path, old in affected.items():
        new = (source / path).read_text()
        assert pointer_log.search(old) and not pointer_log.search(new), path
        assert pointer_log.search(old).group().replace(",calt);", ",*calt);") in new, path
    for path in ("spectro/i1d3.c", "spectro/kleink10.c"):
        assert (source / path).read_text().count("#include <limits.h>") == 1


def test_native_resync_sanitizer_diagnostic_cannot_report_pass(composition, tmp_path, monkeypatch):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _, _, before = composition
    template = tmp_path / "harness"
    template.mkdir()
    name = "native_resync_white_probe.c"
    text = (proof.HERE / name).read_text()
    anchor = "int main(void) {"
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor + "\n{volatile int canary=2147483647; canary+=1;}\n")
    (template / name).write_text(text)
    monkeypatch.setattr(proof, "HERE", template)
    with pytest.raises(AssertionError, match="Sanitizer diagnostic"):
        proof.prove(source, before, tmp_path / "canary", sanitize=True, groups=("white",))
    assert json.loads((tmp_path / "canary/report.json").read_text())["status"] == "incomplete"


def test_only_the_hr_zero_build_sees_a_float_division_by_zero(composition, tmp_path, monkeypatch):
    """clang's `undefined` group excludes float division by zero; the repaired/hr-zero build
    adds that check from sanitizers.FLOAT_DIVIDE_BY_ZERO. A canary dividing by zero on every
    path must pass the plain sanitized hr builds and be refused at hr-zero, so a dropped flag
    at that call cannot turn the check into a second plain run (challenge 213)."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _, _, before = composition
    template = tmp_path / "harness"
    template.mkdir()
    for path in proof.HERE.glob("native_resync_*"):
        shutil.copyfile(path, template / path.name)
    name = "native_resync_hr_probe.c"
    text = (proof.HERE / name).read_text()
    anchor = '\tif (argc == 2 && !strcmp(argv[1], "fine-tune-zero")) {\n'
    assert text.count(anchor) == 1
    canary = "\t{ volatile double zero = 0.0, quotient = 1.0 / zero; (void)quotient; }\n"
    (template / name).write_text(text.replace(anchor, canary + anchor))
    monkeypatch.setattr(proof, "HERE", template)
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in repaired/hr-zero"):
        proof.prove(source, before, tmp_path / "canary", sanitize=True, groups=("hr",))
    assert json.loads((tmp_path / "canary/report.json").read_text())["status"] == "incomplete"
    # The plain sanitized builds ran the same division and reported nothing.
    assert "runtime error" not in (tmp_path / "canary/repaired/hr/run.log").read_text()
    assert "division by zero" in (tmp_path / "canary/repaired/hr-zero/run.log").read_text()
