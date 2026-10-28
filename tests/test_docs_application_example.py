"""The documented application route runs as written, against the Demo frontend."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_documented_complete_route_runs_as_written(tmp_path):
    text = (ROOT / "docs/application.md").read_text()
    section = text.split("## Run the complete route", 1)[1]
    code = re.search(r"```python\n(.*?)```", section, re.S).group(1)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=120,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # The route ends by printing the reviewed sample count of its one reading.
    assert result.stdout.rstrip().splitlines()[-1] == "1"
    assert (tmp_path / "demo-batch" / "batch.json").is_file()
