"""Statically inventory Argyll 3.5.0's public HTML manuals and tool catalogs.

No imports of upstream code, execution, subprocesses, or network access occur.
Only command/argument syntax is copied into the output. Every usage row has a
line reference and text digest; uncertain rows remain explicit parsing gaps.
Build-file references are evidence of text, never proof of an installed tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

VERSION = "3.5.0"
EXPECTED_ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
EXPECTED_MANUALS = frozenset(
    "applycal average cb2ti3 cctiff ccxxmake chartread collink colprof colverify "
    "cxf2ti3 dispcal dispread dispwin extracticc extractttag fakeCMY fakeread "
    "filmread filmtarg greytiff iccdump iccgamut icclu iccvcgt illumread "
    "invprofcheck kodak2ti3 ls2ti3 mppcheck mpplu mppprof oeminst printcal "
    "printtarg profcheck refine revfix scanin spec2cie specplot splitti3 "
    "spotread synthcal synthread targen tiffgamut timage txt2ti3 viewgam xicclu".split()
)
HELPERS = ("ArgyllCMS_install_USB.exe", "ArgyllCMS_uninstall_USB.exe")
FLAG = re.compile(r"(?<![\w-])--?[A-Za-z0-9?][A-Za-z0-9_?#-]*")
DECLARATION = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+")
USAGE = re.compile(r"Usage(?: Summary)?\s*:?", re.IGNORECASE)
SPACE = re.compile(r"[ \t\r\n\f]+")


def normalized(text: str) -> str:
    return " ".join(text.split())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ManualHTML(HTMLParser):
    """Render logical rows while retaining original HTML line coordinates.

    Ordinary HTML whitespace is collapsed, but NBSP column gaps survive until
    syntax extraction. PRE newlines and table cell boundaries are retained.
    Comments, scripts and styles cannot introduce catalog entries or options.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.links: list[dict] = []
        self.parts: list[str] = []
        self.line_start: int | None = None
        self.line_end = 1
        self.heading: str | None = None
        self.pre = False
        self.hidden = 0
        self.active_link: dict | None = None
        self.row_links: list[dict] = []

    def flush(self) -> None:
        text = "".join(self.parts).strip()
        if not self.pre:
            text = SPACE.sub(" ", text)
        if text:
            self.rows.append(
                {
                    "text": text,
                    "line_start": self.line_start,
                    "line_end": self.line_end,
                    "heading": self.heading,
                    "links": list(self.row_links),
                }
            )
        self.parts = []
        self.line_start = None
        self.row_links = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("br", "p", "div", "tr", "li", "pre", "h1", "h2", "h3", "h4"):
            self.flush()
        if tag in ("td", "th") and self.parts:
            self.parts.append("\xa0\xa0")
        if tag in ("h1", "h2", "h3", "h4"):
            self.heading = tag
        if tag == "pre":
            self.pre = True
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href") is not None:
            self.active_link = {
                "href": attributes["href"],
                "label": "",
                "line_start": self.getpos()[0],
                "line_end": self.getpos()[0],
            }
            self.row_links.append(self.active_link)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
            return
        if self.hidden:
            return
        if tag == "a" and self.active_link is not None:
            self.active_link["line_end"] = self.getpos()[0]
            self.active_link["label"] = normalized(self.active_link["label"])
            self.links.append(self.active_link)
            self.active_link = None
        if tag in ("p", "div", "tr", "li", "pre", "h1", "h2", "h3", "h4"):
            self.flush()
        if tag == self.heading:
            self.heading = None
        if tag == "pre":
            self.pre = False

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        if self.active_link is not None:
            self.active_link["label"] += data
        line = self.getpos()[0]
        chunks = data.splitlines(keepends=True) if self.pre else [data]
        for chunk in chunks:
            content = chunk.strip()
            if content:
                first = line + chunk[: len(chunk) - len(chunk.lstrip())].count("\n")
                if self.line_start is None:
                    self.line_start = first
                self.line_end = line + chunk.rstrip().count("\n")
            self.parts.append(chunk.rstrip("\r\n") if self.pre else SPACE.sub(" ", chunk))
            if self.pre and chunk.endswith(("\r", "\n")):
                self.flush()
            line += chunk.count("\n")


def read_file(root: Path, relative: str) -> bytes:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Source reference escapes the supplied root: {relative}")
    return path.read_bytes()


def parse_html(data: bytes) -> ManualHTML:
    charset = re.search(rb"charset\s*=\s*[\"']?([A-Za-z0-9_-]+)", data[:4096], re.I)
    encoding = charset[1].decode("ascii") if charset else "utf-8"
    parser = ManualHTML()
    try:
        parser.feed(data.decode(encoding))
        parser.close()
        parser.flush()
    except (UnicodeError, LookupError) as error:
        raise ValueError(f"Cannot decode HTML declared as {encoding}: {error}") from error
    return parser


def reference(path: str, row: dict) -> dict:
    return {"path": path, "line_start": row["line_start"], "line_end": row["line_end"]}


def local_link(path: str, href: str) -> str | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    candidate = Path(path).parent / unquote(parsed.path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate.as_posix()


def syntax_row(row: dict, command: str, index: int, previous_option: int | None) -> dict:
    """Extract left-column syntax, retaining rather than hiding uncertain rows."""
    raw, text = row["text"], normalized(row["text"])
    result = {
        "row": index,
        "line_start": row["line_start"],
        "line_end": row["line_end"],
        "kind": "unparsed",
        "syntax": None,
        "tokens": [],
        "choices": [],
        "previous_option_row": previous_option,
        "anchors": [link["href"] for link in row["links"] if link["href"].startswith("#")],
        "row_text_sha256": digest(text.encode("utf-8")),
    }
    invocation = re.sub(r"^usage:\s*", "", text, flags=re.I)
    if (
        text.lower().startswith("usage:")
        or re.match(re.escape(command) + r"(?:\s|$)", invocation)
        or (
            index == 0
            and (re.match(r"[A-Za-z][\w-]*\s+\[", invocation) or text.lower().startswith("usage:"))
        )
    ):
        result.update(kind="invocation", syntax=invocation, tokens=FLAG.findall(invocation))
        return result

    # NBSP padding and PRE/table columns are stronger evidence than prose case.
    gap = re.search(r"[ \xa0\t]{2,}|\t", raw)
    # A comma list may pad its final literal before the description column.
    if gap and raw[: gap.start()].rstrip().endswith(","):
        subsequent = re.search(r"[ \xa0\t]{2,}", raw[gap.end() :])
        if subsequent:
            gap = re.compile(r"[ \xa0\t]{2,}").search(raw, gap.end() + subsequent.start())
    left = normalized(raw[: gap.start()]) if gap else text
    if gap and not left:
        left = text
    if text.startswith("-"):
        result["kind"] = "option"
        if gap is None:
            # A few upstream rows omit their column padding. Capitalized prose
            # after lowercase placeholders is distinguishable; ambiguous tails
            # retain a diagnostic instead of being copied as purported syntax.
            boundary = re.search(r"\s+(?=[A-Z][a-z]{2,}(?:\s|$))", left)
            if boundary:
                left = left[: boundary.start()]
            elif len(left.split()) > 12:
                left = ""
        if left:
            result["syntax"] = left
            result["tokens"] = FLAG.findall(left)
        else:
            result["kind"] = "unparsed"
            result["tokens"] = FLAG.findall(text)
    elif gap and left and not text.startswith(":-"):
        # Operand and literal branch columns are syntax, unlike their right-hand
        # descriptions. A colon-prefixed parameter is retained in full.
        result.update(kind="operand_or_branch", syntax=left)
    else:
        branch = re.match(r"([A-Za-z0-9_#.+,-]+)\s*(?:=|: | - )", text)
        parameter = re.match(r"([a-z]:[A-Za-z0-9_:.,]+)(?:\s|$)", text)
        if parameter:
            result.update(kind="branch", syntax=parameter[1])
        elif branch:
            result.update(kind="branch", syntax=branch[1])
        elif text in ("...", "|"):
            result.update(kind="continuation", syntax=text)

    choices = re.findall(r"(?:^|[,;:]\s*)([A-Za-z0-9_#.+-]+)\s*=", text)
    # An enum may start in the description column of an option row.
    if gap:
        choices = (
            re.findall(r"(?:^|[,;]\s*)([A-Za-z0-9_#.+-]+)\s*=", normalized(raw[gap.end() :]))
            + choices
        )
    result["choices"] = list(dict.fromkeys(choices))
    # Explicit comma-separated literal lists are common observer/illuminant
    # continuations. Only accept short literal forms, never general prose.
    literals = re.sub(r"\s*\((?:def\.?|default)\)", "", text, flags=re.I).rstrip(" ]")
    alternatives = re.split(r",\s*|\s+or\s+", literals)
    if (
        result["kind"] != "option"
        and len(alternatives) >= 3
        and all(re.fullmatch(r"[A-Za-z0-9_&./*+-]+(?: [0-9_]+)?", token) for token in alternatives)
    ):
        result.update(kind="continuation", syntax=text, choices=alternatives)
    if result["kind"] == "unparsed" and re.match(r'^Use ["\']?--?', text):
        result.update(kind="option_example", tokens=FLAG.findall(text))
    if result["kind"] == "unparsed":
        result["tokens"] = FLAG.findall(text)
    return result


def manual_inventory(path: str, parser: ManualHTML) -> dict | None:
    declarations = [
        row
        for row in parser.rows
        if row["heading"] == "h2" and DECLARATION.fullmatch(normalized(row["text"]))
    ]
    usages = [
        index
        for index, row in enumerate(parser.rows)
        if row["heading"] and USAGE.fullmatch(normalized(row["text"]))
    ]
    if not declarations:
        if usages:
            raise ValueError(f"Usage section without a recognizable command declaration: {path}")
        return None
    if len(declarations) != 1 or not usages:
        raise ValueError(f"Missing/ambiguous command declaration or usage section: {path}")
    declaration = normalized(declarations[0]["text"])
    name = declaration.rsplit("/", 1)[1]
    synopsis: list[dict] = []
    for start in usages:
        previous = None
        in_examples = False
        for row in parser.rows[start + 1 :]:
            if row["heading"]:
                break
            if normalized(row["text"]).lower().rstrip(":") in ("example", "examples"):
                in_examples = True
            item = syntax_row(row, name, len(synopsis), previous)
            if in_examples and item["kind"] == "invocation":
                item["kind"] = "command_example"
            if item["kind"] == "option":
                previous = item["row"]
            synopsis.append(item)
    if not any(row["kind"] == "invocation" for row in synopsis):
        raise ValueError(f"No recognizable command invocation: {path}")
    diagnostics = [
        {"code": "unparsed_synopsis_row", **reference(path, row), "row": row["row"]}
        for row in synopsis
        if row["kind"] == "unparsed"
    ]
    for row in synopsis:
        if row["kind"] == "invocation" and row["syntax"].split()[0] != name:
            diagnostics.append(
                {
                    "code": (
                        "invocation_missing_command_name"
                        if row["syntax"].startswith("-")
                        else "invocation_name_differs_no_alias_inferred"
                    ),
                    **reference(path, row),
                    "invocation_name": row["syntax"].split()[0],
                }
            )
    return {
        "id": name,
        "name": name,
        "manual_path": path,
        "declared_source_path": declaration,
        "declaration_reference": reference(path, declarations[0]),
        "catalogs": [],
        "catalog_references": [],
        "synopsis": synopsis,
        "diagnostics": diagnostics,
        "invocation_only_option_tokens": sorted(
            {token for row in synopsis if row["kind"] == "invocation" for token in row["tokens"]}
            - {token for row in synopsis if row["kind"] == "option" for token in row["tokens"]}
            - {"-options"}
        ),
    }


def build_evidence(root: Path, name: str, declared: str | None) -> dict:
    """Collect exact-name source files and Jam statements, without evaluating Jam."""
    stem = name.removesuffix(".exe")
    sources = []
    for path in sorted(root.rglob(f"{stem}.c")):
        relative = path.relative_to(root).as_posix()
        data = read_file(root, relative)
        sources.append({"path": relative, "sha256": digest(data), "match": "same_basename"})
    directories = {Path(item["path"]).parent for item in sources}
    if declared:
        directories.add(Path(declared).parent)
    evidence = []
    for directory in sorted(directories):
        for path in sorted((root / directory).glob("Jamfile*")):
            relative = path.relative_to(root).as_posix()
            data = read_file(root, relative)
            lines = data.decode("utf-8", errors="replace").splitlines()
            index = 0
            while index < len(lines):
                line = lines[index]
                commented = line.lstrip().startswith("#")
                beginning = line.lstrip().lstrip("#").strip()
                match = re.match(
                    r"(Executables|MainVariant|Main|MainsFromSources|File)\b", beginning
                )
                start = index
                index += 1
                if not match:
                    continue
                statement = beginning
                while ";" not in statement and index < len(lines):
                    statement += " " + lines[index].strip()
                    index += 1
                if not re.search(
                    r"(?<![\w])" + re.escape(stem) + r"(?:\.c|\.exe)?(?![\w])", statement
                ):
                    continue
                evidence.append(
                    {
                        "path": relative,
                        "sha256": digest(data),
                        "line_start": start + 1,
                        "line_end": index,
                        "rule": match[1],
                        "commented": commented,
                        "conditions_evaluated": False,
                    }
                )
    return {
        "source_files": sources,
        "build_references": evidence,
        "runtime_or_installation_verified": False,
    }


def inventory(source_root: Path) -> dict:
    root = source_root.resolve()
    if not (root / "doc").is_dir():
        raise ValueError("Source root must contain the extracted Argyll doc directory")
    pages: dict[str, ManualHTML] = {}
    documents = []
    for path in sorted((root / "doc").glob("*.html")):
        relative = path.relative_to(root).as_posix()
        data = read_file(root, relative)
        pages[relative] = parse_html(data)
        documents.append({"path": relative, "sha256": digest(data)})
    required = {"doc/ArgyllDoc.html", "doc/MinorTools.html", "doc/Installing_MSWindows.html"}
    required.update(f"doc/{name}.html" for name in EXPECTED_MANUALS)
    if missing := required - pages.keys():
        raise ValueError(
            f"Missing required Argyll {VERSION} public pages: {', '.join(sorted(missing))}"
        )
    versions = set(
        re.findall(
            r"\bV(\d+\.\d+\.\d+)\b",
            " ".join(row["text"] for row in pages["doc/ArgyllDoc.html"].rows if row["heading"]),
        )
    )
    if versions != {VERSION}:
        raise ValueError(f"Expected documentation version {VERSION}; found {sorted(versions)}")
    commands = []
    for path, page in pages.items():
        command = manual_inventory(path, page)
        if command is not None:
            commands.append(command)
    by_manual = {command["manual_path"]: command for command in commands}
    by_name = {command["name"]: command for command in commands}
    if len(by_name) != len(commands) or EXPECTED_MANUALS - by_name.keys():
        raise ValueError("Expected public manuals did not produce unique command records")
    for link in pages["doc/ArgyllDoc.html"].links:
        target = local_link("doc/ArgyllDoc.html", link["href"])
        if target in by_manual:
            command = by_manual[target]
            if "main_index" not in command["catalogs"]:
                command["catalogs"].append("main_index")
            command["catalog_references"].append(
                {
                    **reference("doc/ArgyllDoc.html", link),
                    "label": link["label"],
                    "href": link["href"],
                }
            )
            if link["label"] != command["name"]:
                command["diagnostics"].append(
                    {
                        "code": "catalog_label_differs_from_command_no_alias_inferred",
                        **reference("doc/ArgyllDoc.html", link),
                        "label": link["label"],
                    }
                )
    if not any("main_index" in command["catalogs"] for command in commands):
        raise ValueError("Main command catalog is empty")
    minor_count = 0
    for row in pages["doc/MinorTools.html"].rows:
        for match in re.finditer(r"\b([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+\.exe)\b", row["text"]):
            declared = match[1].removesuffix(".exe")
            name = declared.rsplit("/", 1)[1]
            command = by_name.get(name)
            if command is None:
                command = {
                    "id": f"minor:{declared}",
                    "name": name,
                    "manual_path": None,
                    "declared_source_path": declared,
                    "catalogs": [],
                    "catalog_references": [],
                    "synopsis": [],
                    "diagnostics": [],
                }
                commands.append(command)
            if "minor_tools" not in command["catalogs"]:
                command["catalogs"].append("minor_tools")
            command["catalog_references"].append(
                {
                    **reference("doc/MinorTools.html", row),
                    "label": match[1],
                    "href": None,
                }
            )
            minor_count += 1
    if not minor_count:
        raise ValueError("MinorTools executable catalog is empty")
    for command in commands:
        if command["manual_path"] and not command["catalogs"]:
            command["catalogs"] = ["orphan_manual"]
        command["evidence"] = build_evidence(root, command["name"], command["declared_source_path"])
        if not command["evidence"]["source_files"]:
            command["diagnostics"].append({"code": "no_same_basename_source_file_found"})
    helpers = []
    for name in HELPERS:
        refs = [
            reference("doc/Installing_MSWindows.html", row)
            for row in pages["doc/Installing_MSWindows.html"].rows
            if name in row["text"]
        ]
        if not refs:
            raise ValueError(f"Required Windows helper missing from installation guide: {name}")
        helpers.append(
            {
                "name": name,
                "catalog": "windows_installation_helper",
                "manual_references": refs,
                "evidence": build_evidence(root, name, f"usb/{name}"),
            }
        )
    commands.sort(key=lambda command: command["id"])
    gaps = sum(row["kind"] == "unparsed" for command in commands for row in command["synopsis"])
    return {
        "schema_version": 1,
        "argyll_version": VERSION,
        "expected_archive_sha256": EXPECTED_ARCHIVE_SHA256,
        "archive_identity_verified_by_this_tool": False,
        "scope": "static documentation syntax inventory; no execution or installation verification",
        "documents": documents,
        "commands": commands,
        "windows_helpers": helpers,
        "coverage": {
            "scanned_html_files": len(pages),
            "expected_public_manuals": len(EXPECTED_MANUALS),
            "manual_commands": len(by_manual),
            "minor_catalog_entries": minor_count,
            "main_index_commands": sum("main_index" in c["catalogs"] for c in commands),
            "orphan_manuals": sum("orphan_manual" in c["catalogs"] for c in commands),
            "synopsis_rows": sum(len(c["synopsis"]) for c in commands),
            "option_rows": sum(r["kind"] == "option" for c in commands for r in c["synopsis"]),
            "unparsed_rows": gaps,
            "extraction_no_unparsed_rows": gaps == 0,
            "syntax_semantics_independently_verified": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--check", type=Path, metavar="EXISTING")
    args = parser.parse_args(argv)
    try:
        result = inventory(args.source_root)
        serialized = (
            json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode()
        if args.check:
            if args.check.read_bytes() != serialized:
                print("Inventory differs from the current source tree", file=sys.stderr)
                return 1
        else:
            args.output.write_bytes(serialized)
        gaps = result["coverage"]["unparsed_rows"]
        print(f"Inventory {'matches' if args.check else 'written'}; {gaps} unparsed usage rows.")
        return 0
    except (OSError, ValueError) as error:
        print(f"Inventory error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
