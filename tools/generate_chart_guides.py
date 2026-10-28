"""Read the supplied chart layout images into private nominal location guides.

This imports encoded image pixels, not measurement/reference values. The original
photographs stay outside the package. No white, XYZ, Lab or spectrum is inferred.
"""

import argparse
import hashlib
import json
from array import array
from collections import Counter
from pathlib import Path

from color_measurement.chart_state import definition

SOURCES = {
    "classic": {
        "sha256": "21067c9263c3563cc1b6309f8e88930c1bea7d2226657d08dff3675a037d619b",
        "x": (178, 249, 319, 390, 461, 532),
        "y": (73, 144, 213, 283),
    },
    "sg": {
        "sha256": "aa0d282f8c141739e0dd21b750dbcddaad9a8f6729cdeae10a09ce3b35ef7e13",
        "x": (178, 205, 232, 259, 286, 313, 340, 367, 394, 421, 448, 475, 502, 529),
        "y": (56, 83, 110, 136, 163, 190, 216, 243, 270, 296),
    },
}
# The SG image is dithered: a single center pixel can differ from its patch face
# by 14 levels per channel. Keep the most frequent exact RGB8 of a 5 by 5 window.
WINDOW = 2


def guide(kind, path):
    """Preserve each patch's most frequent source RGB8 under canonical structural IDs."""
    from AppKit import NSBitmapImageRep
    from Foundation import NSData

    source = SOURCES[kind]
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("Supplied layout image changed: " + kind)
    bitmap = NSBitmapImageRep.imageRepWithData_(NSData.dataWithBytes_length_(raw, len(raw)))
    if (bitmap is None or bitmap.bitsPerSample() != 8 or bitmap.samplesPerPixel() != 4
            or bitmap.pixelsWide() != 700 or bitmap.pixelsHigh() != 350):
        raise ValueError("Expected the retained 700 by 350 RGB8 layout image")
    layout = definition(kind)
    cells = []
    for cell in layout["cells"]:
        x, y = source["x"][cell["column"] - 1], source["y"][cell["row"] - 1]
        votes = Counter()
        for dy in range(-WINDOW, WINDOW + 1):
            for dx in range(-WINDOW, WINDOW + 1):
                pixel = array("L", [0] * 4)
                bitmap.getPixel_atX_y_(pixel, x + dx, y + dy)
                if pixel[3] != 255:
                    raise ValueError("Nominal patch window must be opaque")
                votes[tuple(pixel[:3])] += 1
        cells.append({
            "index": cell["index"], "coordinate": cell["coordinate"],
            "rgb8": list(votes.most_common(1)[0][0]), "source_xy": [x / 700, y / 350],
        })
    return {
        "rows": layout["rows"], "columns": layout["columns"],
        "source": "Owner-supplied front-facing visual layout reference",
        "image_sha256": source["sha256"], "image_size": [700, 350], "cells": cells,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classic-image", type=Path, required=True)
    parser.add_argument("--sg-image", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = {
        "format": "nominal-chart-location-guides", "version": 1,
        "scope": "Private illustrative location guidance",
        "encoding": "Source image RGB8 displayed as nominal CSS sRGB",
        "qualification": "Image encoding, lighting and specimen colorimetry are unqualified",
        "guides": {kind: guide(kind, path) for kind, path in (
            ("classic", args.classic_image), ("sg", args.sg_image)
        )},
    }
    path = (Path(__file__).resolve().parents[1]
            / "src/color_measurement/desktop_assets/nominal-chart-guides.json")
    encoded = json.dumps(payload, indent=2) + "\n"
    if args.check:
        if path.read_text() != encoded:
            raise ValueError("Nominal location resource differs from supplied images")
    else:
        path.write_text(encoded)
    print("Nominal location guides match supplied images; no measurement or reference data")


if __name__ == "__main__":
    main()
