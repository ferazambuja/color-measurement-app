"""Every tracked example still runs.

The examples are the only executable prose in the tree: they are what a new
client copies, and nothing else imports them. Deleting a module therefore
breaks them silently -- lint does not resolve imports and no test imported
them, so `examples/i1pro_family.py` stayed lint-clean for a day while importing
a package module that had been deleted.

Discovery is by glob rather than a list, so an example added later is covered
without anyone remembering to add it here. Each runs as its own process with no
arguments: an example that cannot be run that way is not a runnable example.
No instrument is involved; all examples are explicit about substituting one.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted(path.name for path in (ROOT / "examples").glob("*.py"))


def test_every_tracked_example_is_discovered():
    # Without this the parametrisation below can quietly become empty, which is
    # the failure it exists to prevent.
    assert EXAMPLES, "no examples found to run"


@pytest.mark.parametrize("example", EXAMPLES)
def test_an_example_runs_to_completion(example):
    completed = subprocess.run(
        [sys.executable, str(ROOT / "examples" / example)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"{example} exited {completed.returncode}\n{completed.stderr[-2000:]}"
    )
