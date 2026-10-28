"""Regenerate original target packets and expected illustrations without native helpers."""

import argparse
from pathlib import Path

from color_measurement.chart_targets import expected_reference_json, import_ti2_json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/targets/asymmetric-six-patch.ti2"
LAB_SOURCE = SOURCE.with_name("explicit-lab-six-patch.ti2")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    targets = {source: import_ti2_json(str(source)) for source in (SOURCE, LAB_SOURCE)}
    outputs = {
        source.with_suffix(".target.json"): target + "\n" for source, target in targets.items()
    }
    outputs[LAB_SOURCE.with_suffix(".expected.json")] = (
        expected_reference_json(
            targets[LAB_SOURCE], LAB_SOURCE.with_suffix(".context.json").read_text()
        )
        + "\n"
    )
    outputs[LAB_SOURCE.with_suffix(".unavailable.json")] = (
        expected_reference_json(targets[LAB_SOURCE]) + "\n"
    )
    for path, content in outputs.items():
        if args.check:
            if not path.is_file() or path.read_text() != content:
                raise SystemExit(
                    f"Target fixture differs: {path}; run tools/generate_target_fixtures.py"
                )
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
