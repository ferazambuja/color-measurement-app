"""Check declared contribution terms, not ownership or redistribution permission."""

import ast
import re
from pathlib import Path


def check_declaration(name: str, declaration: str, provenance: dict) -> str:
    """Require agreement between the manifest, prose and SPDX (when assigned)."""
    terms = provenance.get("terms", "")
    normalized = " ".join(declaration.replace("*", " ").split())
    unassigned = re.search(
        r"\bLicen[cs]e terms (?:unassigned|for this file have not been assigned"
        r"(?: yet)?)[;.]", normalized,
    )
    gpl2 = "GNU GENERAL PUBLIC LICENSE Version 2 or later" in normalized
    spdx = re.findall(r"SPDX-License-Identifier:\s*([^\r\n]+)", declaration)
    if re.match(r"unassigned(?:;|$)", terms):
        if not unassigned or gpl2 or spdx:
            raise ValueError(
                f"Contributed file header does not state its terms as unassigned: {name}")
        license_id = "unassigned"
    elif terms == "GPL-2.0-or-later":
        if not gpl2 or unassigned:
            raise ValueError(
                f"Contributed file header does not declare GPL-2.0-or-later: {name}")
        if spdx != [terms]:
            raise ValueError(f"Contributed file SPDX declaration disagrees with manifest: {name}")
        license_id = terms
    else:
        raise ValueError(f"Contributed file declares terms the audit does not know: {name}")
    author = provenance.get("author")
    if not author or author not in declaration or "Copyright" not in declaration:
        raise ValueError(f"Contributed file header does not name its declared author: {name}")
    return license_id


def declaration_for(path: Path, text: str) -> str:
    """Read only the notice location used by supported contribution file formats.

    Do not infer a licence from executable test strings or a whole-file keyword
    search. New formats require an explicit notice-location rule.
    """
    if path.suffix in (".c", ".h"):
        declarations = [m[0] for m in re.finditer(r"/\*.*?\*/", text, re.DOTALL)
                        if "Copyright" in m[0]]
    elif path.suffix == ".py":
        declarations = [ast.get_docstring(ast.parse(text), clean=False) or ""]
    elif path.suffix == ".ksh":
        header = re.match(r"(?:#[^\n]*\n|\s*\n)*", text)
        declarations = [header[0]]
    elif path.suffix == ".txt":
        declarations = re.findall(r"(?m)^Licence\n\n([\s\S]+)\Z", text)
    else:
        raise ValueError(f"Unreviewed contribution notice format: {path}")
    if not declarations or "Copyright" not in declarations[0]:
        raise ValueError(f"No contributed copyright notice found: {path}")
    return declarations[0]


def audit_additions(source: Path, entries: list[dict]) -> dict:
    """Audit every added file, independently of the compiler's dependency set."""
    files = []
    for entry in entries:
        if entry["operation"] != "add":
            continue
        name = entry["path"]
        text = (source / name).read_text(encoding="utf-8")
        provenance = {k.removeprefix("provenance_"): v for k, v in entry.items()
                      if k.startswith("provenance_")}
        declaration = declaration_for(Path(name), text)
        license_id = check_declaration(name, declaration, provenance)
        files.append({"path": name, "source_sha256": entry["post_sha256"],
                      "declared_license": license_id})
    return {"scope": "all added contribution files, including tests and documentation",
            "redistribution_review_completed": False, "files": files}
