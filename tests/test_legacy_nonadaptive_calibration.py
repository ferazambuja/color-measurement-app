"""Legacy calibration correction: authenticated source and inert methods only."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "legacy_nonadaptive_calibration",
    ROOT / "tools/native_argyll/legacy_nonadaptive_calibration.py",
)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)


@pytest.fixture(scope="module")
def production_source(argyll_archive, tmp_path_factory):
    from argyll_native_check import prepare

    source, _ = prepare(argyll_archive, tmp_path_factory.mktemp("legacy-calibration") / "build")
    return source


def test_untrusted_archive_is_refused_before_creating_output(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned source archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_source_bound_models_error_propagation_state_and_invariant_controls(
    argyll_archive, production_source, tmp_path,
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for wholly inert source proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output, source=production_source)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report["cache_file_io"] is report["new_mode_exposed"] is False
    assert len(report["results"]["baseline"]) == len(report["results"]["patched"]) == 36
    assert report["paired_case_count"] == 36
    assert report["patched_only_case_count"] == len(report["patched_only_results"]) == 4
    assert report["baseline_uninitialized_scale_cases_executed"] == 0
    assert {row["dtype"] for row in report["results"]["patched"].values()} == {21, 22}
    assert {
        (row["dtype"], row["prior_done"]) for row in report["patched_only_results"].values()
    } == {
        (21, 0),
        (21, 1),
        (22, 0),
        (22, 1),
    }
    for key, row in report["patched_only_results"].items():
        assert key not in report["results"]["baseline"]
        assert len(row["probes"]) == row["allocations"] == row["frees"] == 1
        assert row["return_code"] == report["constants"]["communications_error"]
        assert row["done"] == row["live_buffers"] == 0
        assert row["remaining"] == report["constants"]["integration_bit"]
        assert row["selection_date"] == (123 if row["prior_done"] else 0)
    assert json.loads((output / "report.json").read_text()) == report
    assert proof.digest((output / "i1pro_imp.original.c").read_bytes()) == proof.SOURCE_SHA256
    assert proof.digest((output / "i1pro_imp.patched.c").read_bytes()) == report["patched_sha256"]
    assert report["patched_sha256"] == proof.digest(
        (production_source / proof.SOURCE_PATH).read_bytes())
    for row in report["transmission_scan_results"]["patched"]:
        if row["case"].endswith(("saturated", "misread")):
            assert row["cal_valid"] == 0 and row["needed"] == row["remaining"] == 0x1000
            assert row["factor0"] == 100 and row["cache_hooks"] == 0
            assert row["calibration_date"] == 1234555
    for name, expected in report["generated_products_sha256"].items():
        assert proof.digest((output / name).read_bytes()) == expected
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=production_source)


def test_failed_refresh_cannot_keep_a_previously_valid_selection(
    argyll_archive, production_source, tmp_path
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for wholly inert source proof")
    original = (production_source / proof.SOURCE_PATH).read_text()
    invalidation = "\t\t\ts->done_dintsel = 0; /* A failed refresh must not retain validity. */\n"
    assert original.count(invalidation) == 1
    source = tmp_path / "source"
    driver = source / proof.SOURCE_PATH
    driver.parent.mkdir(parents=True)
    driver.write_text(original.replace(invalidation, "", 1))
    output = tmp_path / "proof"
    with pytest.raises(AssertionError, match="Proof verdict failed"):
        proof.prove(argyll_archive, output, source=source)
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed"
    for dtype in (21, 22):
        row = report["patched_only_results"][f"{dtype}:prior_valid_first_probe_error"]
        assert row["return_code"] == report["constants"]["communications_error"]
        assert row["done"] == 1


def test_missing_compiler_is_refused_before_output(argyll_archive, production_source, tmp_path):
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="C compiler unavailable"):
        proof.prove(argyll_archive, output, source=production_source,
                    compiler="color-measurement-compiler-does-not-exist")
    assert not output.exists()
