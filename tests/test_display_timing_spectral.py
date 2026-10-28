"""Actual Argyll spectral delay methods, with simulated samples and no transport."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import display_timing_spectral as spectral  # noqa: E402
from native_argyll.proof_inputs import before_source, fixture_manifest  # noqa: E402
from native_argyll.spyder_calibration_slots import _function, digest  # noqa: E402


@pytest.fixture(scope="module")
def predecessors(argyll_archive):
    return {path: before_source("display_timing_spectral", path, argyll_archive)
            for path in spectral.SOURCES}


@pytest.fixture(scope="module")
def production(argyll_archive, tmp_path_factory):
    source, _ = native.prepare(
        argyll_archive, tmp_path_factory.mktemp("spectral-timing") / "build",
        contribution=native.NATIVE / "contributions/cr_pr")
    return source


def test_predecessors_are_authenticated_independently_of_production(predecessors, production):
    declared = fixture_manifest()["proofs"]["display_timing_spectral"]
    assert set(declared) == set(predecessors) == set(spectral.SOURCES)
    for path, (name, _) in spectral.SOURCES.items():
        before = predecessors[path]
        after = (production / path).read_text()
        assert digest(before.encode()) == declared[path]["sha256"]
        assert _function(before, name) != _function(after, name)
        spectral._require_context(before, path)
        spectral._require_context(after, path)


def test_actual_methods_refuse_nonfinite_values_and_release_all_allocations(
        predecessors, production, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    # An unrelated production edit must not require a historical Python transform.
    path = production / "spectro/i1pro3_imp.c"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n/* Independent production comment. */\n")
        report = spectral.prove(predecessors, tmp_path / "proof", source=production, sanitize=True)
        assert report["compiled_source_sha256"]["spectro/i1pro3_imp.c"] == digest(path.read_bytes())
    finally:
        path.write_bytes(original)
    assert report["status"] == "passed"
    assert not report["hardware_tested"] and not report["instrument_runtime_linked"]
    for variants in report["results"].values():
        old, new = variants["baseline"], variants["repaired"]
        assert old["control"] == new["control"]
        assert old["overflow"][0] == 0 and new["overflow"][0] == 0x41
        assert old["missing_stamp"][4] == 0 and new["missing_stamp"][4] == 1
        assert old["alloc_fail"][4] == 0 and new["alloc_fail"][4] == 1
        assert "nan_clock" not in old and new["nan_clock"][0] == 0x71
        assert "large_stamp" not in old and new["large_stamp"][0] == 0x71
        assert "reaction_only" not in old
        assert new["reaction_only"] == [0x71, -88, 0, 1, 1, 1, 1, 1]
        for case in ("inttime_zero", "inttime_nan", "inttime_large"):
            assert case not in old
            assert new[case] == [0x71, -88, 0, 0, 0, 0, 0, 1]
        # The read writes back the interval it used; a bad one is refused after the read.
        for case in ("readback_zero", "readback_inf"):
            assert case not in old
            assert new[case] == [0x71, -88, 0, 1, 1, 1, 1, 1]
        # Pristine returns inst_internal_error from the driver-level method;
        # the wrapper's inst_imask mask turns it into inst_ok.
        assert old["missing_stamp"][0] == 0x060000 and old["missing_stamp"][7] == 0
        # Every repaired refusal is reported to the caller as a failure.
        assert all(new[case][7] == 1 for case in new if case != "control")
        assert new["control"][7] == 0
        assert old["read_error"] == new["read_error"]
        assert old["flat"] == new["flat"]
    with pytest.raises(FileExistsError):
        spectral.prove(predecessors, tmp_path / "proof", source=production)


def test_extracted_duration_is_bound_to_each_driver(production, predecessors, tmp_path):
    for path in spectral.SOURCES:
        target = production / path
        original = target.read_text()
        changed = original.replace("#define NDMXTIME 2.0", "#define NDMXTIME 3.0")
        assert changed != original
        try:
            target.write_text(changed)
            with pytest.raises(ValueError, match="duration"):
                spectral.prove(predecessors, tmp_path / "proof", "unused", source=production)
        finally:
            target.write_text(original)
    assert not (tmp_path / "proof").exists()


def test_proof_refuses_modified_missing_or_extra_predecessors(predecessors, production, tmp_path):
    for path in spectral.SOURCES:
        changed = {**predecessors, path: predecessors[path] + "\n"}
        with pytest.raises(ValueError, match="predecessor differs"):
            spectral.prove(changed, tmp_path / "proof", "unused", source=production)
        missing = {name: text for name, text in predecessors.items() if name != path}
        with pytest.raises(ValueError, match="predecessor paths differ"):
            spectral.prove(missing, tmp_path / "proof", "unused", source=production)
    with pytest.raises(ValueError, match="predecessor paths differ"):
        spectral.prove({**predecessors, "spectro/extra.c": ""}, tmp_path / "proof",
                       "unused", source=production)
    assert not (tmp_path / "proof").exists()


@pytest.mark.parametrize("case, removed, expected", [
    ("overflow", "\t\tif (!(samp[i].tot >= -DBL_MAX && samp[i].tot <= DBL_MAX))\n"
     "\t\t\tgoto invalid_samples;\n", 0),
    ("alloc_fail", "\t\tfree_dmatrix(multimeas, 0, nummeas-1, 0, m->nwav[m->highres]-1);\n"
     "\t\treturn I1PRO_INT_MALLOC;\n", 0x63),
    ("missing_stamp", "\t\tfree_dmatrix(multimeas, 0, nummeas-1, 0, m->nwav[m->highres]-1);\n"
     "\t\tfree(samp);\n\t\treturn I1PRO_INTERNAL_ERROR; \n", 0x71),
    # Guard-removal regression, retargeted to the current read-back case.
    ("readback_zero", "\n\t   && inttime > 0.0 && inttime <= DBL_MAX", 0x41),
])
def test_load_bearing_guards(production, tmp_path, case, removed, expected):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    path = "spectro/i1pro_imp.c"
    name, _ = spectral.SOURCES[path]
    repaired = _function((production / path).read_text(), name)
    assert repaired.count(removed) == 1
    if case == "alloc_fail":
        replacement = "\t\treturn I1PRO_INT_MALLOC;\n"
    elif case == "missing_stamp":
        replacement = "\t\treturn I1PRO_INTERNAL_ERROR; \n"
    else:
        replacement = ""
    (tmp_path / "method.inc").write_text(repaired.replace(removed, replacement))
    command = ["cc", "-std=gnu89", "-O1", f"-I{tmp_path}",
               '-DMETHOD_INCLUDE="method.inc"', "-DTEST_I1PRO",
               str(ROOT / "tools/native_argyll/display_timing_spectral_probe.c"),
               "-lm", "-o", str(tmp_path / "probe")]
    built = subprocess.run(command, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(tmp_path / "probe"), case], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    code, _, _, made, freed, allocs, releases, _ = map(int, ran.stdout.split())
    assert code == expected
    if case in ("alloc_fail", "missing_stamp"):
        assert made != freed or allocs != releases


def test_an_inst_code_from_a_driver_method_is_caught_as_reported_success(
        predecessors, production, tmp_path):
    """The regression this correction exists for: returning inst_internal_error
    from a driver-level method. The wrapper masks it to inst_ok, and the proof
    must fail because the caller would see success, not because of a crash."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    path = production / "spectro/i1pro_imp.c"
    original = path.read_text()
    method = _function(original, spectral.SOURCES["spectro/i1pro_imp.c"][0])
    regressed = method.replace("I1PRO_INTERNAL_ERROR", "inst_internal_error")
    assert regressed != method
    try:
        path.write_text(original.replace(method, regressed, 1))
        with pytest.raises(AssertionError, match="Spectral timing result drift: i1pro") as raised:
            spectral.prove(predecessors, tmp_path / "proof", source=production, sanitize=True)
    finally:
        path.write_text(original)
    # 0x060000 with a cleared reported flag: the refusal reaches callers as success.
    assert "[393216, -88, 0, 1, 1, 1, 1, 0]" in str(raised.value)
