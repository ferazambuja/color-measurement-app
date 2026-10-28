"""Pinned shared transport patches and wholly substituted source-flow proof.

This module never imports or runs an instrument runtime. Generated probes contain
whole authenticated upstream functions with inert OS, device and termination
hooks. Windows branches compiled against substitute types are not Windows ABI
or hardware validation. Process cleanup of our own helper remains unchanged.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
from collections.abc import Callable
from pathlib import Path

from color_measurement.native_receipt import native_worker_name

REPAIR_ID = 'app-policy:no-termination'

NATIVE = Path(__file__).resolve().parent
PINNED_SHA256 = {
    "spectro/Jamfile": "a1c2da5dee92da2112d55ada087118426ab4b4c034930e66f58acf59ed511b44",
    "spectro/usbio.c": "fd86ede3492ae1a08245749abe3d653b31902475b7e185de7ef4d05bef3c2086",
    "spectro/hidio.c": "3bf1019968a388e3338a8ade987cbd5194db67cd01715c2892a2f3f042db12e3",
    "spectro/usbio_ox.c": "24c3902f59c41d507b5dedded7c0f69fda20ea07a1b85f98ef31dfee44807cdb",
    "spectro/usbio_lx.c": "1a686a7f35dc3fe0e44691dab9b85c48bc4b8de8d127556b93a781a1f05334a6",
    "spectro/usbio_bsd.c": "9780a8d810b5c42c80e652431a405d32007269ebb4dc09c49dd184737114ee6d",
    "spectro/usbio_nt.c": "5002e541aa3d084a7f56ecdd182f57013362d971ae075e570ab9ba9867214f08",
    "spectro/usbio_w0.c": "f385d1f26a7db3ed7a679025116637d456979f7180a42e43c45d0567d509d388",
    "spectro/usbio_dk.c": "3de50d36d7c8a7cfc6db8c970758dcdab2fffa32ca858c513789103ab1e5a54c",
    "spectro/conv.c": "c6b04014888cb6f4bbd90237460278498766fe20e3ec3d000edadb9e7f74a6fd",
    "spectro/i1pro.c": "ac75b6e3f47537b87ba7ae2b84a5cfdd84047413bdf0235114d4a489562a0bc5",
    "spectro/i1pro3.c": "9c4b1ac9f8af507a2f79de12e51ee89c090ad4d2d0e5557544bc37467c0c535c",
    "spectro/munki.c": "775ba17c71e42d9c931a91d4e66965a7077ce5c7a6b3edb845dcbbf5b3cecad6",
    "spectro/i1d3.c": "65839017319d335ae4de270fd9c882ca0d504afe4863f5cd72cf08df5d0c1a6a",
    "spectro/huey.c": "853ef8bcf7b432fd81f11df982d2ee7c8f20649d2438870065a5707262f15353",
    "spectro/icoms.c": "902638b0934ed263c743c40d763572aef196ba05862dc925f0e9268dee808f3f",
    "spectro/icoms_ux.c": "b9577a44731150223d4f652841cfc22be4eaea92b7d5a949f4276179a707ffb0",
    "spectro/icoms_nt.c": "bef7cc35de665d967110255df147512b93af8f24e537dc8f4afe38a57bc41441",
}
#: Canonical C imports allowed in an executed probe after target prefix normalization:
#: output, string and memory helpers and the compiler's stack guard. Any file, device,
#: terminal, process or sleep call is a hook the template failed to substitute.
INERT_IMPORTS = frozenset({
    "__stack_chk_fail", "__stack_chk_guard", "__stderrp", "__stdoutp", "abort", "bzero",
    "exit", "fflush", "fprintf", "memcpy", "memset", "printf", "putchar", "puts",
    "snprintf", "strcmp", "strlen", "strncmp", "stderr", "stdout",
    "__acrt_iob_func", "__iob_func", "__mingw_vfprintf", "__mingw_vprintf",
    "__mingw_vsnprintf",
    "__printf_chk", "__fprintf_chk", "__snprintf_chk",
    "__main",  # MinGW's compiler-injected static-constructor startup hook.
})
CALLS = {
    "spectro/usbio.c": "usb_open_port(p, config, wr_ep, rd_ep, usbflags, retries, pnames)",
    "spectro/hidio.c": "hid_open_port(p, hidflags, retries, pnames)",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _patch(text: str, name: str) -> str:
    old = CALLS[name]
    if sha256(text.encode()) != PINNED_SHA256[name] or text.count(old) != 1:
        raise ValueError(f"Unrecognized pinned shared transport source: {name}")
    return text.replace(old, old.replace("pnames)", "NULL)"))


def usb_no_termination_patch(text: str) -> str:
    """Discard every driver's process-name list at the shared USB boundary."""
    return _patch(text, "spectro/usbio.c")


def hid_no_termination_patch(text: str) -> str:
    """Discard every driver's process-name list at the shared HID boundary."""
    return _patch(text, "spectro/hidio.c")


PATCHES: dict[str, Callable[[str], str]] = {
    "spectro/usbio.c": usb_no_termination_patch,
    "spectro/hidio.c": hid_no_termination_patch,
}


def composed_pins(report: dict) -> dict[str, str]:
    """The pre-patch digest each pinned path must have in this build.

    Stock, unless an authenticated contribution series modified the path: then
    the series' verified post-image is the text this project's patches were
    applied to. Only paths already in the pin table can be overridden, and only
    from the series receipt the checker itself wrote after verifying it.
    """
    pins = dict(PINNED_SHA256)
    for row in report.get("contribution", {}).get("entries", []):
        if row["operation"] == "modify" and row["path"] in pins:
            pins[row["path"]] = row["post_sha256"]
    return pins


def _original(text: str, name: str, pins: dict[str, str] | None = None) -> str:
    """Undo policy and authenticate the resulting A/B control.

    Imported production source keeps its portable repairs. Only application
    transport policies are reversed for this control.
    """
    pins = PINNED_SHA256 if pins is None else pins
    if name in CALLS:
        from native_argyll.driver_manifest import previous_standalone_utf16

        text = previous_standalone_utf16(text)
        old = CALLS[name]
        new = old.replace("pnames)", "NULL)")
        if text.count(new) != 1 or old in text:
            raise ValueError(f"Shared no-termination patch is missing or ambiguous: {name}")
        original = text.replace(new, old)
    elif name in ("spectro/icoms_ux.c", "spectro/icoms_nt.c") and "CM: passive catalog" in text:
        typename, count = ("devType", 1) if name.endswith("_nt.c") else ("instType", 2)
        new = f"{typename} itype = instUnknown; /* CM: passive catalog */"
        if text.count(new) != count:
            raise ValueError(f"Missing or ambiguous passive serial patch: {name}")
        original = text.replace(new, f"{typename} itype = fast_ser_dev_type(icom, 0, NULL, NULL);")
        if name.endswith("_ux.c"):
            from native_argyll.driver_manifest import LINUX_SERIAL_OPEN_PROBE, SERIAL_PASSIVE_MARKER

            if original.count(SERIAL_PASSIVE_MARKER) != 1:
                raise ValueError("Linux passive enumeration patch is missing")
            original = original.replace(SERIAL_PASSIVE_MARKER, LINUX_SERIAL_OPEN_PROBE)
    elif (name == "spectro/usbio_w0.c"
          and "CM: enumeration does not change kernel driver logging" in text):
        from native_argyll.driver_manifest import USB_PASSIVE_MARKER, WINDOWS_USB_DEBUG_WRITE

        if text.count(USB_PASSIVE_MARKER) != 1:
            raise ValueError("Windows USB enumeration patch is missing")
        original = text.replace(USB_PASSIVE_MARKER, WINDOWS_USB_DEBUG_WRITE)
    else:
        original = text
    if sha256(original.encode()) != pins[name]:
        raise ValueError(f"Pinned source hash mismatch in no-termination proof: {name}")
    return original


def _function(text: str, signature: str, name: str) -> tuple[str, dict]:
    if text.count(signature) != 1:
        raise ValueError(f"Missing or ambiguous pinned function: {name}: {signature}")
    start = text.index(signature)
    # Pinned functions close at column zero. Hash authentication above makes
    # this a source extraction, not a general-purpose C parser.
    end = text.index("\n}\n", start) + 3
    body = text[start:end]
    line = text[:start].count("\n") + 1
    return f'#line {line} "{name}"\n{body}', {
        "path": name, "line": line, "signature": signature, "sha256": sha256(body.encode()),
    }


def _code(text: str) -> str:
    """Ignore strings/comments while retaining newline positions for inventory."""
    pattern = r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    return re.sub(pattern, lambda m: "\n" * m[0].count("\n"), text, flags=re.DOTALL)


def inert_imports(executable: Path, output: Path, report: dict,
                  command: Callable[[list[str], Path, dict], str]) -> list[str]:
    """Refuse non-inert calls before executing a source-extracted probe.

    Probes pass their object file, so compiler runtime startup imports cannot
    hide a missed OS hook in a platform-specific executable's CRT.
    """
    listed = command(["nm", "-u", str(executable)], output, report).splitlines()
    imports = []
    for line in listed:
        if not line.strip():
            continue
        name = line.split()[-1].split("@", 1)[0]
        if platform.system() == "Darwin" and name.startswith("_"):
            name = name[1:]
        elif platform.system() == "Windows" and name.startswith("__imp_"):
            name = name[len("__imp_"):]
        imports.append(name)
    if not imports:
        raise RuntimeError(f"No imports listed for probe {executable.name}")
    imports = sorted(set(imports))
    if unexpected := sorted(set(imports) - INERT_IMPORTS):
        raise RuntimeError(f"Probe {executable.name} imports unsubstituted calls: {unexpected}")
    return imports


def _termination_sites(originals: dict[str, str]) -> list[dict]:
    result = []
    pattern = r"\b(?:kkill_nprocess|kill_nprocess|TerminateProcess|kill)\s*\("
    for name, text in originals.items():
        for match in re.finditer(pattern, _code(text)):
            result.append({
                "path": name, "line": _code(text)[:match.start()].count("\n") + 1,
                "symbol": match[0].split("(")[0].strip(),
            })
    # All vendor-termination helpers and call sites are pinned. Ordinary serial
    # and USB-as-serial transports must not introduce another bypass.
    expected = {"spectro/conv.c": 7, "spectro/usbio_ox.c": 1, "spectro/hidio.c": 1}
    actual = {name: sum(s["path"] == name for s in result) for name in expected}
    if actual != expected or any(s["path"] not in expected for s in result):
        raise ValueError("Pinned service-termination source inventory changed")
    return result


def _driver_inventory(source: Path, originals: dict[str, str]) -> list[dict]:
    """Audit all public serial/USB/HID driver groups for direct process calls."""
    names = {"spectro/rspec.c"}
    for group in ("SER_INSTS", "USB_INSTS", "FAST_SER_INSTS", "SER_USB_INSTS"):
        match = re.findall(r"^" + group + r"\s*=\s*([^;]+);", originals["spectro/Jamfile"], re.M)
        if len(match) != 1:
            raise ValueError(f"Pinned driver group is missing or ambiguous: {group}")
        names.update("spectro/" + name for name in match[0].split())
    result = []
    pattern = (
        r"\b(?:kkill_nprocess|kill_nprocess|TerminateProcess|kill|system|popen|"
        r"execl|execv|execve|execvp|fork)\s*\("
    )
    for name in sorted(names):
        text = (source / name).read_text()
        hits = re.findall(pattern, _code(text))
        if hits != (["kill_nprocess("] if name == "spectro/hidio.c" else []):
            raise ValueError(f"Unexpected direct process-control route in driver group: {name}")
        result.append({"path": name, "sha256": sha256(text.encode()), "direct_process_calls": hits})
    return result


def prove_no_termination(
    source: Path, output: Path, report: dict, cc: str, command: Callable,
) -> dict:
    """Run only substituted probes; source must already have both shared patches.

    ``command(argv, output, report)`` is the checker's existing retained-log
    runner. No command here links the upstream library or platform frameworks.
    No executed code enumerates processes, devices, or calibration caches.
    """
    pins = composed_pins(report)
    texts = {name: (source / name).read_text() for name in PINNED_SHA256}
    originals = {name: _original(text, name, pins)
                 for name, text in texts.items()}
    sites = _termination_sites(originals)
    driver_inventory = _driver_inventory(source, originals)
    extracts = []

    def functions(selections: list[tuple[str, str]], patched: bool) -> str:
        parts = []
        for name, signature in selections:
            # The driver always uses its original process list: an independent
            # positive control, not the earlier i1Pro3-only call-site patch.
            text = texts[name] if patched and name != "spectro/i1pro3.c" else originals[name]
            body, entry = _function(text, signature, name)
            entry["variant"] = "patched" if patched else "baseline"
            extracts.append(entry)
            parts.append(body)
        return "\n".join(parts)

    def probe(label: str, template: str, selections: list[tuple[str, str]]) -> dict:
        if template.count("/* CM_UPSTREAM_FUNCTIONS */") != 1:
            raise ValueError("No-termination probe template marker is missing or ambiguous")
        pair = {}
        for variant, patched in (("baseline", False), ("patched", True)):
            unit = output / f"no-termination-{label}-{variant}.c"
            executable = output / native_worker_name(unit.stem)
            obj = unit.with_suffix(".o")
            generated = template.replace(
                "/* CM_UPSTREAM_FUNCTIONS */", functions(selections, patched)
            )
            # Reject stale/overwritten proof files independently of outer runner.
            with unit.open("x") as stream:
                stream.write(generated)
            command([
                cc, "-std=gnu99", "-O1", f"-DCM_PATCHED={int(patched)}",
                "-c", str(unit), "-o", str(obj),
            ], output, report)
            imports = inert_imports(obj, output, report, command)
            command([cc, str(obj), "-o", str(executable)], output, report)
            result = json.loads(command([str(executable)], output, report))
            if result.get("patched") is not patched or not result.get("cases"):
                raise RuntimeError("Probe returned the wrong variant or no cases")
            result.update(
                imports=imports,
                generated_source_sha256=sha256(unit.read_bytes()),
                executable_sha256=sha256(executable.read_bytes()),
            )
            pair[variant] = result
        if any(case["termination_calls"] for case in pair["patched"]["cases"]):
            raise RuntimeError(f"Shared transport patch allowed termination: {label}")
        if label != "serial-unix" and not any(
            case["termination_calls"] for case in pair["baseline"]["cases"]
        ):
            raise RuntimeError(f"Termination positive control was not exercised: {label}")
        def without_termination(cases):
            return [
                {key: value for key, value in case.items() if key != "termination_calls"}
                for case in cases
            ]
        if without_termination(pair["baseline"]["cases"]) != without_termination(
            pair["patched"]["cases"]
        ):
            raise RuntimeError(f"Transport outcomes or cleanup changed between A/B probes: {label}")
        return pair

    results = {}
    usb_template = (NATIVE / "no_kill_stubs.c").read_text()
    for driver, service in (
        ("i1pro", "i1ProDeviceService"),
        ("i1pro3", "i1ProDeviceService"),
        ("munki", "ninjad"),
    ):
        template = usb_template.replace("i1pro3", driver).replace("i1ProDeviceService", service)
        results[f"usb-macos-{driver}"] = probe(f"usb-macos-{driver}", template, [
            ("spectro/usbio_ox.c", "static int usb_open_port("),
            ("spectro/usbio.c", "static int\nicoms_set_usb_port("),
            (f"spectro/{driver}.c", f"static inst_code\n{driver}_init_coms("),
        ])
    results["hid-windows-i1d3"] = probe(
        "hid-windows-i1d3", (NATIVE / "no_termination_hid_stubs.c").read_text(), [
            ("spectro/hidio.c", "static int hid_open_port("),
            ("spectro/hidio.c", "static int\nicoms_set_hid_port("),
            ("spectro/i1d3.c", "static inst_code\ni1d3_init_coms("),
        ],
    )
    results["serial-unix"] = probe(
        "serial-unix", (NATIVE / "no_termination_serial_stubs.c").read_text(), [
            ("spectro/icoms_ux.c", "static int\nicoms_set_ser_port_ex("),
            ("spectro/icoms_ux.c", "static int\nicoms_set_ser_port("),
        ],
    )
    return {
        "status": "passed",
        "scope": "source flow with substituted OS/device hooks; no hardware or OS ABI proof",
        "shared_patches": list(PATCHES),
        "source_sha256": {name: sha256(text.encode()) for name, text in texts.items()},
        "original_source_sha256": dict(PINNED_SHA256),
        "composed_source_sha256": {name: pins[name] for name in PINNED_SHA256
                                   if pins[name] != PINNED_SHA256[name]},
        "termination_sites": sites,
        "public_driver_source_inventory": driver_inventory,
        "extracted_functions": extracts,
        "probes": results,
        "serial_scope": "UNIX set_ser_port/ex executed with stubs; UNIX/Windows source inventory",
        "other_usb_scope": "Linux/BSD/Windows source inventory only; no build/runtime proof",
    }
