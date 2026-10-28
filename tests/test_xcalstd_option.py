"""Every native XRGA option rejects invalid targets without changing state."""

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from native_argyll import xcalstd_option as proof  # noqa: E402

# Independent regression anchors retained from the authenticated repair evidence.
GUARD = (
    "\t\t/* The target is a conversion-table index, or native for no conversion. */\n"
    "\t\tif (standard != xcalstd_native && standard != xcalstd_xrdi\n"
    "\t\t && standard != xcalstd_gmdi && standard != xcalstd_xrga)\n"
    "\t\t\treturn inst_bad_parameter;\n"
    "\n"
)


@pytest.fixture(scope="module")
def originals(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as z:
        return {p: z.read(proof.PREFIX + p).decode() for p in proof.PATHS}


@pytest.mark.parametrize("sanitize", [False, True])
def test_actual_setter_and_getter_preserve_valid_requests_and_refuse_invalid_ones(
    argyll_archive, tmp_path, sanitize
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    result = proof.prove(argyll_archive, tmp_path / "proof", sanitize=sanitize)
    assert result["status"] == "passed"
    assert result["results"]["baseline"]["cases"] == 1176
    assert result["results"]["patched"]["cases"] == 1176
    assert result["census"] == sorted(proof.PATHS)


@pytest.mark.parametrize("mutation", ["missing", "wrong_error", "state_change"])
def test_compiled_regressions_fail_for_their_behavior(
    argyll_archive, tmp_path, monkeypatch, mutation, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")

    def regressed(text, path, *, reverse=False):
        fixed = text
        if not reverse and path == "spectro/ss.c":
            replacement = {
                "missing": "",
                "wrong_error": GUARD.replace("return inst_bad_parameter;", "return inst_ok;"),
                "state_change": GUARD.replace(
                    "return inst_bad_parameter;",
                    "return (p->target_calstd = standard, inst_bad_parameter);",
                ),
            }[mutation]
            return fixed.replace(GUARD, replacement)
        return fixed

    source = mutate_production(proof.PATHS, regressed)
    with pytest.raises(AssertionError, match="repaired xcalstd result differs"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
    assert json.loads((tmp_path / "regressed/report.json").read_text())["status"] == "incomplete"
