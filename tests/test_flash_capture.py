"""Source-bound flash regressions; no physical acquisition is executed."""

import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from conftest import assert_validator_survives_optimization
from native_argyll.proof_inputs import before_source

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "flash_capture", ROOT / "tools/native_argyll/flash_capture.py"
)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)

# Exact C anchors for mutations of the current production functions. No Python
# repair transform supplies either side of a behavioral comparison.
POSITION_CLEANUP = """\t\tif (p2m != NULL) {
\t\t\tdel_zebix_list(p2m, npos);
\t\t\tp2m = NULL;
\t\t}
"""
BOUNDARY_GUARD = """\tfor (k = nummeas > 1 ? nummeas - 2 : 0; k < nummeas; k++) {
\t\tfor (j = 0; j < m->nraw; j++) {
\t\t\tif (multimeas[k][j] >= thresh)
\t\t\t\treturn I1PRO3_RD_READINCONS;
\t\t}
\t}
"""
SATURATION_GUARD = """\t\t\tif (satthresh > 0.0) {
\t\t\t\tint i, j;
\t\t\t\tfor (i = 0; i < nmeasuered; i++) {
\t\t\t\t\tfor (j = 0; j < m->nraw; j++) {
\t\t\t\t\t\tif (m->nsen == m->nraw && (j == 0 || j == 127))
\t\t\t\t\t\t\tcontinue;
\t\t\t\t\t\tif (multimes[i][j] > satthresh) {
\t\t\t\t\t\t\tfree_dmatrix(absraw, 0, numpatches-1, -1, m->nraw-1);
\t\t\t\t\t\t\tfree_dmatrix(multimes, 0, nmeasuered-1, -1, m->nraw-1);
\t\t\t\t\t\t\treturn I1PRO_RD_SENSORSATURATED;
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
"""


def test_untrusted_source_or_archive_is_refused_before_writes(tmp_path):
    source = tmp_path / "untrusted.zip"
    source.write_bytes(b"not a pinned archive")
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(source, output)
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove_shared_endpoints(source, output)
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove_i1pro_saturation(source, output)
    assert not output.exists()


def test_source_proof_composed_refusals_equivalence_and_cleanup(
    argyll_archive, prepared_production, tmp_path
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output, source=prepared_production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report["baseline_sha256"] == proof.digest(
        before_source("flash_capture", proof.SOURCE_PATH, argyll_archive).encode())
    assert report["patched_sha256"] == proof.digest(
        (prepared_production / proof.SOURCE_PATH).read_bytes())
    assert len(report["results"]["baseline"]) == len(report["results"]["patched"]) == 27
    for case, field, bad_value in (
        ("last_only", "code", report["results"]["patched"]["last_only"]["ok_code"]),
        ("sat11", "code", report["results"]["patched"]["sat11"]["ok_code"]),
        ("sat11", "frees", 2),
        ("sat11", "linearized", 1),
        ("sat11_zebra", "zebra_frees", 0),
    ):
        changed = copy.deepcopy(report["results"])
        changed["patched"][case][field] = bad_value
        with pytest.raises(AssertionError, match=f"flash {case}:"):
            proof._validate(changed)
    # Keep baseline/patched equivalence so only the converter contract can refuse.
    changed = copy.deepcopy(report["results"])
    for label in ("baseline", "patched"):
        changed[label]["converter_with_aux"]["first"] = 0
    with pytest.raises(AssertionError, match="flash converter_with_aux: converter result differs"):
        proof._validate(changed)
    bad = copy.deepcopy(report["results"])
    bad["patched"]["interior"]["live_vectors"] = 1
    assert_validator_survives_optimization(proof, "_validate", report["results"], bad)
    assert json.loads((output / "report.json").read_text()) == report
    assert proof.digest((output / "i1pro3_imp.original.c").read_bytes()) == proof.SOURCE_SHA256
    assert proof.digest((output / "i1pro3_imp.patched.c").read_bytes()) == report["patched_sha256"]
    assert proof.digest((output / "flash-capture.patch").read_bytes()) == report["patch_sha256"]
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=prepared_production)


def test_sibling_endpoint_proof_preserves_interior_signed_data_and_failures(
    argyll_archive, prepared_production, tmp_path
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "shared-proof"
    report = proof.prove_shared_endpoints(argyll_archive, output, source=prepared_production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    assert set(report["patched_function_sha256"]) == set(proof.SHARED_FLASH_SIGNATURES)
    for path, signature in proof.SHARED_FLASH_SIGNATURES.items():
        family = Path(path).stem.removesuffix("_imp")
        original = (output / f"{family}_imp.original.c").read_text()
        patched = (output / f"{family}_imp.patched.c").read_text()
        assert original == before_source("flash_endpoints", path, argyll_archive)
        assert patched == (prepared_production / path).read_text(encoding="utf-8")
        assert proof.digest(patched.encode()) == report["patched_sha256"][path]
        assert (
            proof.digest(proof._function(patched, signature).encode())
            == (report["patched_function_sha256"][path])
        )
        assert len(report["results"][family]["baseline"]) == 12
        assert len(report["results"][family]["patched"]) == 12
        bad = copy.deepcopy(report["results"][family])
        bad["patched"]["interior"]["live_vectors"] = 1
        assert_validator_survives_optimization(
            proof, "_validate_shared_endpoints", report["results"][family], bad
        )
    with pytest.raises(FileExistsError):
        proof.prove_shared_endpoints(argyll_archive, output, source=prepared_production)


def test_zebra_cleanup_regression_is_seen_by_real_scan_proof(
    argyll_archive, tmp_path, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    def regressed(text, path):
        body = proof._function(text, proof.FUNCTIONS[3])
        assert body.count(POSITION_CLEANUP) == 1
        return text.replace(body, body.replace(POSITION_CLEANUP, ""))

    source = mutate_production([proof.SOURCE_PATH], regressed)
    with pytest.raises(AssertionError):
        proof.prove(argyll_archive, tmp_path / "missing-cleanup", source=source)


@pytest.mark.parametrize("family", ["i1pro", "munki"])
def test_each_sibling_boundary_refusal_is_seen_by_real_extractor_proof(
    argyll_archive, tmp_path, family, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    def regressed(text, path):
        body = proof._function(text, proof.SHARED_FLASH_SIGNATURES[path])
        guard = BOUNDARY_GUARD.replace("I1PRO3", family.upper())
        assert body.count(guard) == 1
        return text.replace(body, body.replace(guard, ""))

    source = mutate_production([f"spectro/{family}_imp.c"], regressed)
    with pytest.raises(AssertionError, match="patched endpoint not refused as inconsistent"):
        proof.prove_shared_endpoints(argyll_archive, tmp_path / "missing-refusal", source=source)


def test_i1pro_saturation_real_caller_preserves_native_policy_quantities_and_cleanup(
    argyll_archive, prepared_production, tmp_path
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "saturation-proof"
    report = proof.prove_i1pro_saturation(argyll_archive, output, source=prepared_production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    patched = (output / "i1pro_imp.patched.c").read_text()
    assert patched == (prepared_production / "spectro/i1pro_imp.c").read_text(encoding="utf-8")
    assert report["baseline_sha256"] == proof.digest(before_source(
        "i1pro_flash_saturation", "spectro/i1pro_imp.c", argyll_archive).encode())
    assert report["patched_sha256"] == proof.digest(patched.encode())
    assert set(report["patched_function_sha256"]) == set(proof.I1PRO_SATURATION_SIGNATURES)
    for signature in proof.I1PRO_SATURATION_SIGNATURES:
        assert report["patched_function_sha256"][signature] == proof.digest(
            proof._function(patched, signature).encode()
        )
    assert len(report["results"]["baseline"]) == len(report["results"]["patched"]) == 24
    bad = copy.deepcopy(report["results"])
    bad["patched"]["equal"]["live_matrices"] = 1
    assert_validator_survives_optimization(
        proof, "_validate_i1pro_saturation", report["results"], bad
    )
    with pytest.raises(FileExistsError):
        proof.prove_i1pro_saturation(argyll_archive, output, source=prepared_production)


def test_i1pro_saturation_regression_is_seen_through_actual_caller(
    argyll_archive, tmp_path, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    def regressed(text, path):
        body = proof._function(text, proof.I1PRO_SATURATION_SIGNATURES[0])
        assert body.count(SATURATION_GUARD) == 1
        return text.replace(body, body.replace(SATURATION_GUARD, ""))

    source = mutate_production(["spectro/i1pro_imp.c"], regressed)
    with pytest.raises(AssertionError, match="patched reading not refused as saturated"):
        proof.prove_i1pro_saturation(argyll_archive, tmp_path / "missing-saturation", source=source)


@pytest.mark.parametrize("mutation", ["channel-zero-skipped", "strict-threshold"])
def test_exact_threshold_in_first_channel_is_refused(
    argyll_archive, tmp_path, mutate_production, mutation
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    old = "j = 0;" if mutation == "channel-zero-skipped" else ">= thresh"
    new = "j = 1;" if mutation == "channel-zero-skipped" else "> thresh"
    assert BOUNDARY_GUARD.count(old) == 1

    def regressed(text, path):
        body = proof._function(text, proof.FUNCTIONS[0])
        assert body.count(BOUNDARY_GUARD) == 1
        return text.replace(body, body.replace(BOUNDARY_GUARD, BOUNDARY_GUARD.replace(old, new)))

    source = mutate_production([proof.SOURCE_PATH], regressed)
    with pytest.raises(AssertionError, match="flash threshold_channel0:"):
        proof.prove(argyll_archive, tmp_path / "boundary", source=source)
