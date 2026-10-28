"""Final composed shared cache reader; no instrument or complete cache loader."""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import native_resync_proof as proof  # noqa: E402


@pytest.fixture(scope="module")
def composed(argyll_archive, tmp_path_factory):
    output = tmp_path_factory.mktemp("calibration-string-composition") / "build"
    source, report = native.prepare_with_proof_inputs(argyll_archive, output)
    stage = report["native_resync_repair"]
    before = {p: (output / stage["inputs"] / p).read_text() for p in stage["before_sha256"]}
    return source, before


def consumer_guard(restore, name):
    guard = (
        "if (x.ef != 0\n"
        "\t\t || argyllversion != ARGYLL_VERSION\n"
        f"\t\t || ss != (sizeof({name}))\n"
        "\t\t || strcmp(serno, p->serno) != 0) {"
    )
    reader = "calf_rstrz2(&x, &serno);"
    assert restore.count(reader) == restore.count(guard) == 1
    start = restore.index(reader) + len(reader)
    end = restore.index(guard)
    assert start < end and not restore[start:end].strip()
    return guard


def test_final_reader_and_consumers_bind_their_authentic_predecessor(composed):
    source, before = composed
    path = "spectro/rspec.c"
    final = (source / path).read_text()
    assert (
        native.digest(before[path].encode())
        == "05adc19bb0df58a7e0d216e819958e301882677124661ee6a91d4048820825da"
    )
    assert (
        native.digest(final.encode())
        == "69cc54ecd38e5d645f924eb6a6f2cff7da3f3e6fa79c3d49bf75eed369e5d8f5"
    )
    # Each consumer already refuses a failed identifier read before comparing it.
    for name in ("spydX", "spydX2", "ex1"):
        restore = proof._function(
            (source / f"spectro/{name}.c").read_text(), f"{name}_restore_calibration"
        )
        consumer_guard(restore, name)


@pytest.mark.parametrize("name", ("spydX", "spydX2", "ex1"))
def test_caller_census_rejects_an_ignored_reader_error(composed, name):
    source, _ = composed
    restore = proof._function(
        (source / f"spectro/{name}.c").read_text(), f"{name}_restore_calibration"
    )
    guard = consumer_guard(restore, name)
    mutated = restore.replace(guard, guard.replace("x.ef != 0", "0"))
    assert mutated != restore, "Mutation must change the actual caller condition"
    with pytest.raises(AssertionError):
        consumer_guard(mutated, name)


def test_sanitized_reader_refuses_bad_identifiers_and_preserves_valid_records(composed, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, before = composed
    result = proof.prove(source, before, tmp_path / "proof", sanitize=True, groups=("calf",))
    assert result["status"] == "passed"
    assert set(result["results"]) == {"baseline/calf", "repaired/calf"}
    assert "FAIL zero: validity or explicit refusal" in result["results"]["baseline/calf"]["stdout"]
    assert (
        "FAIL negative: untrusted length must not enter fatal allocation"
        in result["results"]["baseline/calf"]["stdout"]
    )
    assert (
        "13 synthetic identifier cases; 0 failures" in result["results"]["repaired/calf"]["stdout"]
    )


@pytest.mark.parametrize("mutation", ("minimum", "maximum", "position", "end", "terminator"))
def test_reader_regressions_require_the_changed_guards(
    composed, tmp_path, mutate_production, mutation
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, before = composed
    changes = {
        "minimum": ("if (n < 1 || n > CALF_MAXSTRL)", "if (n == 0 || n > CALF_MAXSTRL)"),
        "maximum": ("if (n < 1 || n > CALF_MAXSTRL)", "if (n < 1)"),
        "position": ("fseek(x->fp, pos, SEEK_SET) != 0", "0"),
        "end": ("(long)n > end - pos", "0"),
        "terminator": ("dest[nbytes-1] != '\\0' || memchr(dest, '\\0', nbytes-1) != NULL", "0"),
    }
    old, new = changes[mutation]
    text = (source / "spectro/rspec.c").read_text()
    assert text.count(old) == 1
    mutated = mutate_production(
        ["spectro/rspec.c"], lambda current, path: current.replace(old, new)
    )
    with pytest.raises(AssertionError, match="Resync behavior differs at repaired/calf"):
        proof.prove(mutated, before, tmp_path / "proof", sanitize=True, groups=("calf",))
