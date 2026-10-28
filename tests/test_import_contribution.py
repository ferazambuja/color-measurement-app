"""One-way Git import: exact provenance, stock patches and refusal boundaries."""
import hashlib
import json
import subprocess
import zipfile

import pytest
from native_argyll import contribution
from native_argyll import import_contribution as importer


@pytest.fixture
def source(tmp_path, monkeypatch):
    repository = tmp_path / "driver"
    repository.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args],
                                       stderr=subprocess.PIPE).decode().strip()

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("config", "core.symlinks", "true")
    git("config", "user.name", "Synthetic test")
    git("config", "user.email", "synthetic@example.invalid")
    stock = {"spectro/inst.c": "one\ntwo\nthree\n", "spectro/keep.c": "unchanged\n"}
    for name, text in stock.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, newline="\n")
    git("add", ".")
    git("commit", "--no-gpg-sign", "-qm", "Synthetic stock")
    git("tag", "upstream/v3.5.0")
    (repository / "spectro/inst.c").write_text("one\ntwo\nnew\nthree", newline="\n")
    (repository / "spectro/cr250.c").write_text("synthetic driver\n", newline="\n")
    git("add", ".")
    git("commit", "--no-gpg-sign", "-qm", "Synthetic driver")
    commit = git("rev-parse", "HEAD")
    archive = tmp_path / "stock.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for name, text in stock.items():
            zipped.writestr("Argyll_V3.5.0/" + name, text)
    monkeypatch.setattr(importer, "ARCHIVE_SHA256",
                        hashlib.sha256(archive.read_bytes()).hexdigest())
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({
        "name": "synthetic", "description": "Invented source, no device",
        "instrument_types_added": ["instCR250"], "entries": [
            {"path": "spectro/cr250.c", "provenance": {"author": "A", "terms": "T"}},
            {"path": "spectro/inst.c", "provenance": {
                "upstream_author": "U", "upstream_license": "L",
                "change_author": "A", "change_terms": "T"}},
        ],
    }))
    return repository, commit, archive, recipe, git, stock


def test_round_trip_records_origin_and_reproduces_stock_patch(source, tmp_path):
    repository, commit, archive, recipe, git, stock = source
    output = importer.derive(repository, commit, archive, recipe)
    target = tmp_path / "series"
    importer.publish(output, target, check=False)
    series = contribution.load_series(target)
    assert series.source_origin == {"repository": "driver", "commit": commit,
                                    "tree": git("rev-parse", "HEAD^{tree}")}
    source_tree = tmp_path / "composed"
    for name, text in stock.items():
        path = source_tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, newline="\n")
    receipt = contribution.apply_contribution(
        target, source_tree, contribution.tree_digests(source_tree), importer.ARCHIVE_SHA256)
    assert receipt["source_origin"] == series.source_origin
    assert (source_tree / "spectro/inst.c").read_bytes() == (
        repository / "spectro/inst.c").read_bytes()
    assert (source_tree / "spectro/cr250.c").read_bytes() == (
        repository / "spectro/cr250.c").read_bytes()
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.rglob("*") if p.is_file()}
    importer.publish(importer.derive(repository, commit, archive, recipe), target, check=True)
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
    assert not git("status", "--porcelain")


def test_check_detects_hand_edit_even_when_local_digests_are_updated(source, tmp_path):
    repository, commit, archive, recipe, _, _ = source
    output = importer.derive(repository, commit, archive, recipe)
    target = tmp_path / "series"
    importer.publish(output, target, check=False)
    path = target / "files/spectro/cr250.c"
    path.write_text("hand edit\n")
    manifest = json.loads((target / "manifest.json").read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["entries"][0].update(source_sha256=digest, post_sha256=digest)
    (target / "manifest.json").write_text(json.dumps(manifest))
    contribution.load_series(target)  # Local consistency cannot prove the source commit.
    with pytest.raises(ValueError, match="Generated contribution differs"):
        importer.publish(output, target, check=True)
    assert path.read_text() == "hand edit\n"


@pytest.mark.parametrize("dirty", ["tracked", "staged", "untracked"])
def test_dirty_source_refuses_before_import(source, dirty):
    repository, commit, archive, recipe, git, _ = source
    path = repository / ("spectro/cr250.c" if dirty != "untracked" else "extra.c")
    path.write_text("uncommitted\n")
    if dirty == "staged":
        git("add", str(path))
    with pytest.raises(ValueError, match="source repository is dirty"):
        importer.derive(repository, commit, archive, recipe)


def test_pinned_commit_not_working_head_owns_bytes(source):
    repository, commit, archive, recipe, git, _ = source
    original = (repository / "spectro/cr250.c").read_bytes()
    (repository / "spectro/cr250.c").write_text("later committed driver\n")
    git("add", ".")
    git("commit", "--no-gpg-sign", "-qm", "Later synthetic source")
    result = importer.derive(repository, commit, archive, recipe)
    assert result["files/spectro/cr250.c"] == original
    assert json.loads(result["manifest.json"])["source_origin"]["commit"] == commit


def test_git_replacement_cannot_change_pinned_source(source):
    repository, commit, archive, recipe, git, _ = source
    (repository / "spectro/cr250.c").write_text("replacement driver\n")
    git("add", ".")
    git("commit", "--no-gpg-sign", "-qm", "Synthetic replacement")
    git("replace", commit, git("rev-parse", "HEAD"))
    output = importer.derive(repository, commit, archive, recipe)
    assert output["files/spectro/cr250.c"] == b"synthetic driver\n"


def test_local_diff_settings_cannot_change_generated_patch(source):
    repository, commit, archive, recipe, git, _ = source
    expected = importer.derive(repository, commit, archive, recipe)
    git("config", "diff.context", "0")
    git("config", "diff.algorithm", "histogram")
    assert importer.derive(repository, commit, archive, recipe) == expected


def test_source_subdirectory_cannot_replace_the_repository_root(source):
    repository, commit, archive, recipe, _, _ = source
    with pytest.raises(ValueError, match="Source must name the driver repository root"):
        importer.derive(repository / "spectro", commit, archive, recipe)


def test_wrong_archive_and_wrong_git_stock_refuse(source):
    repository, commit, archive, recipe, git, _ = source
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="archive digest"):
        importer.derive(repository, commit, archive, recipe)
    archive.write_bytes(archive.read_bytes()[:-7])
    git("tag", "-f", "upstream/v3.5.0", commit)
    with pytest.raises(ValueError, match="Git baseline differs"):
        importer.derive(repository, commit, archive, recipe)


def test_git_symlink_and_unlisted_destination_are_refused(source, tmp_path):
    repository, _, archive, recipe, git, _ = source
    path = repository / "spectro/cr250.c"
    path.unlink()
    path.symlink_to("keep.c")
    git("add", ".")
    git("commit", "--no-gpg-sign", "-qm", "Synthetic symlink")
    with pytest.raises(ValueError, match="not one regular file"):
        importer.derive(repository, git("rev-parse", "HEAD"), archive, recipe)
    output = {"manifest.json": b"{}", "files/file.c": b"generated"}
    target = tmp_path / "output"
    target.mkdir()
    (target / "unique.txt").write_text("preserve")
    with pytest.raises(ValueError, match="unlisted output"):
        importer.publish(output, target, check=False)
    assert (target / "unique.txt").read_text() == "preserve"
    assert not (target / "manifest.json").exists()


@pytest.mark.parametrize("key,value", [
    ("repository", "../driver"), ("commit", "bad"), ("tree", "A" * 40), ("tree", True),
])
def test_manifest_origin_is_validated(source, tmp_path, key, value):
    repository, commit, archive, recipe, _, _ = source
    output = importer.derive(repository, commit, archive, recipe)
    manifest = json.loads(output["manifest.json"])
    manifest["source_origin"][key] = value
    output["manifest.json"] = json.dumps(manifest).encode()
    target = tmp_path / "series"
    importer.publish(output, target, check=False)
    with pytest.raises(ValueError, match="Contribution source"):
        contribution.load_series(target)
