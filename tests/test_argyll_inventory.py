"""Original synthetic HTML fixtures: no upstream manual prose or code is copied."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "argyll_inventory.py"
SPEC = importlib.util.spec_from_file_location("argyll_inventory", TOOL)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def manual(name="sample", rows=None):
    rows = rows or (
        f"{name} [-q count] file<br>-q count&nbsp;&nbsp;Choose count<br>file&nbsp;&nbsp;Input"
    )
    return (
        f"<html><h2>module/{name}</h2>\n<h3>Usage Summary</h3>\n{rows}\n"
        "<h3>Details</h3>\nNot part of the synopsis.<br>-z hidden prose option</html>"
    ).encode()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    docs = root / "doc"
    docs.mkdir(parents=True)
    for name in audit.EXPECTED_MANUALS:
        (docs / f"{name}.html").write_bytes(manual(name))
    links = [
        f'<a href="{name}.html">{"splitsti3" if name == "splitti3" else name}</a><br>'
        for name in sorted(audit.EXPECTED_MANUALS)
    ]
    (docs / "ArgyllDoc.html").write_text(
        "<h1>Fixture documentation V3.5.0</h1>\n" + "\n".join(links)
    )
    (docs / "MinorTools.html").write_text(
        "<h3>Synthetic minor catalog</h3>\nmodule/applycal.exe<br>\n"
        "one/probe.exe<br>\ntwo/probe.exe<br>\nmissing/stale.exe<br>"
    )
    (docs / "Installing_MSWindows.html").write_text(
        "<p>Helper identifiers only:</p>\nArgyllCMS_install_USB.exe<br>\n"
        "ArgyllCMS_uninstall_USB.exe<br>"
    )
    module = root / "module"
    module.mkdir()
    (module / "applycal.c").write_text("/* Original inert fixture; never compiled. */\n")
    (module / "Jamfile").write_text(
        "Executables =\n applycal other ;\nMain applycal : applycal.c ;\n"
        "# Main applycal : retired.c ;\n"
    )
    return root


def parsed(rows, name="sample"):
    return audit.manual_inventory(f"doc/{name}.html", audit.parse_html(manual(name, rows)))


def test_tag_wrapping_unlinked_flags_and_single_nbsp_argument():
    command = parsed(
        'sample [-q file]<br>\n<a href="#q">-q</a>&nbsp;<i>filename</i>'
        "&nbsp;&nbsp;Description<br>\n-u&nbsp;&nbsp;Unlinked option<br>"
    )
    rows = command["synopsis"]
    assert rows[1]["syntax"] == "-q filename"
    assert rows[1]["anchors"] == ["#q"]
    assert rows[2]["syntax"] == "-u"
    assert rows[1]["line_start"] == 4
    assert rows[1]["line_end"] == 4
    assert not any("hidden" in (row["syntax"] or "") for row in rows)


def test_entities_pre_table_rows_and_hidden_markup():
    page = audit.parse_html(
        b"<h2>module/sample</h2><h3>Usage</h3>\n"
        b"<pre>sample [flags]\n-a  Alpha\n-b  Beta</pre>"
        b"<!-- -hidden --> <script>-secret</script><style>-styled</style>"
        b"<table><tr><td>-c arg</td><td>Gamma &amp; Delta</td></tr></table>"
        b"<h3>Details</h3>"
    )
    command = audit.manual_inventory("doc/sample.html", page)
    assert [row["syntax"] for row in command["synopsis"]] == [
        "sample [flags]",
        "-a",
        "-b",
        "-c arg",
    ]
    assert command["synopsis"][2]["line_start"] == 4


def test_decoding_declared_charset_and_invalid_encoding():
    page = audit.parse_html(b'<meta charset="windows-1252"><p>caf\xe9</p>')
    assert page.rows[0]["text"] == "café"
    with pytest.raises(ValueError, match="Cannot decode"):
        audit.parse_html(b'<meta charset="not-an-encoding"><p>x</p>')


def test_branches_continuations_repeated_options_and_post_operand_flag():
    command = parsed(
        "sample [flags]<br>-f mode&nbsp;&nbsp;a = first, b = second<br>"
        "c = third, d = fourth<br>-f a p0 p1 p2<br>"
        "M7, Q8 (def.), S&amp;T 2000_2 or file.dat<br>"
        "k,&nbsp;&nbsp;3&nbsp;&nbsp;&nbsp;&nbsp;Channel<br>"
        "file&nbsp;&nbsp;Input<br>-o option&nbsp;&nbsp;Late option<br>"
        "Unstructured wording with no dependable syntax boundary.<br>"
    )
    rows = command["synopsis"]
    assert rows[1]["choices"] == ["a", "b"]
    assert rows[2]["choices"] == ["c", "d"]
    assert rows[3]["syntax"] == "-f a p0 p1 p2"
    assert rows[4]["kind"] == "continuation"
    assert rows[4]["choices"] == ["M7", "Q8", "S&T 2000_2", "file.dat"]
    assert rows[5]["syntax"] == "k, 3"
    assert rows[7]["syntax"] == "-o option"
    assert rows[8]["kind"] == "unparsed"
    assert rows[8]["syntax"] is None
    assert len(rows[8]["row_text_sha256"]) == 64
    assert command["diagnostics"][-1]["code"] == "unparsed_synopsis_row"


def test_invocation_mismatches_are_preserved_not_aliases():
    command = parsed("other [-v] file<br>-v&nbsp;&nbsp;Description<br>")
    assert command["name"] == "sample"
    assert command["synopsis"][0]["syntax"] == "other [-v] file"
    assert command["diagnostics"][0]["code"] == "invocation_name_differs_no_alias_inferred"
    missing = parsed("usage: -x in out<br>usage: -i in data out<br>-x&nbsp;&nbsp;Description")
    assert len([r for r in missing["synopsis"] if r["kind"] == "invocation"]) == 2
    assert all(d["code"] == "invocation_missing_command_name" for d in missing["diagnostics"])


def test_inline_whitespace_and_literal_enum_never_silently_drop_choices():
    command = parsed(
        "sample [flags]<br>-o choice&nbsp;&nbsp;Select<br>"
        "<span>2000_2 </span><i> (def.)</i>, 2010_2, S&amp;T 2020_2 or file.dat<br>"
        ":- inputs 'source.dat',&nbsp;&nbsp;and outputs something<br>"
    )
    enum = command["synopsis"][2]
    assert enum["kind"] == "continuation"
    assert enum["syntax"] == "2000_2 (def.), 2010_2, S&T 2020_2 or file.dat"
    assert enum["choices"] == ["2000_2", "2010_2", "S&T 2020_2", "file.dat"]
    assert command["synopsis"][3]["kind"] == "unparsed"


def test_invocation_only_flags_do_not_collect_command_examples():
    command = parsed("sample [-v]<br>-q&nbsp;&nbsp;Description<br>Examples<br>sample -q2 file<br>")
    assert command["invocation_only_option_tokens"] == ["-v"]
    assert command["synopsis"][-1]["kind"] == "command_example"


def test_complete_catalog_and_build_evidence(source):
    result = audit.inventory(source)
    assert result["coverage"]["manual_commands"] == 50
    assert result["coverage"]["minor_catalog_entries"] == 4
    assert result["archive_identity_verified_by_this_tool"] is False
    assert result["coverage"]["syntax_semantics_independently_verified"] is False
    commands = {command["id"]: command for command in result["commands"]}
    assert commands["applycal"]["catalogs"] == ["main_index", "minor_tools"]
    assert commands["applycal"]["evidence"]["source_files"][0]["path"] == "module/applycal.c"
    refs = commands["applycal"]["evidence"]["build_references"]
    assert [(r["rule"], r["line_start"], r["line_end"], r["commented"]) for r in refs] == [
        ("Executables", 1, 2, False),
        ("Main", 3, 3, False),
        ("Main", 4, 4, True),
    ]
    assert "minor:one/probe" in commands and "minor:two/probe" in commands
    assert commands["minor:missing/stale"]["diagnostics"][0]["code"] == (
        "no_same_basename_source_file_found"
    )
    mismatch = commands["splitti3"]["diagnostics"][0]
    assert mismatch["code"] == "catalog_label_differs_from_command_no_alias_inferred"
    assert mismatch["label"] == "splitsti3"
    assert [helper["name"] for helper in result["windows_helpers"]] == list(audit.HELPERS)


def test_orphan_manual_is_discovered(source):
    (source / "doc" / "uncatalogued.html").write_bytes(manual("uncatalogued"))
    result = audit.inventory(source)
    orphan = next(c for c in result["commands"] if c["name"] == "uncatalogued")
    assert orphan["catalogs"] == ["orphan_manual"]
    assert result["coverage"]["orphan_manuals"] == 1


@pytest.mark.parametrize("filename", ["spotread.html", "ArgyllDoc.html", "MinorTools.html"])
def test_missing_required_public_page_is_fatal(source, filename):
    (source / "doc" / filename).unlink()
    with pytest.raises(ValueError, match="Missing required"):
        audit.inventory(source)


def test_wrong_version_and_empty_minor_catalog_fail(source):
    path = source / "doc" / "ArgyllDoc.html"
    original = path.read_text()
    path.write_text(original.replace("3.5.0", "9.9.9"))
    with pytest.raises(ValueError, match="version"):
        audit.inventory(source)
    path.write_text(original)
    (source / "doc" / "MinorTools.html").write_text("<p>Empty catalog</p>")
    with pytest.raises(ValueError, match="catalog is empty"):
        audit.inventory(source)


def test_missing_helper_or_unrecognized_usage_fails(source):
    path = source / "doc" / "Installing_MSWindows.html"
    path.write_text("ArgyllCMS_install_USB.exe")
    with pytest.raises(ValueError, match="helper missing"):
        audit.inventory(source)
    with pytest.raises(ValueError, match="No recognizable"):
        parsed("Unrecognized invocation prose only.<br>")


def test_cli_determinism_and_source_drift_check(source, tmp_path, capsys):
    output = tmp_path / "inventory.json"
    args = ["--source-root", str(source)]
    assert audit.main([*args, "--output", str(output)]) == 0
    original = output.read_bytes()
    assert audit.main([*args, "--check", str(output)]) == 0
    assert audit.main([*args, "--output", str(output)]) == 0
    assert original == output.read_bytes()
    record = json.loads(original)
    assert not any(str(source) in json.dumps(item) for item in record["documents"])
    with (source / "module" / "applycal.c").open("a") as stream:
        stream.write("/* Changed synthetic source. */\n")
    assert audit.main([*args, "--check", str(output)]) == 1
    assert output.read_bytes() == original
    assert "differs" in capsys.readouterr().err


def test_cli_fails_instead_of_empty_success(tmp_path):
    output = tmp_path / "inventory.json"
    assert audit.main(["--source-root", str(tmp_path), "--output", str(output)]) == 1
    assert not output.exists()


def test_source_reference_cannot_escape_root(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        audit.read_file(tmp_path, "../outside.html")
