"""Generate offline demo colours with the existing synthetic shape/Argyll converter.

Only a colour calculator is compiled and run, never a worker or instrument.
The installed demo reads its packaged output and needs neither C nor Argyll.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/color_measurement/data/demo_palette.json"
NAMES = ["Cool white mix", "Warm white mix", "Neutral white mix", "Narrow blue",
         "Narrow green", "Narrow red", "Cyan mix", "Magenta mix", "Amber peak",
         "Broadband", "Phosphor-like white", "Low signal"]


def generate(source):
    templates = json.loads((ROOT / "src/color_measurement/data/demo.json").read_text())
    grids = {(36, 380, 730, 1)}  # Also supplies colours for XYZ-only personas.
    for persona in templates["personas"].values():
        for route in persona["routes"].values():
            for patch in route["patches"]:
                spectrum = patch["backend_reported"]["spectrum"]
                if spectrum:
                    grids.add(tuple(spectrum[k] for k in (
                        "spec_n", "wavelength_start_nm", "wavelength_end_nm", "norm")))
    shape_source = (ROOT / "tools/native_argyll/chart_stub.c").read_text()
    # Reuse the authoritative table and its entire calculator verbatim. No
    # second spectral algorithm, device constructor, session or transport code.
    start = shape_source.index("static const double shapes[12][3][3]")
    end = shape_source.index("\nstatic void fill(", start)
    calculator = shape_source[start:end]
    libraries = [source / p for p in ("numlib/numsup.c", "xicc/xspect.c",
                 "spectro/sa_conv.c", "cgats/cgats.c", "cgats/pars.c")]
    header = '\n'.join(f'#include "{name}"' for name in (
        "sa_config.h", "numsup.h", "cgats.h", "xspect.h", "insttypes.h",
        "conv.h", "icoms.h", "inst.h"))
    program = header + "\n#include <math.h>\n#include <stdlib.h>\n" + calculator + r'''
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    int bands = atoi(argv[1]); double first = atof(argv[2]), last = atof(argv[3]);
    for (int k = 0; k < 12; k++) {
        ipatch p = {0};
        cm_synthetic_reading(&p, k, atoi(argv[4]) == 1, first, last, bands);
        printf("%.17g %.17g %.17g", p.XYZ[0], p.XYZ[1], p.XYZ[2]);
        for (int j = 0; j < bands; j++) printf(" %.17g", p.sp.spec[j]);
        puts("");
    }
    return 0;
}
'''
    palette = {}
    with tempfile.TemporaryDirectory(prefix="demo-palette-") as temporary:
        folder = Path(temporary)
        shutil.copyfile(source / "h/aconfig.h", folder / "sa_config.h")
        (folder / "palette.c").write_text(program)
        includes = [folder] + [source / d for d in ("spectro", "numlib", "rspl",
                                                   "xicc", "cgats", "h")]
        link = ["-framework", "CoreFoundation", "-lobjc"] if sys.platform == "darwin" else []
        subprocess.run(["cc", "-std=gnu99", "-O1", "-DSALONEINSTLIB", "-DUNIX",
                        *[f"-I{p}" for p in includes], str(folder / "palette.c"),
                        *map(str, libraries), "-lm", *link,
                        "-o", str(folder / "palette")], check=True)
        for grid in sorted(grids):
            result = subprocess.run([str(folder / "palette"), *map(str, grid)],
                                    check=True, capture_output=True, text=True)
            rows = [list(map(float, line.split())) for line in result.stdout.splitlines()]
            if len(rows) != 12 or any(len(row) != 3 + grid[0] for row in rows):
                raise SystemExit("Palette calculator returned an invalid row or band count")
            palette[":".join(map(str, grid))] = [
                {"XYZ": row[:3], "samples": row[3:]} for row in rows]
    hashes = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in libraries}
    return json.dumps({"provenance": "simulated", "names": NAMES,
                       "calculator_sha256": hashlib.sha256(calculator.encode()).hexdigest(),
                       "argyll_source_sha256": hashes, "palettes": palette},
                      indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--argyll-source", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = generate(args.argyll_source)
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text() != content:
            raise SystemExit("Demo palette differs; regenerate it")
    else:
        OUTPUT.write_text(content)
