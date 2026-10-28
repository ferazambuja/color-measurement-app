"""The authenticated contribution series that composes an external driver onto
the pinned stock archive.

Every refusal here names the failure it must catch and shows the check catching
it: a series that lies about a digest, writes where it did not declare, applies
a diff anywhere but at its stated lines, or carries a file the manifest does not
list, stops the build. The positive path composes the repository's CR/PR
series onto the real archive and shows the application policies still anchor
on the composed text.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from color_measurement.native_receipt import native_worker_name

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
SERIES = TOOLS / "native_argyll/contributions/cr_pr"
ADDED_TYPES = ("instCR250", "instCR100", "instCR300", "instPR655", "instPR670")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# prepare() imports its transforms as ``native_argyll.<module>`` from tools/.
sys.path.insert(0, str(TOOLS))


contribution = load("contribution", TOOLS / "native_argyll/contribution.py")
driver_manifest = load("driver_manifest", TOOLS / "native_argyll/driver_manifest.py")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# A small synthetic series over a synthetic source tree


STOCK = {
    "spectro/inst.c": "int a;\nint b;\nint c;\nint d;\n",
    "spectro/keep.c": "untouched\n",
    "spectro/last.c": "no newline at end",
}
ARCHIVE = "0" * 64
NEW = "new file\n"
MODIFIED = "int a;\nint b;\nint cr250;\nint c;\nint d;\n"
DIFF = (
    "--- a/spectro/inst.c\n+++ b/spectro/inst.c\n"
    "@@ -1,4 +1,5 @@\n int a;\n int b;\n+int cr250;\n int c;\n int d;\n"
)


def make_source(tmp_path) -> Path:
    source = tmp_path / "source"
    for path, text in STOCK.items():
        (source / path).parent.mkdir(parents=True, exist_ok=True)
        (source / path).write_text(text, newline="\n")
    return source


def entries(diff=DIFF, modified=MODIFIED, new=NEW):
    return [
        {
            "path": "spectro/cr250.c", "operation": "add", "source": "files/spectro/cr250.c",
            "source_sha256": sha(new.encode()), "post_sha256": sha(new.encode()),
            "provenance": {"author": "A", "terms": "unassigned"},
        },
        {
            "path": "spectro/inst.c", "operation": "modify",
            "source": "patches/spectro/inst.c.patch",
            "source_sha256": sha(diff.encode()),
            "pre_sha256": sha(STOCK["spectro/inst.c"].encode()),
            "post_sha256": sha(modified.encode()),
            "provenance": {"upstream_author": "U", "upstream_license": "L",
                           "change_author": "A", "change_terms": "unassigned"},
        },
    ]


def make_series(tmp_path, rows=None, *, diff=DIFF, new=NEW, archive=ARCHIVE, added=("instCR250",),
                name="synthetic"):
    directory = tmp_path / "series"
    (directory / "files/spectro").mkdir(parents=True)
    (directory / "patches/spectro").mkdir(parents=True)
    (directory / "files/spectro/cr250.c").write_text(new, newline="\n")
    (directory / "patches/spectro/inst.c.patch").write_text(diff, newline="\n")
    manifest = {
        "schema_version": contribution.SCHEMA_VERSION,
        "name": name, "base": {"archive_sha256": archive},
        "source_origin": {"repository": "synthetic", "commit": "1" * 40, "tree": "2" * 40},
        "instrument_types_added": list(added), "entries": rows if rows is not None else entries(),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


def test_synthetic_series_applies_exactly_and_receipts_every_entry(tmp_path):
    source = make_source(tmp_path)
    stock = contribution.tree_digests(source)
    receipt = contribution.apply_contribution(make_series(tmp_path), source, stock, ARCHIVE)
    assert (source / "spectro/cr250.c").read_text() == NEW
    assert (source / "spectro/inst.c").read_text() == MODIFIED
    assert (source / "spectro/keep.c").read_text() == "untouched\n"
    assert receipt["instrument_types_added"] == ["instCR250"]
    assert [row["operation"] for row in receipt["entries"]] == ["add", "modify"]
    assert receipt["entries"][1]["provenance_upstream_author"] == "U"
    assert receipt["entries"][1]["provenance_change_terms"] == "unassigned"
    assert receipt["entries"][0]["pre_sha256"] is None
    assert receipt["manifest_sha256"] == sha((tmp_path / "series/manifest.json").read_bytes())


@pytest.mark.parametrize("mutate, message", [
    (lambda rows: rows[0].update(source_sha256="1" * 64, post_sha256="1" * 64),
     "Series file digest mismatch"),
    (lambda rows: rows[1].update(pre_sha256="1" * 64), "Pre-image digest mismatch"),
    (lambda rows: rows[1].update(post_sha256="1" * 64), "Post-image digest mismatch"),
    (lambda rows: rows[0].update(path="spectro/keep.c"), "already exists"),
    (lambda rows: rows[1].update(path="spectro/missing.c"), "absent"),
])
def test_digest_and_target_refusals(tmp_path, mutate, message):
    rows = entries()
    mutate(rows)
    source = make_source(tmp_path)
    stock = contribution.tree_digests(source)
    with pytest.raises(ValueError, match=message):
        contribution.apply_contribution(make_series(tmp_path, rows), source, stock, ARCHIVE)


@pytest.mark.parametrize("mutate, message", [
    (lambda rows: rows[0].update(path="../escape.c"), "traversal"),
    (lambda rows: rows[0].update(path="/etc/x"), "absolute"),
    (lambda rows: rows[0].update(path="spectro/./x.c"), "traversal"),
    (lambda rows: rows[0].update(path="spectro\\x.c"), "forward-slash"),
    (lambda rows: rows[0].update(path="spectro/inst.c"), "duplicate target"),
    (lambda rows: rows[0].update(operation="delete"), "not one of"),
    (lambda rows: rows.append(dict(rows[0], path="spectro/other.c")), "duplicate series"),
    (lambda rows: rows[0].update(pre_sha256="0" * 64), "addition has no pre-image"),
    (lambda rows: rows[0].update(post_sha256="1" * 64), "post-image digest"),
    (lambda rows: rows[0].update(provenance={}), "provenance"),
    (lambda rows: rows.pop(), "unlisted"),
])
def test_manifest_shape_refusals(tmp_path, mutate, message):
    rows = entries()
    mutate(rows)
    with pytest.raises(ValueError, match=message):
        contribution.load_series(make_series(tmp_path, rows))


def test_unlisted_series_file_and_symlinks_are_refused(tmp_path):
    directory = make_series(tmp_path)
    (directory / "files/spectro/extra.c").write_text("x")
    with pytest.raises(ValueError, match="unlisted"):
        contribution.load_series(directory)
    (directory / "files/spectro/extra.c").unlink()
    contribution.load_series(directory)
    real = tmp_path / "elsewhere.c"
    real.write_text(NEW)
    (directory / "files/spectro/cr250.c").unlink()
    os.symlink(real, directory / "files/spectro/cr250.c")
    with pytest.raises(ValueError, match="symlink"):
        contribution.load_series(directory)


def test_target_crossing_a_symlink_is_refused(tmp_path):
    source = make_source(tmp_path)
    (source / "spectro").rename(tmp_path / "moved")
    os.symlink(tmp_path / "moved", source / "spectro")
    with pytest.raises(ValueError, match="crosses a symlink"):
        contribution.apply_contribution(make_series(tmp_path), source, {}, ARCHIVE)
    assert not (tmp_path / "moved/cr250.c").exists()


def test_base_archive_must_match(tmp_path):
    source = make_source(tmp_path)
    stock = contribution.tree_digests(source)
    series = make_series(tmp_path, archive="f" * 64)
    with pytest.raises(ValueError, match="different base archive"):
        contribution.apply_contribution(series, source, stock, ARCHIVE)


@pytest.mark.parametrize("diff, message", [
    (DIFF.replace(" int b;\n", " int B;\n"), None),             # context differs by one byte
    (DIFF.replace("@@ -1,4 +1,5 @@", "@@ -2,4 +2,5 @@"), None), # shifted position
    (DIFF.replace("@@ -1,4 +1,5 @@", "@@ -1,4 +1,4 @@"), None), # counts disagree
    (DIFF + "stray text\n", "unclassified line"),               # invalid hunk line
    (DIFF + "@@ stray text\n", "text outside its hunks"),        # invalid next header
    ("--- a\n+++ b\n", None),                                   # no hunks
    (DIFF.replace("+int cr250;\n", "?int cr250;\n"), None),  # unclassified line
])
def test_diffs_apply_only_exactly(tmp_path, diff, message):
    with pytest.raises(ValueError, match=message):
        contribution.apply_unified_diff(STOCK["spectro/inst.c"], diff)
    rows = entries(diff=diff)
    rows[1]["source_sha256"] = sha(diff.encode())
    source = make_source(tmp_path)
    stock = contribution.tree_digests(source)
    with pytest.raises(ValueError, match=message):
        contribution.apply_contribution(make_series(tmp_path, rows, diff=diff), source, stock,
                                        ARCHIVE)
    assert (source / "spectro/inst.c").read_text() == STOCK["spectro/inst.c"]


def test_diff_handles_missing_final_newline_both_ways():
    before = "a\nb"
    grow = "--- a\n+++ b\n@@ -1,2 +1,2 @@\n a\n-b\n\\ No newline at end of file\n+b\n"
    assert contribution.apply_unified_diff(before, grow) == "a\nb\n"
    shrink = "--- a\n+++ b\n@@ -1,2 +1,2 @@\n a\n-b\n+b\n\\ No newline at end of file\n"
    assert contribution.apply_unified_diff("a\nb\n", shrink) == "a\nb"
    with pytest.raises(ValueError):
        contribution.apply_unified_diff("a\nb\n", grow)   # old side claims no newline


def test_tree_verification_catches_undeclared_writes(tmp_path):
    source = make_source(tmp_path)
    stock = contribution.tree_digests(source)
    series = contribution.load_series(make_series(tmp_path))
    receipt = contribution.apply_series(series, source, ARCHIVE)
    contribution.verify_tree(source, stock, receipt)
    (source / "spectro/stray.c").write_text("x")
    with pytest.raises(ValueError, match="stray.c"):
        contribution.verify_tree(source, stock, receipt)
    (source / "spectro/stray.c").unlink()
    (source / "spectro/keep.c").write_text("changed\n")
    with pytest.raises(ValueError, match="keep.c"):
        contribution.verify_tree(source, stock, receipt)


def test_enum_pin_accepts_only_the_declared_appended_enums(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        stock_header = zipped.read("Argyll_V3.5.0/spectro/insttypes.h").decode()
    composed = stock_header.replace(
        "\n\tinstFakeDisp", "\tinstCR250,\t/* Colorimetry Research CR-250 */\n\tinstFakeDisp")
    assert len(driver_manifest.parse_instrument_types(stock_header)) == 38
    rows = driver_manifest.parse_instrument_types(composed, ("instCR250",))
    assert len(rows) == 39 and rows[-1]["enum"] == "instCR250" and rows[-1]["native_type"] == 39
    assert rows[37]["enum"] == "instColorHug2"
    with pytest.raises(ValueError, match="differ from the declared series"):
        driver_manifest.parse_instrument_types(composed)
    with pytest.raises(ValueError, match="differ from the declared series"):
        driver_manifest.parse_instrument_types(stock_header, ("instCR250",))
    with pytest.raises(ValueError, match="Pinned public instrument enum set changed"):
        driver_manifest.parse_instrument_types(composed.replace("instColorHug2", "instOther"))


# --------------------------------------------------------------------------
# The repository's combined CR/PR series against the real pinned archive


def test_repository_series_is_self_consistent(argyll_archive):
    series = contribution.load_series(SERIES)
    assert series.name == "cr-pr-drivers"
    assert series.base_archive_sha256 == sha(Path(argyll_archive).read_bytes())
    assert series.instrument_types_added == ADDED_TYPES
    assert len(series.entries) == 69
    operations = {entry.operation for entry in series.entries}
    assert operations == {"add", "modify"}
    paths = {entry.path for entry in series.entries}
    assert {"spectro/spotread.c", "spectro/sa_conv.h"} <= paths
    assert "doc/spotread.html" not in paths
    for entry in series.entries:
        assert Path(entry.path).parts[0] in {"spectro", "cgats", "numlib", "xicc"}
        if entry.operation == "add":
            assert entry.provenance["terms"] == "GPL-2.0-or-later"
        else:
            assert entry.provenance["upstream_author"] == "Graeme W. Gill"
            assert entry.provenance["change_terms"] == "GPL-2.0-or-later"


def test_repository_series_composes_with_project_transforms(argyll_archive, tmp_path):
    with zipfile.ZipFile(argyll_archive) as zipped:
        zipped.extractall(tmp_path)
    source = tmp_path / "Argyll_V3.5.0"
    stock = contribution.tree_digests(source)
    stock_inst = (source / "spectro/inst.c").read_text()
    receipt = contribution.apply_contribution(
        SERIES, source, stock, sha(Path(argyll_archive).read_bytes()))
    assert len(receipt["entries"]) == 69
    composed_inst = (source / "spectro/inst.c").read_text()
    assert "new_cr250(icom, itype)" in composed_inst
    assert "new_cr250" not in stock_inst
    assert "new_pr655(icom, itype)" in composed_inst
    assert "new_pr655" not in stock_inst

    # The constructor guard still anchors and still precedes the new dispatch.
    guarded = driver_manifest.checked_constructor_patch(composed_inst)
    assert guarded.index("itype != expected_type") < guarded.index("new_cr250(icom, itype)")
    assert guarded.index("itype != expected_type") < guarded.index("new_pr655(icom, itype)")
    # The passive enumeration transform's exact anchors are untouched by the series.
    for path, patch in driver_manifest.passive_serial_patches().items():
        if path.startswith("spectro/icoms"):
            patch((source / path).read_text())
    # The enum pin moves only by the declared types, and the build lists both drivers.
    header = (source / "spectro/insttypes.h").read_text()
    rows = driver_manifest.parse_instrument_types(header, receipt["instrument_types_added"])
    assert len(rows) == 43
    assert [row["enum"] for row in rows[-6:]] == ["instColorHug2", *ADDED_TYPES]
    sources = driver_manifest.instrument_sources(source)
    assert {"spectro/cr250.c", "spectro/cr250_imp.c", "spectro/pr655.c",
            "spectro/pr655_imp.c"} <= set(sources)
    # The stock proof is unchanged: without the declaration the composed header is refused.
    with pytest.raises(ValueError):
        driver_manifest.parse_instrument_types(header)


def test_prepare_composes_the_series_into_the_current_broad_build(argyll_archive, tmp_path):
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    source, report = native.prepare(Path(argyll_archive), tmp_path / "broad",
                                    contribution=SERIES)
    assert report["build_scope"] == report["driver_scope"] == "all_public"
    assert report["contribution"]["name"] == "cr-pr-drivers"
    assert len(report["contribution"]["entries"]) == 69
    assert report["contribution_license_inventory"]["files"] == [
        {"path": row["path"], "source_sha256": row["post_sha256"],
         "declared_license": "GPL-2.0-or-later"}
        for row in report["contribution"]["entries"] if row["operation"] == "add"
    ]
    assert "spectro/cr250.c" not in report["upstream_source_sha256"]
    assert "spectro/pr655.c" not in report["upstream_source_sha256"]
    inst = (source / "spectro/inst.c").read_text()
    assert inst.index("itype != expected_type") < inst.index("new_cr250(icom, itype)")
    assert inst.index("itype != expected_type") < inst.index("new_pr655(icom, itype)")
    assert {row["path"] for row in report["patches"]} >= {"spectro/inst.c", "spectro/icoms_ux.c"}
    # The receipt carries the series as BUILD INPUTS, not only as provenance
    # entries: authenticate_stub enumerates the declared series from disk and
    # requires every file here, so dropping this recording would refuse every
    # --contribution build however fresh it was. Pinned because the recorder and
    # the authenticator have to enumerate the same set to agree.
    recorded = report["original_check_source_sha256"]
    expected = {path.relative_to(ROOT).as_posix(): sha(path.read_bytes())
                for path in SERIES.rglob("*") if path.is_file()}
    prefix = SERIES.relative_to(ROOT).as_posix() + "/"
    assert expected
    assert {name: digest for name, digest in recorded.items()
            if name.startswith(prefix)} == expected


def test_notice_audit_records_contributed_terms_without_relabelling(tmp_path):
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    (tmp_path / "UPSTREAM.txt").write_text("")
    unit = tmp_path / "unit.c"
    unit.write_text(
        "/* Copyright 2026 A.\n * Licence terms for this file have not been assigned. */\n")
    row = {"path": "unit.c", "operation": "add", "provenance_author": "A",
           "provenance_terms": "unassigned; owner decision pending"}
    # Not contributed: an unreviewed declaration is refused as before.
    with pytest.raises(ValueError, match="Unreviewed upstream license declaration"):
        native.retain_upstream_notices(tmp_path, tmp_path, ["unit.c"])
    inventory = native.retain_upstream_notices(tmp_path, tmp_path, ["unit.c"],
                                               contributed={"unit.c": row})
    (entry,) = inventory["files"]
    assert entry["declared_license"] == "unassigned" and entry["contributed"] is True
    assert entry["provenance"] == {"author": "A", "terms": "unassigned; owner decision pending"}
    assert inventory["redistribution_review_completed"] is False
    assert "(unassigned)" in (tmp_path / "UPSTREAM.txt").read_text()
    # A contributed file that claims terms the audit cannot check is refused.
    with pytest.raises(ValueError, match="terms the audit does not know"):
        native.retain_upstream_notices(tmp_path, tmp_path, ["unit.c"],
                                       contributed={"unit.c": dict(row, provenance_terms="MIT")})
    unit.write_text("/* Copyright 2026 A. */\n")
    with pytest.raises(ValueError, match="does not state its terms"):
        native.retain_upstream_notices(tmp_path, tmp_path, ["unit.c"],
                                       contributed={"unit.c": row})


@pytest.mark.parametrize("change", ["order", "origin", "sentinel", "extra_value"])
def test_enum_pin_preserves_actual_numeric_identity(argyll_archive, change):
    with zipfile.ZipFile(argyll_archive) as zipped:
        header = zipped.read("Argyll_V3.5.0/spectro/insttypes.h").decode()
    assert len(driver_manifest.parse_instrument_types(header)) == 38
    if change == "order":
        header = header.replace("instDTP22", "instTEMP").replace("instDTP41", "instDTP22")
        header = header.replace("instTEMP", "instDTP41")
    elif change == "origin":
        header = header.replace("devUnknown      = 0", "devUnknown      = 10")
    elif change == "sentinel":
        header = header.replace("instFakeDisp = 9998", "instFakeDisp = 99")
    else:
        header = header.replace("instDTP22,", "instHidden = 44,\n    instDTP22,")
    with pytest.raises(ValueError):
        driver_manifest.parse_instrument_types(header)


@pytest.mark.parametrize("declaration", [
    "Licence terms assigned: MIT.", "License terms: GPL-2.0-or-later.",
    "This material is licenced under the GNU GENERAL PUBLIC LICENSE Version 2 or later",
    "Licence terms unassigned; GNU GENERAL PUBLIC LICENSE Version 2 or later"])
def test_notice_audit_refuses_assigned_terms_labelled_unassigned(tmp_path, declaration):
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    (tmp_path / "UPSTREAM.txt").write_text("")
    (tmp_path / "unit.c").write_text(f"/* Copyright 2026 A. {declaration} */\n")
    with pytest.raises(ValueError, match="does not state its terms"):
        native.retain_upstream_notices(tmp_path, tmp_path, ["unit.c"], contributed={
            "unit.c": {"provenance_author": "A", "provenance_terms": "unassigned"}})


@pytest.mark.parametrize("change", ["missing_author", "missing_change_terms", "bool_schema",
                                   "unhashable_enum", "duplicate_key", "windows_absolute"])
def test_manifest_requires_unambiguous_identity_and_provenance(tmp_path, change):
    directory = make_series(tmp_path)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if change == "missing_author":
        del manifest["entries"][0]["provenance"]["author"]
    elif change == "missing_change_terms":
        del manifest["entries"][1]["provenance"]["change_terms"]
    elif change == "bool_schema":
        manifest["schema_version"] = True
    elif change == "unhashable_enum":
        manifest["instrument_types_added"] = [["instCR250"]]
    elif change == "windows_absolute":
        manifest["entries"][0]["path"] = "C:/outside.c"
    text = json.dumps(manifest)
    if change == "duplicate_key":
        text = text.replace('"name": "synthetic"', '"name": "discarded", "name": "synthetic"')
    manifest_path.write_text(text, newline="\n")
    with pytest.raises(ValueError):
        contribution.load_series(directory)


def test_tree_verification_refuses_an_unchanged_symlink(tmp_path):
    source = make_source(tmp_path)
    stock = contribution.tree_digests(source)
    target = source / "spectro/keep.c"
    outside = tmp_path / "outside.c"
    target.rename(outside)
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        contribution.verify_tree(source, stock, [])


def test_diff_headers_cannot_name_another_target(tmp_path):
    source = make_source(tmp_path)
    diff = DIFF.replace("spectro/inst.c", "spectro/another.c")
    series = contribution.load_series(make_series(tmp_path, entries(diff=diff), diff=diff))
    with pytest.raises(ValueError, match="header"):
        contribution.apply_series(series, source, ARCHIVE)


def test_unknown_diff_backslash_marker_is_refused():
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-a\n+b\n\\ anything is not a newline marker\n"
    with pytest.raises(ValueError, match="marker"):
        contribution.apply_unified_diff("a\n", diff)


@pytest.mark.parametrize("change", ["source", "manifest", "unlisted"])
def test_substituted_worker_currency_includes_its_contribution(argyll_archive, tmp_path,
                                                            monkeypatch, change):
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    runner = load("contribution_gate_runner", TOOLS / "test_all.py")
    output = tmp_path / "build"
    _, report = native.prepare(Path(argyll_archive), output, contribution=SERIES)
    root = tmp_path / "checkout"
    shutil.copytree(TOOLS / "native_argyll", root / "tools/native_argyll")
    shutil.copy2(TOOLS / "argyll_native_check.py", root / "tools/argyll_native_check.py")
    vocabulary = Path("src/color_measurement/argyll_capability.py")
    (root / vocabulary.parent).mkdir(parents=True)
    shutil.copy2(TOOLS.parent / vocabulary, root / vocabulary)
    monkeypatch.setattr(runner, "ROOT", root)
    helper = output / native_worker_name("generic-session-stub-worker")
    helper.write_text("Inert bytes, never executed")
    report.update(status="passed", generic_session={"status": "passed",
                                                   "executable_sha256": sha(helper.read_bytes())})
    (output / "report.json").write_text(json.dumps(report))
    runner.authenticate_stub(helper)
    series = root / "tools/native_argyll/contributions/cr_pr"
    target = {"source": "files/spectro/cr250.c", "manifest": "manifest.json",
              "unlisted": "files/spectro/unlisted.c"}[change]
    with (series / target).open("a") as stream:
        stream.write("\nChanged since build\n")
    with pytest.raises(RuntimeError, match="native source"):
        runner.authenticate_stub(helper)


def test_prepare_refuses_external_or_symlinked_series(argyll_archive, tmp_path):
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    directory = make_series(tmp_path)
    with pytest.raises(ValueError, match="must live under"):
        native.prepare(Path(argyll_archive), tmp_path / "build",
                       contribution=directory)
    link = tmp_path / "series-link"
    link.symlink_to(SERIES, target_is_directory=True)
    with pytest.raises(ValueError, match="directory"):
        contribution.load_series(link)


def test_changed_author_is_not_reported_as_matching_the_header(tmp_path):
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    (tmp_path / "UPSTREAM.txt").write_text("")
    (tmp_path / "unit.c").write_text("/* Copyright 2026 A. Licence terms unassigned; */\n")
    with pytest.raises(ValueError, match="declared author"):
        native.retain_upstream_notices(tmp_path, tmp_path, ["unit.c"], contributed={
            "unit.c": {"provenance_author": "Somebody Else", "provenance_terms": "unassigned"}})


def test_actual_production_headers_declare_gpl_2_or_later(tmp_path):
    """The owner assigned GPL-2.0-or-later, the terms of the spectro drivers the
    series sits beside. Each compiled unit's header must say so, agree with the
    manifest, name its author, and have Argyll's GPL text retained beside it.
    """
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    names = [f"spectro/{family}{suffix}" for family in ("cr250", "pr655")
             for suffix in (".c", ".h", "_imp.c", "_imp.h")]
    series = contribution.load_series(SERIES)
    contributed = {entry.path: {f"provenance_{key}": value
                               for key, value in entry.provenance.items()}
                   for entry in series.entries if entry.path in names}
    source = tmp_path / "source"
    for name in names:
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_bytes((SERIES / "files" / name).read_bytes())
    # Argyll ships the GPL text as License2.txt at its root; the audit retains it.
    (source / "License2.txt").write_text("GNU GENERAL PUBLIC LICENSE Version 2\n")
    output = tmp_path / "output"
    output.mkdir()
    (output / "UPSTREAM.txt").write_text("")
    inventory = native.retain_upstream_notices(source, output, names, contributed=contributed)
    assert len(inventory["files"]) == 8
    assert all(row["declared_license"] == "GPL-2.0-or-later"
               and row["license_texts"] == ["License2.txt"]
               and row["provenance"]["terms"] == "GPL-2.0-or-later"
               for row in inventory["files"])
    assert (output / "upstream-licenses/License2.txt").is_file()


@pytest.mark.parametrize("header,terms,message", [
    ("Licence terms unassigned;", "GPL-2.0-or-later", "does not declare GPL-2.0-or-later"),
    ("GNU GENERAL PUBLIC LICENSE Version 2 or later; Licence terms unassigned;",
     "GPL-2.0-or-later", "does not declare GPL-2.0-or-later"),
    ("GNU GENERAL PUBLIC LICENSE Version 2 or later", "MIT", "terms the audit does not know"),
    ("SPDX-License-Identifier: MIT\n GNU GENERAL PUBLIC LICENSE Version 2 or later",
     "GPL-2.0-or-later", "SPDX declaration disagrees"),
    ("GNU GENERAL PUBLIC LICENSE Version 2 or later", "GPL-2.0-or-later",
     "SPDX declaration disagrees"),
    ("SPDX-License-Identifier: GPL-2.0-or-later\n Licence terms unassigned;",
     "unassigned", "does not state its terms"),
])
def test_notice_audit_refuses_a_header_that_disagrees_with_its_manifest(
        tmp_path, header, terms, message):
    """A receipt naming one licence over a header stating another misstates the
    file, so the manifest and the header must agree in both directions."""
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    (tmp_path / "UPSTREAM.txt").write_text("")
    (tmp_path / "License2.txt").write_text("GNU GENERAL PUBLIC LICENSE Version 2\n")
    (tmp_path / "unit.c").write_text(f"/* Copyright 2026 A. {header} License2.txt */\n")
    with pytest.raises(ValueError, match=message):
        native.retain_upstream_notices(tmp_path, tmp_path, ["unit.c"], contributed={
            "unit.c": {"provenance_author": "A", "provenance_terms": terms}})


@pytest.mark.parametrize("name", [
    "cr250.c", "cr250.h", "cr250_imp.c", "cr250_imp.h", "cr250.txt",
    "cr250_test.c", "cr250_test.ksh", "cr250_driver_test.c", "cr250_driver_test.py",
    "cr250_fake.c", "cr250_fake.h",
    "pr655.c", "pr655.h", "pr655_imp.c", "pr655_imp.h", "pr655.txt",
    "pr655_test.c", "pr655_test.ksh", "pr655_driver_test.c", "pr655_driver_test.py",
])
def test_prepare_checks_terms_of_every_added_file(argyll_archive, tmp_path, monkeypatch, name):
    """A self-consistent digest manifest must not hide a conflicting declaration,
    even in a test or document that no compiler dependency scan will select.
    """
    native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
    native_root = tmp_path / "tools/native_argyll"
    series = native_root / "contributions/cr_pr"
    shutil.copytree(SERIES, series)
    target = series / "files/spectro" / name
    before = target.read_text()
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in before
    target.write_text(before.replace("SPDX-License-Identifier: GPL-2.0-or-later",
                                     "SPDX-License-Identifier: MIT"))
    manifest_path = series / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for row in manifest["entries"]:
        if row["path"] == f"spectro/{name}":
            row["source_sha256"] = row["post_sha256"] = sha(target.read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(native, "NATIVE", native_root)
    monkeypatch.setattr(native, "HERE", native_root.parent)
    builder = native_root.parent / "argyll_native_check.py"
    shutil.copyfile(TOOLS / "argyll_native_check.py", builder)
    monkeypatch.setattr(native, "__file__", str(builder))
    message = f"SPDX declaration disagrees with manifest: spectro/{name}"
    with pytest.raises(ValueError, match=message):
        native.prepare(Path(argyll_archive), tmp_path / "build",
                       contribution=series)
