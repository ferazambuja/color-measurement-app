"""Shared dark calibration must preserve each selected exposure/bank pairing."""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from native_argyll import munki_dark_copy as proof  # noqa: E402

# Independent regression anchors retained from the authenticated repair evidence.
HEAD = "\t\t\t\t && ss->dark_int_time == s->dark_int_time\n"
NEW = "ss->dark_int_time3 = s->dark_int_time3;"
OLD = "ss->dark_int_time3 = s->dark_int_time2;"
QUANTIZED_HEAD = (
    "\t\t\t\t /* Compare the hardware integration counts used by the trigger. */\n"
    "\t\t\t\t && floor(ss->dark_int_time / m->intclkp + 0.5)\n"
    "\t\t\t\t    == floor(s->dark_int_time / m->intclkp + 0.5)\n"
)


def test_sharing_preserves_banks_and_required_calibration(argyll_archive, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler required for the inert native proof")
    report = proof.prove(argyll_archive, tmp_path / "proof", sanitize=True)
    assert report["status"] == "passed"
    assert report["results"]["patched"]["checks"] == 375
    assert report["results"]["patched"]["failures"] == 0
    assert report["results"]["baseline"] == {"checks": 354, "failures": 41, "tripwires": 0}


@pytest.mark.parametrize(
    "old,new",
    [
        ("s->dispswap == 0 && ", ""),
        (" && ss->dispswap == 0", ""),
        (NEW, OLD),
        (QUANTIZED_HEAD, HEAD),
        (QUANTIZED_HEAD, "\t\t\t\t && fabs(ss->dark_int_time - s->dark_int_time) < 0.01\n"),
    ],
)
def test_each_regressed_sharing_rule_is_caught(
    argyll_archive, tmp_path, monkeypatch, old, new, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler required for the inert native proof")

    def regressed(text):
        repaired = text
        assert repaired.count(old) == 1
        return repaired.replace(old, new)

    source = mutate_production([proof.SOURCE_PATH], lambda text, path: regressed(text))
    with pytest.raises(AssertionError, match="shared-dark transitions failed"):
        proof.prove(argyll_archive, tmp_path / "proof", source=source)


def _baseline_trace():
    names = list(proof.BASELINE_FAILURES.elements())
    rows = [f"  [{case}] {check} expected 1 actual 0 FAIL" for case, check in names]
    rows.extend(
        "  [control] unchanged check expected 1 actual 1 PASS" for _ in range(354 - len(rows))
    )
    return "\n".join(rows)


@pytest.mark.parametrize(
    "fault",
    [
        "unrelated",
        "missing",
        "duplicate",
        "count",
        "trace-missing",
        "trace-extra",
        "tripwire",
        "exit",
    ],
)
def test_baseline_requires_exact_dark_bank_failures(fault):
    row = {"checks": 354, "failures": 41, "tripwires": 0}
    trace = _baseline_trace()
    returncode = 1
    if fault == "unrelated":
        trace = trace.replace(
            "canonical third exposure preserved", "unrelated allocation failure", 1
        )
    elif fault == "missing":
        trace = trace.replace("actual 0 FAIL", "actual 1 PASS", 1)
        row["failures"] -= 1
    elif fault == "duplicate":
        lines = trace.splitlines()
        index = next(i for i, line in enumerate(lines) if line != lines[0])
        lines[index] = lines[0]
        trace = "\n".join(lines)
    elif fault == "count":
        row["failures"] += 1
    elif fault == "trace-missing":
        trace = "\n".join(trace.splitlines()[:-1])
    elif fault == "trace-extra":
        trace += "\n  [control] extra check expected 1 actual 1 PASS"
    elif fault == "tripwire":
        row["tripwires"] = 1
    elif fault == "exit":
        returncode = 0
    with pytest.raises(AssertionError):
        proof._validate_result("baseline", row, trace, returncode)


def test_exact_baseline_trace_and_clean_patched_trace():
    proof._validate_result(
        "baseline", {"checks": 354, "failures": 41, "tripwires": 0}, _baseline_trace(), 1
    )
    trace = "\n".join("  [control] unchanged check expected 1 actual 1 PASS" for _ in range(375))
    proof._validate_result("patched", {"checks": 375, "failures": 0, "tripwires": 0}, trace, 0)
