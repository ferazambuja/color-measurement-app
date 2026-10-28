"""Check that documentation still agrees with the tree it describes.

Two properties, both mechanical:

*Excerpts.* Each ``<!-- source: relative/path.py:function -->`` marker must be
immediately followed by a ``python`` fenced block. Source is parsed, never
imported or run. Function lines include decorators, docstrings, and inline
comments. The final line terminator is excluded from comparison because it
separates the fence; all other bytes, including indentation and internal line
endings, must match.

*Links.* Every repository-relative Markdown link, inline or reference-style,
must name something that exists with the same case. Deleting a module is the
moment its documentation goes stale, and prose saying so is unenforceable; a
resolved path is not. A fragment into a Markdown document, or into the same page, must name
one of its headings, slugged as GitHub renders them; renaming a heading
otherwise breaks every link to it silently. External links are not fetched --
this check never leaves the checkout.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Sequence
from urllib.parse import unquote

MARKER = re.compile(rb"<!-- source: ([^\s]+) -->")
# Markdown inline links and images. The path stops at whitespace, ')' or the
# '#' that begins a fragment, so `[text](guide.md#section)` checks `guide.md`
# and then its `section` heading; an empty path is the same page.
# A title may use quotes or parentheses; escaped delimiters remain title text.
TITLE = r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^)\\])*\))'''
LINK = re.compile(
    rf"\[[^\]]*\]\(\s*<?([^)>\s#]*)(?:#([^)>\s]*))?>?(?:\s+{TITLE})?\s*\)"
)
# Reference-style definitions, `[label]: guide.md#section`, name links too;
# the destination may start on the next line. A footnote, `[^1]: text`, is not a link.
DEFINITION = re.compile(
    r"^ {0,3}\[(?!\^)[^\]]+\]:[ \t]*(?:\n[ \t]*)?<?([^\s>#]*)(?:#([^\s>]*))?>?", re.M
)
# ATX headings, up to three spaces indented, without their closing #s; fences
# are skipped so a shell comment in a code block is not a heading.
HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
# An HTML comment block runs from a line starting `<!--` to the line holding
# `-->`; nothing inside it renders, headings included.
COMMENT = re.compile(r" {0,3}<!--")
# Inline comments contribute no heading text, but identical text in code spans does.
HEADING_COMMENT = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)|<!--.*?-->")
_EXTERNAL = ("http://", "https://", "mailto:", "ftp://", "//")


def _without_final_newline(value: bytes) -> bytes:
    if value.endswith(b"\r\n"):
        return value[:-2]
    if value.endswith(b"\n"):
        return value[:-1]
    return value


def _uses_wrong_case(base: Path, relative: Path) -> bool:
    """Check written path components before resolution can remove them.

    Directory entry names retain their case even on a case-insensitive volume.
    Missing paths and unreadable parents keep their ordinary validation errors.
    """
    parts = relative.parts
    current = base
    if relative.is_absolute():
        current = Path(relative.anchor)
        parts = parts[1:]
    for part in parts:
        if part == "..":
            # Resolve only the already-checked prefix, retaining symlink semantics
            # while allowing a later component after an absent directory and `..`.
            current = current.resolve().parent
            continue
        try:
            names = {child.name for child in current.iterdir()}
        except OSError:
            names = set()
        if part not in names and any(name.casefold() == part.casefold() for name in names):
            return True
        current = current / part
    return False


def extract_symbol(reference: str, root: Path) -> bytes:
    """Return original source lines for one unambiguous top-level function."""
    relative_path, separator, symbol = reference.rpartition(":")
    if not separator or not relative_path or not symbol.isidentifier():
        raise ValueError(f"invalid source reference: {reference!r}")
    root = root.resolve()
    wrong_case = _uses_wrong_case(root, Path(relative_path))
    path = (root / relative_path).resolve()
    if Path(relative_path).is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"source path must remain inside the repository: {relative_path}")
    if wrong_case:
        raise ValueError(f"source path uses wrong case: {relative_path}")
    if path.suffix != ".py":
        raise ValueError(f"source path must name a Python file: {relative_path}")
    try:
        source = path.read_bytes()
        tree = ast.parse(source, filename=relative_path)
    except (OSError, SyntaxError, ValueError) as error:
        raise ValueError(f"cannot parse {relative_path}: {error}") from error
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one top-level function for {reference}; found {len(matches)}")
    node = matches[0]
    first_line = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    return b"".join(source.splitlines(keepends=True)[first_line - 1 : node.end_lineno])


def heading_anchors(text: str) -> set[str]:
    """Return the fragment ids GitHub gives a document's headings.

    GitHub lowercases the heading text, drops everything but word characters,
    hyphens and spaces, turns each space into a hyphen, and suffixes repeats with
    -1, -2 in document order. Backticks and emphasis asterisks are dropped by
    the same rule, so raw heading text slugs as its rendering does.
    """
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    fence = None
    comment = False
    for line in text.split("\n"):
        opening = FENCE.match(line)
        if fence is not None:
            if (opening and opening[1][0] == fence[0] and len(opening[1]) >= len(fence)
                    and not opening[2].strip(" \t\r")):
                fence = None
            continue
        if comment:
            comment = "-->" not in line
            continue
        # A backtick fence's info string cannot contain a backtick; such a line
        # is inline code, and opening a fence on it would invert every later one.
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence = opening[1]
            continue
        start = COMMENT.match(line)
        if start:
            comment = "-->" not in line[start.end():]
            continue
        heading = HEADING.fullmatch(line)
        if heading is None:
            continue
        # HEADING already excludes the surrounding spaces. A removed edge comment
        # leaves its neighbouring space in the rendered text, and GitHub keeps it
        # (as it does for a leading emoji), so do not strip again.
        title = HEADING_COMMENT.sub(lambda match: match[0] if match[1] else "", heading[1] or "")
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        anchor = slug
        while anchor in occurrences:
            occurrences[slug] += 1
            anchor = f"{slug}-{occurrences[slug]}"
        occurrences[anchor] = 0
        anchors.add(anchor)
    return anchors


def check_links(root: Path, path: Path, anchors: dict[Path, set[str]] | None = None) -> list[str]:
    """Return one error per repository-relative link that names nothing.

    Paths must use their directory entries' exact case before resolution; a fragment
    into a Markdown document is looked up among its heading anchors. Nothing is
    imported or fetched. A link leaving the checkout is an error in itself,
    because it cannot be checked and will not survive being read from a clone.
    """
    anchors = {} if anchors is None else anchors
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{path}: cannot read: {error}"]
    for match in [*LINK.finditer(text), *DEFINITION.finditer(text)]:
        target, fragment = match[1], match[2]
        if target.startswith(_EXTERNAL) or ":" in target.partition("/")[0]:
            continue
        if not target and fragment is None:
            continue
        line = text.count("\n", 0, match.start()) + 1
        wrong_case = _uses_wrong_case(path.parent, Path(target)) if target else False
        resolved = (path.parent / target).resolve() if target else path.resolve()
        if not resolved.is_relative_to(root):
            errors.append(f"{path}:{line}: link leaves the repository: {target}")
            continue
        if wrong_case:
            errors.append(f"{path}:{line}: link path uses wrong case: {target}")
            continue
        if not resolved.exists():
            errors.append(f"{path}:{line}: link names nothing: {target}")
            continue
        if fragment is None or resolved.suffix != ".md":
            continue
        if resolved not in anchors:
            try:
                anchors[resolved] = heading_anchors(resolved.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as error:
                errors.append(f"{path}:{line}: cannot read {target}: {error}")
                continue
        if unquote(fragment) not in anchors[resolved]:
            errors.append(f"{path}:{line}: link names no heading: {target}#{fragment}")
    return errors


def check_documents(
    root: Path, documentation: Path, required: Sequence[str] = ()
) -> tuple[int, list[str]]:
    """Return the count of checked excerpts and all validation failures."""
    if not documentation.is_dir():
        return 0, [f"documentation directory does not exist: {documentation}"]
    errors: list[str] = []
    checked = 0
    symbols: set[str] = set()
    for path in sorted(documentation.rglob("*.md")):
        lines = path.read_bytes().splitlines(keepends=True)
        index = 0
        while index < len(lines):
            line = _without_final_newline(lines[index])
            if b"<!-- source:" not in line:
                index += 1
                continue
            location = f"{path}:{index + 1}"
            marker = MARKER.fullmatch(line)
            if marker is None:
                errors.append(f"{location}: malformed source marker")
                index += 1
                continue
            try:
                reference = marker[1].decode("utf-8")
            except UnicodeDecodeError:
                errors.append(f"{location}: source reference must be UTF-8")
                index += 1
                continue
            index += 1
            if index >= len(lines) or _without_final_newline(lines[index]) != b"```python":
                errors.append(f"{location}: source marker must immediately precede a python fence")
                continue
            start = index + 1
            index = start
            while index < len(lines) and _without_final_newline(lines[index]) != b"```":
                index += 1
            if index == len(lines):
                errors.append(f"{location}: unclosed source fence")
                continue
            excerpt = b"".join(lines[start:index])
            index += 1
            checked += 1
            try:
                expected = extract_symbol(reference, root)
            except ValueError as error:
                errors.append(f"{location}: {error}")
                continue
            if _without_final_newline(excerpt) != _without_final_newline(expected):
                errors.append(f"{location}: excerpt differs from {reference}")
                continue
            symbols.add(reference.rpartition(":")[2])
    for symbol in required:
        if symbol not in symbols:
            errors.append(f"missing valid source excerpt for required function: {symbol}")
    # Top-level documents link into `docs/`, `src/` and `tests/` as well, and are
    # the ones a reader meets first, so they are checked with the rest.
    anchors: dict[Path, set[str]] = {}
    for path in sorted(documentation.rglob("*.md")) + sorted(root.glob("*.md")):
        errors.extend(check_links(root, path, anchors))
    return checked, errors


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", nargs="?", const="docs", metavar="DIRECTORY")
    mode.add_argument("--extract", metavar="PATH:FUNCTION")
    parser.add_argument("--require", action="append", default=[], metavar="FUNCTION")
    arguments = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1] if root is None else root.resolve()
    if arguments.extract:
        if arguments.require:
            parser.error("--require can only be used when checking documentation")
        try:
            source = extract_symbol(arguments.extract, root)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        sys.stdout.buffer.write(source)
        return 0
    documentation = root / (arguments.check or "docs")
    checked, errors = check_documents(root, documentation, arguments.require)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Checked {checked} source excerpt(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
