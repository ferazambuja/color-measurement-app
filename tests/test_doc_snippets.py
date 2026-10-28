"""Source excerpt and link checks use synthetic documents; nothing is imported.

The link half exists because deleting a module is the moment its documentation
goes stale, and nothing noticed: a per-device stack was removed with its tests,
and fourteen links kept pointing at the deleted files through a green gate.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "check_doc_snippets.py"
SPEC = importlib.util.spec_from_file_location("check_doc_snippets", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

FUNCTION = b'def example(value):\n    """Keep the actual source."""\n    return value + 1\n'


def make_files(tmp_path, *, source=FUNCTION, excerpt=FUNCTION, reference="source.py:example"):
    (tmp_path / "source.py").write_bytes(source)
    documentation = tmp_path / "docs"
    documentation.mkdir()
    (documentation / "example.md").write_bytes(
        f"<!-- source: {reference} -->\n```python\n".encode() + excerpt + b"```\n"
    )
    return documentation


def test_exact_function_passes_and_mutated_excerpt_fails(tmp_path):
    documentation = make_files(tmp_path)
    assert checker.check_documents(tmp_path, documentation, ["example"]) == (1, [])
    path = documentation / "example.md"
    path.write_bytes(path.read_bytes().replace(b"value + 1", b"value + 2"))
    count, errors = checker.check_documents(tmp_path, documentation, ["example"])
    assert count == 1
    assert any("excerpt differs" in error for error in errors)
    assert any("missing valid source excerpt" in error for error in errors)


def test_extract_includes_decorators_docstring_and_inline_comment_without_execution(tmp_path):
    function = (
        b"@undefined_decorator(\n    3,\n)\n"
        b"async def example(value):\n"
        b'    """Actual docstring."""\n'
        b"    return value  # Keep this comment.\n"
    )
    source = b"raise RuntimeError('do not execute')\n\n" + function + b"\nOTHER = 2\n"
    documentation = make_files(tmp_path, source=source, excerpt=function)
    assert checker.extract_symbol("source.py:example", tmp_path) == function
    assert checker.check_documents(tmp_path, documentation) == (1, [])


@pytest.mark.parametrize("reference", ["missing.py:example", "source.py:missing"])
def test_unresolved_source_fails(tmp_path, reference):
    documentation = make_files(tmp_path, reference=reference)
    assert checker.check_documents(tmp_path, documentation)[1]


@pytest.mark.parametrize(
    "source", [b"def example(:\n", FUNCTION + b"\n" + FUNCTION, b"example = 3\n"]
)
def test_invalid_or_ambiguous_source_fails(tmp_path, source):
    documentation = make_files(tmp_path, source=source)
    assert checker.check_documents(tmp_path, documentation)[1]


@pytest.mark.parametrize(
    "replacement, expected",
    [
        (b"<!-- source: source.py:example --> extra\n", "malformed source marker"),
        (b"<!-- source: source.py:example -->\n\n```python\n", "immediately precede"),
        (b"<!-- source: source.py:example -->\n```text\n", "immediately precede"),
        (b"<!-- source: source.py:example -->\n```python\n", "unclosed source fence"),
    ],
)
def test_bad_marker_or_fence_fails(tmp_path, replacement, expected):
    documentation = make_files(tmp_path)
    (documentation / "example.md").write_bytes(replacement)
    assert any(expected in error for error in checker.check_documents(tmp_path, documentation)[1])


def test_missing_documentation_and_required_excerpt_fail(tmp_path):
    assert checker.check_documents(tmp_path, tmp_path / "missing")[1]
    documentation = make_files(tmp_path)
    assert checker.check_documents(tmp_path, documentation, ["undocumented"])[1]
    (documentation / "example.md").unlink()
    assert checker.check_documents(tmp_path, documentation, ["example"])[1]


@pytest.mark.parametrize("reference, message", [
    ("../source.py:example", "remain inside"),
    ("/source.py:example", "remain inside"),
    ("bad", "invalid source reference"),
])
def test_invalid_or_external_reference_fails(tmp_path, reference, message):
    """The file outside the root exists, so only the containment check can refuse it."""
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "source.py").write_text("def example():\n    return 1\n")
    with pytest.raises(ValueError, match=message):
        checker.extract_symbol(reference, root)


def test_internal_whitespace_is_not_normalized(tmp_path):
    documentation = make_files(tmp_path, excerpt=FUNCTION.replace(b"    return", b"  return"))
    assert checker.check_documents(tmp_path, documentation)[1]


def test_crlf_source_and_excerpt_match(tmp_path):
    function = FUNCTION.replace(b"\n", b"\r\n")
    documentation = make_files(tmp_path, source=function, excerpt=function)
    assert checker.check_documents(tmp_path, documentation) == (1, [])


def test_final_newline_only_is_not_part_of_fence_comparison(tmp_path):
    documentation = make_files(tmp_path, source=FUNCTION.rstrip(b"\n"))
    assert checker.check_documents(tmp_path, documentation) == (1, [])


def test_cli_default_explicit_check_and_extract(tmp_path, capsys):
    make_files(tmp_path)
    assert checker.main([], root=tmp_path) == 0
    assert checker.main(["--check", "docs", "--require", "example"], root=tmp_path) == 0
    assert "Checked 1" in capsys.readouterr().out
    assert checker.main(["--extract", "source.py:example"], root=tmp_path) == 0
    assert capsys.readouterr().out.encode() == FUNCTION
    assert checker.main(["--extract", "source.py:missing"], root=tmp_path) == 1
    assert "found 0" in capsys.readouterr().err


def write_links(tmp_path, body: str) -> Path:
    documentation = tmp_path / "docs"
    documentation.mkdir(exist_ok=True)
    (documentation / "links.md").write_text(body, encoding="utf-8")
    return documentation


def test_a_link_that_resolves_passes_and_one_that_names_nothing_fails(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "other.md").write_text("target\n", encoding="utf-8")
    documentation = write_links(tmp_path, "[here](other.md) and [gone](deleted.md)\n")
    errors = checker.check_documents(tmp_path, documentation)[1]
    assert len(errors) == 1
    assert "link names nothing: deleted.md" in errors[0]
    assert "links.md:1" in errors[0]


def test_a_fragment_is_stripped_before_the_file_is_looked_for(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "other.md").write_text("## A heading\n", encoding="utf-8")
    documentation = write_links(tmp_path, "[section](other.md#a-heading)\n")
    assert checker.check_documents(tmp_path, documentation)[1] == []


@pytest.mark.parametrize("body", [
    "[page](OTHER.md#a-heading)\n",
    "[page](../DOCS/other.md#a-heading)\n",
    "![plot](ASSETS/plot.png)\n",
    "[page]: OTHER.md#a-heading\n",
    "[page](ASSETS/../other.md#a-heading)\n",
    "[page](missing/../OTHER.md#a-heading)\n",
])
def test_links_require_exact_case_before_resolving(tmp_path, body):
    documentation = write_links(tmp_path, body)
    (documentation / "other.md").write_text("## A heading\n", encoding="utf-8")
    (documentation / "Assets").mkdir()
    (documentation / "Assets" / "plot.png").write_bytes(b"image")
    errors = checker.check_documents(tmp_path, documentation)[1]
    assert len(errors) == 1
    assert "link path uses wrong case:" in errors[0]


def test_exact_case_links_keep_parent_segments_and_fragments(tmp_path):
    documentation = write_links(
        tmp_path,
        "[page](../docs/Assets/../other.md#a-heading)\n"
        "![plot](Assets/plot.png)\n"
        "[page]: other.md#a-heading\n",
    )
    (documentation / "other.md").write_text("## A heading\n", encoding="utf-8")
    (documentation / "Assets").mkdir()
    (documentation / "Assets" / "plot.png").write_bytes(b"image")
    assert checker.check_documents(tmp_path, documentation)[1] == []


@pytest.mark.parametrize("reference", [
    "Source.py:example",
    "SRC/source.py:example",
    "SRC/../source.py:example",
    "missing/../Source.py:example",
])
def test_source_markers_require_exact_case_before_resolving(tmp_path, reference):
    documentation = make_files(tmp_path, reference=reference)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "source.py").write_bytes(FUNCTION)
    with pytest.raises(ValueError, match="source path uses wrong case:"):
        checker.extract_symbol(reference, tmp_path)
    assert any("source path uses wrong case:" in error
               for error in checker.check_documents(tmp_path, documentation)[1])


def test_exact_case_source_markers_keep_parent_segments(tmp_path):
    documentation = make_files(tmp_path, reference="src/../source.py:example")
    (tmp_path / "src").mkdir()
    assert checker.check_documents(tmp_path, documentation) == (1, [])


def test_external_and_in_page_links_are_not_resolved_on_disk(tmp_path):
    documentation = write_links(
        tmp_path,
        "# Heading\n"
        "[web](https://example.invalid/x) [mail](mailto:a@example.invalid) [top](#heading)\n",
    )
    assert checker.check_documents(tmp_path, documentation)[1] == []


def test_a_fragment_must_name_a_heading_of_its_document(tmp_path):
    # A renamed heading breaks every link to it while both files still exist,
    # so path resolution alone passed a broken anchor.
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "other.md").write_text("## Renamed heading\n", encoding="utf-8")
    documentation = write_links(
        tmp_path,
        "# Here\n"
        "[a](other.md#a-heading) [b](#gone) [c](#here) [d](<other.md#renamed-heading>)\n",
    )
    errors = checker.check_documents(tmp_path, documentation)[1]
    assert [error.partition(": ")[2] for error in errors] == [
        "link names no heading: other.md#a-heading",
        "link names no heading: #gone",
    ]
    assert all("links.md:2" in error for error in errors)


def test_heading_anchors_follow_github_slugs():
    text = (
        "# Current contract 48\n"
        "## The `dataset_revision` field: A1/B1, 2° & u′v′ ##\n"
        "   ### Indented *emphasis*\n"
        "    #### Indented code, not a heading\n"
        "## Repeat\n## Repeat\n## Repeat\n"
        "```sh\n# a shell comment\n```\n"
        "~~~\n## fenced\n~~~\n"
        "```text\n```still-code\n# Still in backticks\n```\n## After backticks\n"
        "~~~text\n~~~still-code\n# Still in tildes\n~~~\n## After tildes\n"
        "```inline `code` is not a fence\n## After inline code\n```\n# In a real fence\n```\n"
        "<!-- a note -->\n## After a one-line comment\n"
        "<!--\n## Commented out\n-->\n## After a comment block\n"
        "## Visible <!-- hidden --> title\n"
        "## `<!-- literal -->` code\n"
        "## <!-- lead --> Leading comment\n"
        "## Trailing comment <!-- tail -->\n"
        "## 📦 Leading emoji\n"
        "#hashtag\n"
        "## Café\n"
    )
    assert checker.heading_anchors(text) == {
        "current-contract-48",
        "the-dataset_revision-field-a1b1-2--uv",
        "indented-emphasis",
        "repeat", "repeat-1", "repeat-2",
        "café", "after-backticks", "after-tildes", "after-inline-code",
        "after-a-one-line-comment", "after-a-comment-block",
        "visible--title", "---literal----code",
        "-leading-comment", "trailing-comment-", "-leading-emoji",
    }


def test_titled_links_and_reference_definitions_are_checked_too(tmp_path):
    # Both forms name a destination; skipping them let a broken one pass.
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "other.md").write_text("## A heading\n", encoding="utf-8")
    documentation = write_links(
        tmp_path,
        '[a](other.md#a-heading "Title") [b](other.md#missing "Title") [c](gone.md \'t\')\n'
        '[d](other.md#missing-parenthesis (Title)) '
        '[e](other.md#a-heading (Title))\n'
        r'[f](other.md#missing-escape "A \"quoted\" title")' '\n'
        r"[g](other.md#a-heading 'An \'escaped\' title')" '\n'
        "[ok]: other.md#a-heading\n[bad]: other.md#absent\n[lost]: deleted.md\n"
        "[continued]:\n  other.md#missing-continuation\n"
        "[continued-ok]:\n\tother.md#a-heading\n"
        "[^1]: A footnote is not a link.\n",
    )
    errors = [error.partition(": ")[2]
              for error in checker.check_documents(tmp_path, documentation)[1]]
    assert sorted(errors) == sorted([
        "link names no heading: other.md#missing",
        "link names nothing: gone.md",
        "link names no heading: other.md#missing-parenthesis",
        "link names no heading: other.md#missing-escape",
        "link names no heading: other.md#absent",
        "link names nothing: deleted.md",
        "link names no heading: other.md#missing-continuation",
    ])


def test_an_encoded_fragment_matches_and_other_targets_keep_their_fragment(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "other.md").write_text("## Café\n", encoding="utf-8")
    (tmp_path / "docs" / "data.json").write_text("{}\n", encoding="utf-8")
    documentation = write_links(tmp_path, "[a](other.md#caf%C3%A9) [b](data.json#field)\n")
    assert checker.check_documents(tmp_path, documentation)[1] == []


def test_a_link_leaving_the_checkout_is_refused_even_if_it_exists(tmp_path):
    # Resolvable on this machine and broken in every clone, so path existence is
    # not the property; staying inside the repository is.
    documentation = write_links(tmp_path, "[outside](../../etc/hosts)\n")
    errors = checker.check_documents(tmp_path, documentation)[1]
    assert len(errors) == 1 and "leaves the repository" in errors[0]


def test_a_top_level_document_is_checked_with_the_rest(tmp_path):
    documentation = write_links(tmp_path, "no links here\n")
    (tmp_path / "README.md").write_text("[gone](docs/deleted.md)\n", encoding="utf-8")
    errors = checker.check_documents(tmp_path, documentation)[1]
    assert len(errors) == 1 and "README.md:1" in errors[0]
