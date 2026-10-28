"""Render the original spectrum-and-reading app icon with macOS AppKit.

The vector scene below is the only design source. SVG is the browser icon;
PNG and ICNS are native build assets. These colors are decorative branding,
not colorimetric values or a measured spectrum.
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

TILE = (64, 64, 896, 896, 196, "#181a1e")
BARS = (
    (232, 260, 88, 190, 28, "#6b65e8"),
    (350, 260, 88, 330, 28, "#38bad1"),
    (468, 260, 88, 448, 28, "#63ca85"),
    (586, 260, 88, 300, 28, "#efc15b"),
    (704, 260, 88, 178, 28, "#e477a9"),
)
CURVE = (
    ("M", (180, 342)),
    ("C", (310, 342, 332, 736, 496, 744)),
    ("C", (602, 750, 624, 342, 844, 342)),
)


def svg():
    shapes = []
    for x, y, width, height, radius, fill in (TILE, *BARS):
        shapes.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'rx="{radius}" fill="{fill}"/>'
        )
    curve = " ".join(command + " " + " ".join(map(str, points)) for command, points in CURVE)
    shapes.extend((
        '<path d="M 192 220 H 832" stroke="#626770" stroke-width="24" stroke-linecap="round"/>',
        f'<path d="{curve}" fill="none" stroke="#f4f5f7" '
        'stroke-width="36" stroke-linecap="round"/>',
        '<circle cx="496" cy="744" r="32" fill="#f4f5f7"/>',
    ))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">\n'
        '<title>Color Measurement</title>\n'
        '<g transform="translate(0 1024) scale(1 -1)">\n'
        + "\n".join(shapes) + '\n</g>\n</svg>\n'
    )


def png(size):
    from AppKit import (
        NSBezierPath,
        NSBitmapImageRep,
        NSCalibratedRGBColorSpace,
        NSColor,
        NSGraphicsContext,
        NSPNGFileType,
    )
    from Foundation import NSAffineTransform

    def color(value):
        channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        NSColor.colorWithSRGBRed_green_blue_alpha_(*channels, 1).set()

    bitmap = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, size, size, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
    )
    NSGraphicsContext.saveGraphicsState()
    try:
        NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap))
        transform = NSAffineTransform.transform()
        transform.scaleBy_(size / 1024)
        transform.concat()
        for x, y, width, height, radius, fill in (TILE, *BARS):
            color(fill)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((x, y), (width, height)), radius, radius
            ).fill()
        baseline = NSBezierPath.bezierPath()
        baseline.moveToPoint_((192, 220))
        baseline.lineToPoint_((832, 220))
        baseline.setLineWidth_(24)
        baseline.setLineCapStyle_(1)
        color("#626770")
        baseline.stroke()
        curve = NSBezierPath.bezierPath()
        for command, points in CURVE:
            if command == "M":
                curve.moveToPoint_(points)
            else:
                curve.curveToPoint_controlPoint1_controlPoint2_(
                    points[4:6], points[0:2], points[2:4]
                )
        color("#f4f5f7")
        curve.setLineWidth_(36)
        curve.setLineCapStyle_(1)
        curve.stroke()
        NSBezierPath.bezierPathWithOvalInRect_(((464, 712), (64, 64))).fill()
    finally:
        NSGraphicsContext.restoreGraphicsState()
    return bytes(bitmap.representationUsingType_properties_(NSPNGFileType, {}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Refuse stale assets without changing them")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    builder = root / "tools" / "desktop_builder"
    with tempfile.TemporaryDirectory(prefix="cm-icon-") as temporary:
        iconset = Path(temporary) / "AppIcon.iconset"
        iconset.mkdir()
        for logical in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                suffix = "@2x" if scale == 2 else ""
                path = iconset / f"icon_{logical}x{logical}{suffix}.png"
                path.write_bytes(png(logical * scale))
        icns = Path(temporary) / "app-icon.icns"
        subprocess.run([
            "/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(icns)
        ], check=True)
        outputs = {
            root / "src" / "color_measurement" / "desktop_assets" / "app-icon.svg":
                svg().encode("utf-8"),
            builder / "app-icon.png": (iconset / "icon_512x512@2x.png").read_bytes(),
            builder / "app-icon.icns": icns.read_bytes(),
        }
        if arguments.check:
            stale = [str(path.relative_to(root)) for path, payload in outputs.items()
                     if not path.is_file() or path.read_bytes() != payload]
            if stale:
                raise SystemExit(
                    "App icon assets differ from their vector source: " + ", ".join(stale)
                )
            print("App icon SVG/PNG/ICNS match their vector source; no asset writes")
        else:
            for path, payload in outputs.items():
                path.write_bytes(payload)


if __name__ == "__main__":
    main()
