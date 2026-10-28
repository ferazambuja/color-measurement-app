"""Read-only freshness checks shared by desktop launch, generation and packaging."""

import hashlib
import json

from .json_values import unique_object

_STALE_RENDERER = "Bundled renderer is stale; regenerate desktop renderer assets"


def compiler_identity(sources):
    """Bind the compiler receipt to its existing locked npm origin."""
    builder = sources.parent
    try:
        identity = json.loads((builder / "compiler-identity.json").read_text())
        locked = json.loads((builder / "package-lock.json").read_text())["packages"][
            "node_modules/" + identity["package"]
        ]
        matches = (
            identity["version"] == "0.28.2"
            and identity["version"] == locked["version"]
            and identity["archive_url"] == locked["resolved"]
            and identity["archive_integrity"] == locked["integrity"]
            and identity["binary_path"] == "bin/esbuild"
            and len(identity["binary_sha256"]) == 64
        )
    except (KeyError, TypeError) as error:
        raise ValueError("Compiler receipt or locked npm origin is malformed") from error
    if not matches:
        raise ValueError("Compiler receipt differs from the locked npm origin")
    return identity


def renderer_sources(assets):
    """Locate the renderer sources beside a source checkout.

    The package ships only the compiled bundle; its sources live under
    ``tools/desktop_builder/renderer`` and are absent from an installed wheel,
    where this returns None and only the bundle itself can be authenticated.
    """
    root = assets.parents[2]
    sources = root / "tools" / "desktop_builder" / "renderer"
    # A damaged checkout must not become an installed-wheel check merely because
    # its entrypoint or entire renderer directory disappeared.
    if assets.parent.parent.name == "src" or (root / "tools" / "desktop_builder").is_dir():
        if not (sources / "app.js").is_file():
            raise ValueError("Renderer source entrypoint is missing from this checkout")
        return sources
    return None


def renderer_identity(assets, sources):
    """Bind the bundled renderer to exact source inputs and output bytes."""
    return {
        "compiler": compiler_identity(sources),
        "format": "iife",
        "target": "safari17",
        "resources": nominal_guide_identity(assets),
        "inputs": {
            path.relative_to(sources).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(sources.rglob("*.js"))
        },
        "output_sha256": hashlib.sha256((assets / "workspace.js").read_bytes()).hexdigest(),
    }


def nominal_guide_identity(assets):
    """Validate the illustrative resource without creating scientific quantities."""
    from .chart_state import definition

    path = assets / "nominal-chart-guides.json"
    try:
        raw = path.read_bytes()
        packet = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(packet, dict) or set(packet) != {
            "format", "version", "scope", "encoding", "qualification", "guides"
        }:
            raise ValueError("Nominal guide packet fields changed")
        valid = (
            packet["format"] == "nominal-chart-location-guides"
            and type(packet["version"]) is int and packet["version"] == 1
            and packet["scope"] == "Private illustrative location guidance"
            and packet["encoding"] == "Source image RGB8 displayed as nominal CSS sRGB"
            and packet["qualification"] == (
                "Image encoding, lighting and specimen colorimetry are unqualified"
            )
            and isinstance(packet["guides"], dict)
            and set(packet["guides"]) == {"classic", "sg"}
        )
        if not valid:
            raise ValueError("Nominal guide scope or encoding changed")
        for kind, guide in packet["guides"].items():
            layout = definition(kind)
            if not isinstance(guide, dict) or set(guide) != {
                "rows", "columns", "source", "image_sha256", "image_size", "cells"
            }:
                raise ValueError("Nominal guide fields changed")
            valid = valid and type(guide["rows"]) is int and type(guide["columns"]) is int
            valid = valid and (guide["rows"], guide["columns"]) == (
                layout["rows"], layout["columns"]
            ) and isinstance(guide["cells"], list) and len(guide["cells"]) == len(layout["cells"])
            valid = valid and guide["source"] == (
                "Owner-supplied front-facing visual layout reference"
            ) and guide["image_size"] == [700, 350]
            valid = valid and isinstance(guide["image_sha256"], str)
            valid = valid and len(guide["image_sha256"]) == 64 and all(
                letter in "0123456789abcdef" for letter in guide["image_sha256"]
            )
            if not valid:
                raise ValueError("Nominal guide layout or source changed")
            for cell, canonical in zip(guide["cells"], layout["cells"]):
                if not isinstance(cell, dict) or set(cell) != {
                    "index", "coordinate", "rgb8", "source_xy"
                }:
                    raise ValueError("Nominal guide cell fields changed")
                valid = valid and type(cell["index"]) is int
                valid = valid and (cell["index"], cell["coordinate"]) == (
                    canonical["index"], canonical["coordinate"]
                ) and isinstance(cell["rgb8"], list) and len(cell["rgb8"]) == 3 and all(
                    type(value) is int and 0 <= value <= 255 for value in cell["rgb8"]
                ) and isinstance(cell["source_xy"], list) and len(cell["source_xy"]) == 2 and all(
                    type(value) in {int, float} and 0 <= value < 1 for value in cell["source_xy"]
                )
        if not valid:
            raise ValueError("Nominal guide layout or encoding changed")
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError("Nominal guide resource is missing or invalid") from error
    return {path.name: hashlib.sha256(raw).hexdigest()}


def check_renderer(assets, sources=None):
    """Refuse a changed bundle, and stale inputs whenever their sources are given.

    Without sources (an installed wheel) the bundle must be the one its manifest
    records. With sources (a checkout, the generator, the tests) every input must
    also hash to the recorded build input. No compiler is invoked.
    """
    manifest = assets / "workspace-manifest.json"
    if not manifest.is_file():
        raise ValueError(_STALE_RENDERER)
    try:
        recorded = json.loads(manifest.read_text())
    except ValueError as error:
        raise ValueError(_STALE_RENDERER) from error
    if (not isinstance(recorded, dict)
            or recorded.get("resources") != nominal_guide_identity(assets)):
        raise ValueError(_STALE_RENDERER)
    if sources is None:
        output = hashlib.sha256((assets / "workspace.js").read_bytes()).hexdigest()
        if not isinstance(recorded, dict) or recorded.get("output_sha256") != output:
            raise ValueError(_STALE_RENDERER)
    elif recorded != renderer_identity(assets, sources):
        raise ValueError(_STALE_RENDERER)


def reference_identity(assets):
    return {
        "inputs": {
            "science.py": hashlib.sha256((assets.parent / "science.py").read_bytes()).hexdigest(),
            "presentation.py": hashlib.sha256(
                (assets.parent / "presentation.py").read_bytes()
            ).hexdigest(),
            "resources/CIE_xyz_1931_2deg.csv": hashlib.sha256(
                (assets / "resources" / "CIE_xyz_1931_2deg.csv").read_bytes()
            ).hexdigest(),
        },
        "output_sha256": hashlib.sha256((assets / "reference-data.js").read_bytes()).hexdigest(),
    }


def check_reference(assets):
    manifest = assets / "reference-manifest.json"
    if not manifest.is_file() or json.loads(manifest.read_text()) != reference_identity(assets):
        raise ValueError("Bundled reference data is stale; regenerate desktop reference assets")
