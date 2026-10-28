"""Common conversion allocation failure and its binding to every native build."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from native_argyll import conversion_allocation as repair  # noqa: E402


def test_actual_helpers_refuse_allocation_failure_and_keep_success_behavior(
        argyll_archive, prepared_production, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound proof")
    output = tmp_path / "proof"
    result = repair.prove(argyll_archive, output, source=prepared_production, sanitize=True)
    assert result["status"] == "passed" and result["sanitize"] is True
    assert set(result["results"]) == {
        "normal", "sanitized", "baseline-xrga-oom", "baseline-filter-oom"}
    # Every call, declaration and definition is counted in the whole source tree.
    expected = {path: {"ipatch_convert_xrga": xrga, "ipatch_convert_custom_filter": filtered}
                for path, xrga, filtered in (
                    ("spectro/xrga.c", 1, 0), ("spectro/xrga.h", 1, 0),
                    ("spectro/inst.c", 0, 1), ("spectro/inst.h", 0, 1),
                    ("spectro/dtp20.c", 3, 1), ("spectro/dtp22.c", 1, 1),
                    ("spectro/dtp41.c", 2, 1), ("spectro/ss.c", 2, 1),
                    ("spectro/i1pro_imp.c", 1, 1), ("spectro/i1pro3_imp.c", 1, 1),
                    ("spectro/munki_imp.c", 1, 1),
                )}
    assert result["reference_inventory"] == result["production_reference_inventory"] == expected
    for name in ("normal", "sanitized"):
        assert result["results"][name]["failures"] == 0
        assert result["results"][name]["callers"] == 18
        assert result["results"][name]["checks"] == 843
    assert result["patched_sha256"] == {
        path: hashlib.sha256((prepared_production / path).read_bytes()).hexdigest()
        for path in repair.PATHS
    }
    for case in ("baseline-xrga-oom", "baseline-filter-oom"):
        assert result["results"][case]["returncode"] != 0
        assert result["results"][case]["diagnostic"] is True
    assert result == json.loads((output / "report.json").read_text())


def test_unknown_archive_refused_before_proof_output(tmp_path):
    archive = tmp_path / "wrong.zip"
    archive.write_bytes(b"not the pinned source")
    output = tmp_path / "proof"
    with pytest.raises(ValueError):
        repair.prove(archive, output)
    assert not output.exists()


def test_added_unchecked_caller_cannot_escape_the_complete_census(prepared_production):
    text = (prepared_production / "spectro/dtp20.c").read_text()
    text += "\nvoid unexpected_caller(ipatch *v) {\n"
    text += "    ipatch_convert_xrga(v, 1, xcalstd_nonpol, xcalstd_xrdi, xcalstd_xrga, 1);\n}\n"
    with pytest.raises(ValueError, match="Unexpected helper-reference census in spectro/dtp20.c"):
        repair._census(text, "spectro/dtp20.c")


def test_new_driver_file_cannot_bypass_the_caller_inventory(
    argyll_archive, mutate_production, tmp_path
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound proof")
    # Obtain an owned tree before adding the independent, newly named caller.
    source = mutate_production(("spectro/inst.c",),
                               lambda text, _: text + "\n/* Inventory input. */\n")
    (source / "spectro/extra_driver.c").write_text(
        "void extra(ipatch *v) { ipatch_convert_xrga(v, 1, "
        "xcalstd_nonpol, xcalstd_xrdi, xcalstd_xrga, 1); }\n")
    output = tmp_path / "proof"
    with pytest.raises(ValueError,
                       match="Unexpected helper-reference census in spectro/extra_driver.c"):
        repair.prove(argyll_archive, output, source=source)
    assert not output.exists()


def test_reflective_proof_uses_current_source_and_accepts_a_harmless_comment(
    argyll_archive, prepared_production, mutate_production, tmp_path
):
    from native_argyll import legacy_reflective_calibration

    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound proof")
    source = prepared_production
    result = legacy_reflective_calibration.prove(
        argyll_archive, source, tmp_path / "reflective", compiler="cc")
    implementation = source / "spectro/i1pro_imp.c"
    assert result["status"] == "passed" and result["case_count"] == 10
    assert result["compiled_source_sha256"] == hashlib.sha256(
        implementation.read_bytes()).hexdigest()
    commented_source = mutate_production(("spectro/i1pro_imp.c",),
                                         lambda text, _: text + "\n/* Harmless comment. */\n")
    commented = legacy_reflective_calibration.prove(
        argyll_archive, commented_source, tmp_path / "commented", compiler="cc")
    assert commented["status"] == "passed" and commented["case_count"] == 10
    assert commented["compiled_source_sha256"] == hashlib.sha256(
        (commented_source / "spectro/i1pro_imp.c").read_bytes()).hexdigest()
    assert commented["compiled_source_sha256"] != result["compiled_source_sha256"]
    assert commented["fragment_sha256"] == result["fragment_sha256"]
    assert commented["results"] == result["results"]
