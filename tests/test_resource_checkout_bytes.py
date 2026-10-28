"""Authenticated resources must survive Git clean filters without byte changes."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOTS = (
    "src/color_measurement/data/illuminants",
    "src/color_measurement/desktop_assets/resources",
)


@pytest.mark.parametrize("autocrlf", ["false", "true", "input"])
def test_resource_attributes_preserve_publisher_bytes(tmp_path, autocrlf):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    attributes = ROOT.joinpath(".gitattributes").read_text()
    tmp_path.joinpath(".gitattributes").write_text(attributes)

    def filtered(path, payload, *, raw=False):
        args = ["git", "-C", str(tmp_path), "-c", f"core.autocrlf={autocrlf}",
                "hash-object", "--stdin"]
        args += ["--no-filters"] if raw else ["--path", path]
        return subprocess.check_output(args, input=payload)

    crlf_resource = None
    for folder in RESOURCE_ROOTS:
        for path in sorted(ROOT.joinpath(folder).iterdir()):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT).as_posix()
            payload = path.read_bytes()
            assert filtered(relative, payload) == filtered(relative, payload, raw=True), relative
            if b"\r\n" in payload:
                crlf_resource = (relative, payload)
    assert crlf_resource is not None, "The checksum-bound CRLF inputs must exercise this check"
    # Demonstrate that the inherited generic LF rule corrupts these real bytes.
    tmp_path.joinpath(".gitattributes").write_text("* text=auto eol=lf\n")
    path, payload = crlf_resource
    assert filtered(path, payload) != filtered(path, payload, raw=True)
