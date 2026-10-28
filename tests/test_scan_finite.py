"""Actual strip recognizers refuse undefined averaging without changing finite reads."""

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from native_argyll import scan_finite as proof  # noqa: E402

# Independent regression anchors retained from the authenticated repair evidence.
CHANGES = (
    (
        "\twhite_avg /= (m->nraw - 2.0);",
        "\twhite_avg /= (m->nraw - 2.0);\n"
        "\t/* CM: a nonfinite normalization cannot establish consistency. */\n"
        "\tif (!isfinite(white_avg))\n"
        "\t\trv |= 1;",
    ),
    (
        "\t\tfor (j = 0; j < m->nraw; j++) \n\t\t\tpavg[pix][j] /= (double)pat[k].no;",
        "\t\tfor (j = 0; j < m->nraw; j++) {\n"
        "\t\t\tpavg[pix][j] /= (double)pat[k].no;\n"
        "\t\t\t/* CM: band sums can overflow independently of row sums. */\n"
        "\t\t\tif (!isfinite(pavg[pix][j]))\n"
        "\t\t\t\trv |= 1;\n"
        "\t\t}",
    ),
    (
        "\t\tif (cons > patch_cons_thr) {",
        "\t\t/* CM: a nonfinite ratio cannot pass, even with a nonfinite tolerance. */\n"
        "\t\tif (!isfinite(cons) || cons > patch_cons_thr) {",
    ),
)


@pytest.mark.parametrize("sanitize", [False, True])
def test_invalid_patches_reach_caller_refusal_and_valid_readings_are_unchanged(
    argyll_archive, tmp_path, sanitize
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    result = proof.prove(argyll_archive, tmp_path / "proof", sanitize=sanitize)
    assert result["status"] == "passed"
    for family in proof.PATHS.values():
        baseline, repaired = (result["results"][family][v] for v in ("baseline", "patched"))
        # Neither finite per-sample values nor a finite row sum implies finite band sums.
        row = baseline["band_overflow"]
        assert (row["nonfinite_input"], row["nonfinite_output"], row["flags"]) == (0, 2, 0)
        assert repaired["band_overflow"]["caller_error"] == repaired["band_overflow"]["readincons"]
        # Maxima from trimmed cells can overflow the normalizer alone.
        row = baseline["white_overflow"]
        assert (row["nonfinite_input"], row["nonfinite_output"], row["flags"]) == (0, 0, 0)
        assert repaired["white_overflow"]["flags"] == 1
        # Real native setters accept NaN/Inf tolerances. The explicit ratio
        # guard must refuse overflowing rows even with finite returned bands.
        for case in ("neg_row_inf_tolerance", "neg_row_nan_tolerance"):
            assert (
                baseline[case]["nonfinite_input"],
                baseline[case]["nonfinite_output"],
                baseline[case]["flags"],
            ) == (0, 0, 0)
            assert repaired[case]["flags"] == 1
            assert repaired[case]["caller_error"] == repaired[case]["readincons"]
        # The existing saturation classification, including bit2, remains intact.
        if family == "i1pro":
            assert repaired["sat9"]["flags"] == 0
            assert repaired["sat10"]["flags"] == 2
    assert result == json.loads((tmp_path / "proof/report.json").read_text())


# Each remaining guard has a counterexample that the other two cannot refuse.
@pytest.mark.parametrize(
    ("guard", "case"), [(0, "white_overflow"), (1, "nan_cell"), (2, "neg_row_inf_tolerance")]
)
def test_independent_overflow_guards_cannot_be_removed(
    argyll_archive, tmp_path, monkeypatch, guard, case, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")

    def regressed(text, path, *, reverse=False):
        corrected = text
        if not reverse:
            old, new = CHANGES[guard]
            assert corrected.count(new) == 1
            corrected = corrected.replace(new, old)
        return corrected

    source = mutate_production(proof.PATHS, regressed)
    with pytest.raises(AssertionError, match=f"munki/{case}: undefined patch accepted"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
    assert json.loads((tmp_path / "regressed/report.json").read_text())["status"] == "incomplete"


@pytest.mark.parametrize("family", ["munki", "i1pro"])
def test_an_arbitrary_helper_error_cannot_replace_the_proven_refusal(
    argyll_archive, tmp_path, monkeypatch, family
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    run = proof.subprocess.run

    def substitute(argv, **kwargs):
        result = run(argv, **kwargs)
        executable = Path(argv[0])
        if (
            executable.parent.name == "patched"
            and executable.parent.parent.name == family
            and argv[1:] == ["nan_cell"]
        ):
            row = json.loads(result.stdout)
            row.update(code=123, flags=0, caller_error=123, cleanup=0)
            result.stdout = json.dumps(row)
        return result

    monkeypatch.setattr(proof.subprocess, "run", substitute)
    with pytest.raises(AssertionError, match=f"{family}/nan_cell: expected averaging-tail refusal"):
        proof.prove(argyll_archive, tmp_path / "early-error")
    assert json.loads((tmp_path / "early-error/report.json").read_text())["status"] == "incomplete"


def test_authenticated_archive_bytes_are_the_extracted_bytes(argyll_archive, tmp_path, monkeypatch):
    original = argyll_archive.read_bytes()
    archive = tmp_path / "archive.zip"
    archive.write_bytes(original)
    read_bytes = Path.read_bytes

    def changed_after_read(path):
        data = read_bytes(path)
        if path == archive:
            path.write_bytes(b"replaced after authentication")
        return data

    class SnapshotObserved(Exception):
        pass

    def observe_zip(file):
        assert file.read() == original
        assert archive.read_text() == "replaced after authentication"
        raise SnapshotObserved

    monkeypatch.setattr(Path, "read_bytes", changed_after_read)
    monkeypatch.setattr(proof.zipfile, "ZipFile", observe_zip)
    monkeypatch.setattr(proof.shutil, "which", lambda _: "inert-compiler")
    with pytest.raises(SnapshotObserved):
        proof.prove(archive, tmp_path / "proof")


def test_current_native_setter_is_executed_without_changing_the_predecessor(
        argyll_archive, tmp_path, mutate_production):
    import subprocess

    def refusing_setter(text, path):
        before = proof._function(text, "munki_set_scan_toll")
        assert before.count("return MUNKI_OK;") == 1
        after = before.replace("return MUNKI_OK;", "return 123; /* injected setter failure */")
        return text.replace(before, after)

    source = mutate_production(["spectro/munki_imp.c"], refusing_setter)
    output = tmp_path / "setter-proof"
    with pytest.raises(subprocess.CalledProcessError) as error:
        proof.prove(argyll_archive, output, source=source)
    # The probe exits 4 only when its real native setter refuses the request.
    assert error.value.returncode == 4
    assert Path(error.value.cmd[0]).parent == output / "munki/patched"
    report = json.loads((output / "report.json").read_text())
    hashes = report["tolerance_setter_sha256"]["munki"]
    assert hashes["baseline"] != hashes["patched"]
    assert "baseline" in report["results"]["munki"]
    assert report["status"] == "incomplete"
