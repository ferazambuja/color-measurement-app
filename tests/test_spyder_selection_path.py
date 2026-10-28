"""Spyder X2/2024 selection path; the actual selectors, no transport or hardware."""

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from native_argyll import spyder_calibration_slots as slots  # noqa: E402
from native_argyll import spyder_selection_path as proof  # noqa: E402

# Independent regression anchors retained from the authenticated repair evidence.
PATCHED_DRIVER_SHA256 = "ebbf8f0a72162287188a80276aa4c23c1412cc56ac409fe39c3e5fb9a4f39c8a"
TAIL = (
    "\tp->ix = dentry->ix;\t\t\t\t/* Native index */\n",
    "\tp->ix = dentry->ix;\t\t\t\t/* Native index */\n"
    "\tp->usehl = usehl;\t\t\t\t/* CM: and its measurement path */\n",
)


MISPATHED = {
    "mixed_high_low_high",
    "mixed_low_high_low",
    "max_display_boundary",
    "ccmx_list",
    "correction_matrix",
    "failed_load",
}


@pytest.fixture(scope="module")
def driver(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        return zipped.read(proof.PREFIX + proof.DRIVER_PATH).decode()


def test_unknown_source_and_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_every_selection_leaves_its_own_path(argyll_archive, tmp_path, prepared_production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    assert report["patched_sha256"] == {
        proof.DRIVER_PATH: proof.digest((prepared_production / proof.DRIVER_PATH).read_bytes())
    }
    # Both variants run over the repaired seven-slot header: the selector is the difference.
    assert report["header_sha256"] == slots.digest(
        (prepared_production / slots.SOURCE_PATH).read_bytes()
    )
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    assert set(baseline["failed_scenarios"].split(",")) == MISPATHED
    log = (output / "baseline/run.log").read_text()
    # The original keeps the previous path: high level reselected as low level and
    # the reverse, past the display-number limit, under a CCMX and after a retry.
    for scenario in MISPATHED:
        assert f"{scenario}:" in log, scenario
    assert "wrong_cbid" not in log and "uniform_high" not in log and "x2" not in log
    assert (patched["failures"], patched["failed_scenarios"], patched["returncode"]) == (0, "", 0)
    assert patched["selections"] == baseline["selections"] and patched["slots"] == 7
    assert patched["checks"] == baseline["checks"] == proof.EXPECTED["patched"]["checks"]
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output)


@pytest.mark.parametrize(
    ("old", "new", "seen_as"),
    [
        # The path is decided for inbuilt entries only: a CCMX entry keeps the old one.
        ("(inst_dtflags_mtx | inst_dtflags_ccmx)", "inst_dtflags_mtx", "ccmx_list"),
        # The path is decided but not applied on a cached reselection: the original.
        (TAIL[1].splitlines(True)[1], "", "mixed_high_low_high"),
        # The display-number limit is dropped: OLED is sent through the high-level command.
        ("\t\t && ix < p->mxdnp1\n", "", "max_display_boundary"),
    ],
    ids=["inbuilt-only", "not-applied", "no-limit"],
)
def test_a_regressed_repair_is_seen_by_the_proof(
    argyll_archive, tmp_path, monkeypatch, old, new, seen_as, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")

    def regressed(text):
        corrected = text
        assert corrected.count(old) == 1
        return corrected.replace(old, new)

    source = mutate_production([proof.DRIVER_PATH], lambda text, path: regressed(text))
    with pytest.raises(AssertionError, match=f"patched result.*{seen_as}"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
