"""Shared source-patch refusal and optional wholly substituted native proof.

The actual pinned source is separately supplied, never a package fixture. The
source-backed C check skips when that local audit input or host C compiler is
absent. It compiles and runs only functions with inert OS/device hooks.
"""

import importlib.util
import json
import platform
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from argyll_native_check import compiler_name

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "no_termination", ROOT / "tools/native_argyll/no_termination.py"
)
no_termination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(no_termination)


@pytest.fixture
def pinned_source(argyll_archive, tmp_path):
    source = tmp_path / "source"
    (source / "spectro").mkdir(parents=True)
    names = set(no_termination.PINNED_SHA256) | {"spectro/rspec.c"}
    with zipfile.ZipFile(argyll_archive) as upstream:
        prefix = "Argyll_V3.5.0/"
        jam = upstream.read(prefix + "spectro/Jamfile").decode()
        for group in ("SER_INSTS", "USB_INSTS", "FAST_SER_INSTS", "SER_USB_INSTS"):
            names.update(
                "spectro/" + name
                for name in re.search(r"^" + group + r"\s*=\s*([^;]+);", jam, re.M)[1].split()
            )
        for name in names:
            (source / name).write_bytes(upstream.read(prefix + name))
    return source


def apply_shared_patches(source):
    for name, patch in no_termination.PATCHES.items():
        path = source / name
        path.write_text(patch(path.read_text()))


@pytest.mark.parametrize("name", list(no_termination.PATCHES))
def test_unknown_upstream_is_refused_even_if_expected_call_is_present(name):
    text = "/* unrelated source */\n" + no_termination.CALLS[name]
    with pytest.raises(ValueError, match="Unrecognized pinned"):
        no_termination.PATCHES[name](text)


@pytest.mark.parametrize("name", list(no_termination.PATCHES))
def test_pinned_patch_changes_only_the_argument_and_refuses_reapplication(pinned_source, name):
    text = (pinned_source / name).read_text()
    patched = no_termination.PATCHES[name](text)
    assert no_termination._original(patched, name) == text
    assert text.count(no_termination.CALLS[name]) == 1
    assert no_termination.CALLS[name] not in patched
    assert text.replace(no_termination.CALLS[name], "CALL") == patched.replace(
        no_termination.CALLS[name].replace("pnames)", "NULL)"), "CALL"
    )
    with pytest.raises(ValueError, match="Unrecognized pinned"):
        no_termination.PATCHES[name](patched)


@pytest.mark.parametrize("name,marker", [
    ("spectro/usbio.c", '#include "inst.h"'),
    ("spectro/hidio.c", '#include "icoms.h"'),
])
def test_utf16_composition_preserves_full_source_authentication(pinned_source, name, marker):
    from native_argyll.driver_manifest import include_standalone_utf16

    original = (pinned_source / name).read_text()
    patched = include_standalone_utf16(no_termination.PATCHES[name](original), marker)
    assert no_termination._original(patched, name) == original
    with pytest.raises(ValueError, match="Pinned source hash mismatch"):
        no_termination._original(patched + "\n/* Unauthenticated change. */\n", name)


def test_proof_refuses_unpatched_transport_before_any_command(pinned_source, tmp_path):
    def forbidden_command(*args):
        pytest.fail("Unpatched source must be refused before compilation or execution")

    with pytest.raises(ValueError, match="patch is missing"):
        no_termination.prove_no_termination(
            pinned_source, tmp_path, {}, "not-a-compiler", forbidden_command
        )


def test_proof_refuses_unaudited_shared_source_change_before_any_command(pinned_source, tmp_path):
    apply_shared_patches(pinned_source)
    path = pinned_source / "spectro/usbio_ox.c"
    path.write_text(path.read_text() + "\n/* Source change requires renewed audit. */\n")
    with pytest.raises(ValueError, match="Pinned source hash mismatch"):
        no_termination.prove_no_termination(
            pinned_source, tmp_path, {}, "not-a-compiler",
            lambda *args: pytest.fail("Changed source must be refused before a command"),
        )


@pytest.mark.parametrize("call", [
    "kkill_nprocess", "kill_nprocess", "TerminateProcess", "kill", "system", "popen",
    "execl", "execv", "execve", "execvp", "fork",
])
def test_driver_inventory_rejects_a_direct_serial_process_control_bypass(pinned_source, call):
    originals = {
        name: (pinned_source / name).read_text() for name in no_termination.PINNED_SHA256
    }
    path = pinned_source / "spectro/specbos.c"
    path.write_text(path.read_text() + f"\nvoid bypass(void) {{ {call}(123, 15); }}\n")
    # hidio.c is audited first and must hold exactly one kill_nprocess( call; naming the
    # injected file keeps that expectation from answering for it.
    with pytest.raises(ValueError,
                       match="direct process-control route in driver group: spectro/specbos.c"):
        no_termination._driver_inventory(pinned_source, originals)


def test_source_backed_usb_hid_and_serial_ab_have_no_termination(argyll_archive, tmp_path):
    import argyll_native_check as native

    cc = shutil.which(compiler_name())
    if cc is None:
        pytest.skip("Host C compiler unavailable")
    source, report = native.prepare(argyll_archive, tmp_path / "production")

    commands = []

    def run(args, output, report):
        commands.append(args)
        assert not any("-framework" in arg or ".a" == Path(arg).suffix for arg in args)
        if args[0] == "nm":
            assert args[1] == "-u" and Path(args[2]).parent == tmp_path
        elif args[0] != cc:
            # A probe runs only after its own import listing was checked.
            assert ["nm", "-u", str(Path(args[0]).with_suffix(".o"))] in commands[:-1]
            assert Path(args[0]).parent == tmp_path
            assert Path(args[0]).name.startswith("no-termination-")
        result = subprocess.run(args, cwd=output, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return result.stdout

    result = no_termination.prove_no_termination(
        source, tmp_path, report, cc, run
    )
    assert result["status"] == "passed"
    pins = no_termination.composed_pins(report)
    assert result["composed_source_sha256"] == {
        name: value for name, value in pins.items()
        if value != no_termination.PINNED_SHA256[name]
    }
    required_routes = {
        "usb-macos-i1pro", "usb-macos-i1pro3", "usb-macos-munki",
        "hid-windows-i1d3", "serial-unix",
    }
    assert required_routes <= result["probes"].keys()
    executed = {Path(args[0]).name for args in commands
                if args[0] not in (cc, "nm")}
    assert {
        f"no-termination-{route}-{variant}" + (".exe" if platform.system() == "Windows" else "")
        for route in required_routes for variant in ("baseline", "patched")
    } <= executed
    public_sources = {"spectro/rspec.c"}
    jam = (source / "spectro/Jamfile").read_text()
    for group in ("SER_INSTS", "USB_INSTS", "FAST_SER_INSTS", "SER_USB_INSTS"):
        public_sources.update(
            "spectro/" + name
            for name in re.search(r"^" + group + r"\s*=\s*([^;]+);", jam, re.M)[1].split()
        )
    assert {entry["path"] for entry in result["public_driver_source_inventory"]} == public_sources
    for label, pair in result["probes"].items():
        baseline = pair["baseline"]["cases"]
        patched = pair["patched"]["cases"]
        assert not any(case["termination_calls"] for case in patched)
        assert bool(any(case["termination_calls"] for case in baseline)) == (label != "serial-unix")
        for variant in ("baseline", "patched"):
            assert set(pair[variant]["imports"]) <= no_termination.INERT_IMPORTS
    # Retained result is plain JSON, so the enclosing native receipt can retain it.
    assert json.loads(json.dumps(result)) == result


def test_probe_imports_are_refused_unless_inert(tmp_path, monkeypatch):
    """A template that misses a hook links the real call; the probe must not run."""
    monkeypatch.setattr(no_termination.platform, "system", lambda: "Darwin")
    probe = tmp_path / "no-termination-serial-unix-patched"

    def listing(text):
        return lambda args, output, report: text

    assert no_termination.inert_imports(
        probe, tmp_path, {}, listing("___stderrp\n_fprintf\n_printf\n_puts\n_strcmp\n")
    ) == ["__stderrp", "fprintf", "printf", "puts", "strcmp"]
    for leaked in ("_tcsetattr", "_open", "_kill", "_system", "_usleep"):
        with pytest.raises(RuntimeError, match=f"imports unsubstituted calls: .*{leaked[1:]}"):
            no_termination.inert_imports(
                probe, tmp_path, {}, listing(f"_printf\n{leaked}\n_puts\n"))
    with pytest.raises(RuntimeError, match="No imports listed"):
        no_termination.inert_imports(probe, tmp_path, {}, listing(""))


def test_composed_pins_override_only_series_modified_pinned_paths():
    stock = no_termination.PINNED_SHA256
    assert no_termination.composed_pins({}) == stock
    receipt = {"contribution": {"entries": [
        {"path": "spectro/Jamfile", "operation": "modify", "post_sha256": "a" * 64},
        {"path": "spectro/inst.c", "operation": "modify", "post_sha256": "b" * 64},
        {"path": "spectro/usbio.c", "operation": "add", "post_sha256": "c" * 64},
    ]}}
    pins = no_termination.composed_pins(receipt)
    assert pins["spectro/Jamfile"] == "a" * 64
    assert pins["spectro/usbio.c"] == stock["spectro/usbio.c"]     # an add never overrides
    assert "spectro/inst.c" not in pins                              # not a pinned path
    assert {k: v for k, v in pins.items() if k != "spectro/Jamfile"} == {
        k: v for k, v in stock.items() if k != "spectro/Jamfile"}
    text = "FAST_SER_INSTS = specbos.c cr250.c ;\n"
    with pytest.raises(ValueError, match="hash mismatch"):
        no_termination._original(text, "spectro/Jamfile")
    override = dict(stock, **{"spectro/Jamfile": no_termination.sha256(text.encode())})
    assert no_termination._original(text, "spectro/Jamfile", override) == text


@pytest.mark.parametrize("series", ["repairs", "cr_pr"])
def test_production_policy_proof_keeps_imported_portable_repairs(
    argyll_archive, tmp_path, series,
):
    import argyll_native_check as native

    source, report = native.prepare(
        argyll_archive, tmp_path / "production",
        contribution=native.NATIVE / "contributions" / series)
    assert report["portable_repairs_imported"] is True
    pins = no_termination.composed_pins(report)
    for name in no_termination.PINNED_SHA256:
        text = (source / name).read_text()
        original = no_termination._original(text, name, pins)
        assert no_termination.sha256(original.encode()) == pins[name]
        with pytest.raises(ValueError, match="Pinned source hash mismatch"):
            no_termination._original(text + "\n/* drift */\n", name, pins)


@pytest.mark.parametrize("system,listing,expected", [
    ("Linux", " U printf\n U stderr\n U stdout\n U strcmp\n",
     ["printf", "stderr", "stdout", "strcmp"]),
    ("Linux", " U __printf_chk\n U __fprintf_chk\n U __snprintf_chk\n",
     ["__fprintf_chk", "__printf_chk", "__snprintf_chk"]),
    ("Windows", " U __imp_printf\n U __mingw_vfprintf\n U __main\n",
     ["__main", "__mingw_vfprintf", "printf"]),
])
def test_probe_object_imports_use_target_format(tmp_path, monkeypatch, system, listing, expected):
    monkeypatch.setattr(no_termination.platform, "system", lambda: system)
    assert no_termination.inert_imports(
        tmp_path / "probe.o", tmp_path, {}, lambda *args: listing) == expected
    with pytest.raises(RuntimeError, match="imports unsubstituted calls: .*open"):
        no_termination.inert_imports(
            tmp_path / "probe.o", tmp_path, {}, lambda *args: listing + " U open\n")
