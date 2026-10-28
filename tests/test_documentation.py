"""Documentation that claims coverage names the tests that exist.

`docs/testing.md` carries the coverage map: one row per test file, saying what
that file establishes. It is the document a reader consults to find out what is
checked, so a row for a deleted file is a claim of coverage that does not
exist -- and a test file with no row is work nobody can find.

Both directions rotted silently when the per-device stack was deleted: nine rows
survived their files, and six test files had no row at all. Prose asking for the
table to be maintained had been there the whole time; a set comparison is what
actually holds it.

The gate's link check (`tools/check_doc_snippets.py`) independently rejects a row
whose link resolves to nothing. This compares the sets, which says which row and
which file rather than which path.
"""

import re
from pathlib import Path

import pytest
from test_all import discover_test_modules

ROOT = Path(__file__).resolve().parents[1]
ROW = re.compile(r"^\|\s+\[`(tests/[^`\r\n]+\.py)`\]\([^\r\n)]+\)\s+\|", re.MULTILINE)


def test_the_coverage_map_names_exactly_the_test_files_that_exist():
    document = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
    table = re.search(r"^\| File or tool \| Existing coverage \|\n(?:\|.*(?:\n|$))+",
                      document, re.MULTILINE)
    assert table is not None, "no coverage table found; the table's shape must have changed"
    listed = set(ROW.findall(table[0]))
    present = set(discover_test_modules(ROOT))
    assert listed, "no coverage rows found; the table's shape must have changed"
    assert listed - present == set(), "coverage rows for files that no longer exist"
    assert present - listed == set(), "test files with no row in the coverage map"


@pytest.mark.parametrize("module", [
    "tests/new_test.py", "tests/nested/test_new.py", "tests/nested/new_test.py",
])
@pytest.mark.parametrize("mapped", [False, True])
def test_coverage_map_requires_a_row_for_nested_and_suffix_modules(
    tmp_path, monkeypatch, module, mapped,
):
    path = tmp_path / module
    path.parent.mkdir(parents=True)
    path.write_text("# Synthetic inventory only\n")
    (tmp_path / "tests/test_root.py").write_text("# Synthetic inventory only\n")
    document = (
        "| File or tool | Existing coverage |\n| --- | --- |\n"
        "| [`tests/test_root.py`](../tests/test_root.py) | Synthetic root |\n"
    )
    if mapped:
        document += f"| [`{module}`](../{module}) | Synthetic variant |\n"
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/testing.md").write_text(document)
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    if mapped:
        test_the_coverage_map_names_exactly_the_test_files_that_exist()
    else:
        with pytest.raises(AssertionError, match="test files with no row"):
            test_the_coverage_map_names_exactly_the_test_files_that_exist()


@pytest.mark.parametrize("placement", ["prose", "second_cell", "other_table"])
def test_a_link_outside_a_coverage_first_cell_cannot_replace_a_row(
    tmp_path, monkeypatch, placement,
):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_root.py").write_text("# Synthetic inventory only\n")
    (tmp_path / "tests/new_test.py").write_text("# Unmapped synthetic inventory\n")
    link = "[`tests/new_test.py`](../tests/new_test.py)"
    document = (
        "| File or tool | Existing coverage |\n| --- | --- |\n"
        "| [`tests/test_root.py`](../tests/test_root.py) | Synthetic root |\n"
    )
    if placement == "second_cell":
        document += f"| Other tool | {link} |\n"
    elif placement == "other_table":
        document += f"\n| Other links | Note |\n| --- | --- |\n| {link} | Inline mention |\n"
    else:
        document += f"\nInline mention: {link}.\n"
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/testing.md").write_text(document)
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="test files with no row"):
        test_the_coverage_map_names_exactly_the_test_files_that_exist()


def test_every_documented_tool_flag_exists():
    """A documented command has to be one that runs.

    Two of them were not. `docs/testing.md` told a reader to pass `--i1pro-stub`
    to `tools/test_scoped.py` and to select an `i1pro` profile, both deleted with
    the per-device stack; the whole native-stub section named workers nothing
    builds. Prose describing a command is exactly as durable as prose describing
    a file path, which is to say not at all -- and a flag is checkable, so it is
    checked.

    Only the flags written after the tool's own path count; `uv run`'s own
    `--locked` and `--extra` come earlier on the line and are not its business.

    The CI recipes are commands too. native.yml kept the deleted `--i1pro3-stub`
    and `--i1pro-stub` until 76ec2e2 found them by hand, because only Markdown
    was read here.
    """
    import re
    import subprocess
    import sys

    documents = [*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"),
                 *ROOT.glob(".github/workflows/*.yml")]
    assert documents, "no documents found to check"
    documented = {}
    for document in documents:
        # A command continued with a trailing backslash is one command: join it
        # first so the flags on its later lines are checked too. Line numbers
        # below count the joined text.
        text = re.sub(r"\\\n\s*", " ", document.read_text(encoding="utf-8"))
        for number, line in enumerate(text.splitlines(), 1):
            match = re.search(r"tools/([\w/]+\.py)(.*)$", line)
            if match:
                for flag in re.findall(r"--[\w-]+", match[2]):
                    documented.setdefault((match[1], flag), f"{document.name}:{number}")
    assert documented, "no tool invocations found; the search must have stopped matching"

    unknown = []
    for (tool, flag), where in sorted(documented.items()):
        path = ROOT / "tools" / tool
        if not path.is_file():
            unknown.append(f"{where}: tools/{tool} does not exist")
            continue
        usage = subprocess.run(
            [sys.executable, str(path), "--help"],
            capture_output=True, text=True, cwd=ROOT, timeout=120, check=False,
        ).stdout
        if flag not in re.findall(r"--[\w-]+", usage):
            unknown.append(f"{where}: tools/{tool} has no {flag}")
    assert unknown == [], "documented commands that would fail: " + "; ".join(unknown)


def test_every_named_substituted_worker_is_one_the_builder_writes():
    """A worker path is checkable the same way a flag is. The same native.yml
    passed `i1pro3-stub-worker` and `i1pro-stub-worker`, which nothing had built
    since the per-device stack went; the builder names what it writes."""
    import re

    builder = (ROOT / "tools" / "argyll_native_check.py").read_text(encoding="utf-8")
    built = set(re.findall(r'"([\w-]+-stub-worker)"', builder))
    assert built, "the builder names no substituted worker; the search must have stopped matching"
    named = {}
    for document in [*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"),
                     *ROOT.glob(".github/workflows/*.yml")]:
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            for worker in re.findall(r"([\w-]+-stub-worker)(?:\.exe)?\b", line):
                named.setdefault(worker, f"{document.name}:{number}")
    assert named, "no worker named anywhere; the search must have stopped matching"
    unknown = [f"{where}: {worker}" for worker, where in sorted(named.items())
               if worker not in built]
    assert unknown == [], "named workers nothing builds: " + "; ".join(unknown)


def test_the_counts_the_documents_quote_are_the_counts_the_code_derives():
    """"Measure; do not quote" applies to the documents too.

    Both the README and the frontend contract said ArgyllCMS has 18 calibration
    conditions. It has 19: `inst.h` declares 24 `inst_calc_` values, of which
    three are masks and two are sentinels. Nobody had re-derived it since the
    vocabulary was first written down, and a number in prose drifts exactly the
    way a file path does -- silently, and while reading as authoritative.

    Keyed on the phrasings as written. A rewording fails here and is meant to:
    the number is the thing being pinned, and it has to stay attached to a
    sentence somebody actually reads.
    """
    from color_measurement.argyll_capability import (
        _CONDITION_DETAIL,
        CONDITION_ACTIONS,
        CONTRIBUTED_INSTRUMENTS,
        INSTRUMENTS,
        MODE_ROUTES,
    )

    claims = {
        "docs/platforms.md": [
            (f"catalog has {len(INSTRUMENTS)} names", "catalog names"),
            (f"the catalog has {len(INSTRUMENTS) + len(CONTRIBUTED_INSTRUMENTS)}\nnames",
             "composed catalog names"),
        ],
        "docs/generic-frontend.md": [
            (f"**The {len(CONDITION_ACTIONS)} actions are the screens you build**, "
             f"for all {len(_CONDITION_DETAIL)} conditions:", "actions and conditions"),
        ],
    }
    missing = []
    for name, expected in claims.items():
        text = (ROOT / name).read_text(encoding="utf-8")
        missing += [f"{name}: {what} -- expected {phrase!r}"
                    for phrase, what in expected if phrase not in text]
    assert missing == [], "documented counts disagree with the code: " + "; ".join(missing)
    assert len(MODE_ROUTES) == 18, (
        "the 18 in `eighteen named routes` is MODE_ROUTES, a different table from "
        "the conditions -- if this changes, that phrasing needs checking too")
