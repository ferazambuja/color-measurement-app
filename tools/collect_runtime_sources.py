"""Download the pinned Qt/PySide Corresponding Source once, with digest verification.

This is an explicit release-preparation command. The Qt archive is large; reuse
one verified source set across target packages. No binary or instrument is run.
"""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def verify_sources(directory):
    origin = json.loads((ROOT / "tools/desktop_runtime_licenses/QT-ORIGIN.json").read_text())
    for item in origin["archives"]:
        path = Path(directory) / item["filename"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing runtime source: " + item["filename"])
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError("Runtime source digest differs: " + item["filename"])
    return origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--check", action="store_true", help="Verify existing sources without network"
    )
    args = parser.parse_args()
    if not args.check:
        args.directory.mkdir(parents=True, exist_ok=True)
        origin = json.loads((ROOT / "tools/desktop_runtime_licenses/QT-ORIGIN.json").read_text())
        for item in origin["archives"]:
            target = args.directory / item["filename"]
            if target.exists():
                continue  # The final verification below refuses changed existing bytes.
            descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=args.directory)
            try:
                with (
                    os.fdopen(descriptor, "wb") as stream,
                    urlopen(item["url"], timeout=60) as remote,
                ):
                    while block := remote.read(1024 * 1024):
                        stream.write(block)
                with open(temporary, "rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != item["sha256"]:
                    raise ValueError("Downloaded runtime source digest differs")
                # Same-directory hard linking publishes complete verified bytes
                # atomically and exclusively, without a second large source copy.
                os.link(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
    verify_sources(args.directory)
    print("Pinned Qt and PySide source archives verified; no binary execution")


if __name__ == "__main__":
    main()
