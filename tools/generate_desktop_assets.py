"""Generate bundled offline reference illustrations from the shared scientific owner."""

import argparse
import hashlib
import json
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from color_measurement import desktop_assets_manifest, presentation, science

#: Renderer sources are build inputs, kept beside the builder and out of the wheel.
SOURCES = Path(__file__).resolve().with_name("desktop_builder") / "renderer"


def generate_renderer(assets, sources, esbuild):
    identity = desktop_assets_manifest.compiler_identity(sources)
    builder = sources.parent
    launcher = builder / "node_modules" / "esbuild" / "bin" / "esbuild"
    binary = builder / "node_modules" / identity["package"] / identity["binary_path"]
    if esbuild.resolve() not in {launcher.resolve(), binary.resolve()}:
        raise ValueError("Use the compiler installed by the locked desktop builder")
    compiler = binary.read_bytes()
    if hashlib.sha256(compiler).hexdigest() != identity["binary_sha256"]:
        raise ValueError("Native compiler bytes differ from the authenticated npm archive")
    with tempfile.TemporaryDirectory(prefix="cm-renderer-") as directory:
        # Execute exactly the bytes that were hashed: a private copy in a
        # directory only this process can write, not the installed path, which
        # could be replaced between the hash and either execution. The npm
        # launcher's ESBUILD_BINARY_PATH override cannot choose another
        # executable here either.
        esbuild = Path(directory) / "esbuild"
        esbuild.write_bytes(compiler)
        esbuild.chmod(0o700)
        version = subprocess.check_output([str(esbuild), "--version"], text=True).strip()
        if version != identity["version"]:
            raise ValueError("The native compiler version differs from its authenticated receipt")
        output = Path(directory) / "workspace.js"
        subprocess.run(
            [str(esbuild), "app.js", "--bundle", "--format=iife", "--target=safari17",
             "--outfile=" + str(output)],
            cwd=sources, check=True,
        )
        (assets / "workspace.js").write_bytes(output.read_bytes())
    (assets / "workspace-manifest.json").write_text(
        json.dumps(desktop_assets_manifest.renderer_identity(assets, sources), indent=2) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--kind", choices=("reference", "renderer", "all"), default="all")
    parser.add_argument("--esbuild", type=Path)
    args = parser.parse_args()
    assets = Path(presentation.__file__).with_name("desktop_assets")
    if args.kind in {"renderer", "all"}:
        if args.check:
            desktop_assets_manifest.check_renderer(assets, SOURCES)
            print("Bundled renderer inputs/output match; no compiler or acquisition")
        else:
            if args.esbuild is None:
                parser.error("Renderer generation requires --esbuild from the locked npm tool")
            generate_renderer(assets, SOURCES, args.esbuild)
    if args.kind == "renderer":
        return
    xyz = dict(
        values=(20, 30, 40),
        unit="cd/m^2",
        scale="absolute; Y is luminance",
        observer="CIE1931_2",
        illuminant="none (emissive)",
    )
    scale = presentation.DEFAULT_PREVIEW_SCALE
    payload = dict(
        preview_scale=scale,
        reference_gamuts=presentation.reference_projections(scale),
        wavelength_guide=presentation.wavelength_guide(),
        spectral_locus=presentation.spectral_locus(),
        chromaticity_field=presentation.chromaticity_field(),
        standard_whites=[asdict(entry) for entry in science.standard_reference_catalog()],
        gamut_witness=dict(
            scope="Numerical example; no acquisition or display qualification",
            xyz=xyz["values"],
            scale=scale,
            previews=presentation.display_projections(
                {"example": {"observation": {"backend_reported": {"xyz": xyz}}}}, scale
            )["example"],
        ),
        readings={},
        eligible_white_sources=[],
        source_commit="installed-science",
        dataset_revision="empty",
        source_hashes={
            module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (science, presentation)
        },
    )
    target = Path(presentation.__file__).with_name("desktop_assets") / "reference-data.js"
    text = "const FIXTURES = " + json.dumps(payload, ensure_ascii=False) + ";\n"
    if args.check:
        if target.read_text() != text:
            raise SystemExit(
                "Bundled reference illustrations are stale; regenerate from current source"
            )
        desktop_assets_manifest.check_reference(assets)
        print("Bundled reference illustrations match current source; no acquisition")
    else:
        target.write_text(text)
        (assets / "reference-manifest.json").write_text(
            json.dumps(desktop_assets_manifest.reference_identity(assets), indent=2) + "\n"
        )
        print("Generated bundled reference illustrations; no acquisition")


if __name__ == "__main__":
    main()
