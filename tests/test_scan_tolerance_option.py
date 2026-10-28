"""The public scan-tolerance option refuses nonfinite ratios; finite policies are unchanged."""

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from native_argyll import scan_tolerance_option as proof  # noqa: E402

# Independent regression anchors retained from the authenticated repair evidence.
GUARD = (
    "\t\t/* CM: a nonfinite ratio cannot bound a consistency check. */\n"
    "\t\tif (!isfinite(toll_ratio))\n"
    "\t\t\treturn inst_bad_parameter;\n"
)


@pytest.fixture(scope="module")
def originals(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as archive:
        return {path: archive.read(proof.PREFIX + path).decode() for path in proof.PATHS}


@pytest.mark.parametrize("sanitize", [False, True])
def test_nonfinite_ratios_are_refused_before_the_setter_and_finite_ones_are_unchanged(
    argyll_archive, tmp_path, originals, sanitize, prepared_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    result = proof.prove(argyll_archive, tmp_path / "proof", sanitize=sanitize)
    assert result["status"] == "passed"
    assert result["hardware_tested"] is result["instrument_runtime_linked"] is False
    for path, family in proof.FAMILIES.items():
        assert result["before_sha256"][path] == proof.digest(originals[path].encode())
        assert result["patched_sha256"][path] == proof.digest(
            (prepared_production / path).read_bytes()
        )
        baseline, repaired = (result["results"][family][v] for v in ("baseline", "patched"))
        for row in baseline[: proof.NONFINITE]:
            # The original stores what it was given after one setter call.
            assert (row["code"], row["calls"]) == (proof.INST_OK, 1)
        stored = [float.fromhex(row["stored"]) for row in baseline[: proof.NONFINITE]]
        assert stored[0] != stored[0] and stored[1] == float("inf")
        # The i1Pro3 floor turns negative infinity into a finite 1e-6 that no
        # caller asked for; the other two store it as given.
        assert stored[2] == (1e-6 if family == "i1pro3" else float("-inf"))
        for row in repaired[: proof.NONFINITE]:
            assert (row["code"], row["calls"], float.fromhex(row["stored"])) == (
                proof.INST_BAD_PARAMETER,
                0,
                proof.PRIOR,
            )
        assert baseline[proof.NONFINITE :] == repaired[proof.NONFINITE :]
        # The i1Pro3 floor and the other two setters' pass-through are preserved.
        stored = float.fromhex(repaired[proof.VALUES.index("-2.")]["stored"])
        assert stored == (1e-6 if family == "i1pro3" else -2.0)
    assert result == json.loads((tmp_path / "proof/report.json").read_text())


def test_a_dropped_guard_is_a_regression(argyll_archive, tmp_path, monkeypatch, mutate_production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")

    def regressed(text, path, *, reverse=False):
        corrected = text
        if not reverse and path == "spectro/munki.c":
            assert corrected.count(GUARD) == 1
            corrected = corrected.replace(GUARD, "")
        return corrected

    source = mutate_production(proof.PATHS, regressed)
    with pytest.raises(AssertionError, match="patched munki result for NAN: code 0x0, calls 1"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
    assert json.loads((tmp_path / "regressed/report.json").read_text())["status"] == "incomplete"


def test_an_arbitrary_error_cannot_replace_the_generic_bad_parameter(
    argyll_archive, tmp_path, monkeypatch
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    run = proof.subprocess.run

    def substitute(argv, **kwargs):
        result = run(argv, **kwargs)
        if Path(argv[0]).stem == "i1pro-patched":
            rows = json.loads(result.stdout)
            rows[0]["code"] = 0x1B0000
            result.stdout = json.dumps(rows)
        return result

    monkeypatch.setattr(proof.subprocess, "run", substitute)
    with pytest.raises(AssertionError, match="patched i1pro result for NAN: code 0x1b0000"):
        proof.prove(argyll_archive, tmp_path / "other-error")
