"""Source-bound i1Display 3 and Klein K-10 display-delay timing proof."""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import display_timing as timing  # noqa: E402
from native_argyll.proof_inputs import before_source  # noqa: E402
from native_argyll.spyder_calibration_slots import _function, digest  # noqa: E402


@pytest.fixture(scope="module")
def sources(argyll_archive, tmp_path_factory):
    source, _ = native.prepare(argyll_archive, tmp_path_factory.mktemp("display-timing") / "build",
                               contribution=native.NATIVE / "contributions/cr_pr")
    return source, {path: (source / path).read_text() for path in timing.FUNCTIONS}


def test_current_delay_methods_use_elapsed_time_and_count_the_last_sample(argyll_archive, sources):
    _, current = sources
    for path, name in timing.FUNCTIONS.items():
        before = _function(before_source("display_timing", path, argyll_archive), name)
        after = _function(current[path], name)
        assert "cutime > NDMXTIME" in before and "ndsamps = i;" in before
        assert "cutime - sutime > NDMXTIME" in after
        assert "ndsamps = i < NDSAMPS ? i + 1 : i;" in after


def test_archive_refusal_precedes_outputs(tmp_path):
    archive = tmp_path / "other.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        timing.prove(archive, output)
    assert not output.exists()


def test_actual_methods_on_scripted_clock_and_samples(argyll_archive, tmp_path, sources):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    output = tmp_path / "proof"
    production, current = sources
    report = timing.prove(argyll_archive, output, sanitize=True, source=production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report["sanitize"] is True
    assert report["baseline_klein_aged_out_of_bounds"] is True
    assert report["results"]["patched"]["kleink10"]["future_stamp"][0] == 3
    assert report["results"]["patched"]["kleink10"]["future_stamp"][2:6] == (1, 1, 7, 1)
    assert report["input_sha256"] == {
        path: digest(before_source("display_timing", path, argyll_archive).encode())
        for path in timing.FUNCTIONS}
    assert report["patched_sha256"] == {path: digest(text.encode())
                                        for path, text in current.items()}
    assert report["results"]["patched"]["i1d3"]["control"][:2] == (0, 400)
    assert report["results"]["patched"]["kleink10"]["control"][:2] == (0, 40)
    assert report["results"]["baseline"]["i1d3"]["aged"][:2] == (3, 1)
    assert report["results"]["patched"]["i1d3"]["aged"][:2] == (0, 400)
    assert report["results"]["patched"]["kleink10"]["aged"][:2] == (0, 40)
    assert report["results"]["patched"]["kleink10"]["single"][:2] == (3, 1)
    assert report["results"]["patched"]["kleink10"]["zero"][:2] == (3, 40)
    assert report["results"]["baseline"]["kleink10"]["flat_zero"][0] == 0
    assert report["results"]["patched"]["kleink10"]["flat_zero"][0] == 4
    assert report["results"]["baseline"]["kleink10"]["missing_marker"][0] == 0
    assert report["results"]["patched"]["kleink10"]["missing_marker"][0] == 4
    assert report["results"]["baseline"]["kleink10"]["reversed_negative"][0] == 0
    assert report["results"]["patched"]["kleink10"]["reversed_negative"][0] == 4
    assert report["results"]["baseline"]["i1d3"]["overflow_sum"][0] == 0
    assert report["results"]["patched"]["i1d3"]["overflow_sum"][0] == 4
    # The only case that reaches the sec/tot guard's exit, so its restore is checked here.
    assert report["results"]["patched"]["i1d3"]["overflow_sum"][2:6] == (0, 0, 7, 1)
    for driver in ("i1d3", "kleink10"):
        for case in ("nan_sample", "nan_unused", "inf_sample"):
            assert report["results"]["baseline"][driver][case][0] == 0
            assert report["results"]["patched"][driver][case][0] == 4
            assert report["results"]["patched"][driver][case][2:6] == (
                (0, 0, 7, 1) if driver == "i1d3" else (1, 1, 7, 1))
        for case in ("nan_clock", "nan_stamp", "old_stamp"):
            assert report["results"]["patched"][driver][case][0] == 3
            assert report["results"]["patched"][driver][case][2:6] == (
                (0, 0, 7, 1) if driver == "i1d3" else (1, 1, 7, 1))
    assert (output / "i1d3.patch").is_file()
    assert (output / "kleink10.patch").is_file()
    with pytest.raises(FileExistsError):
        timing.prove(argyll_archive, output, source=production)


@pytest.mark.parametrize(("path", "driver", "case", "mutation", "expected"), [
    (timing.I1D3, "i1d3", "aged",
     ("cutime - sutime > NDMXTIME", "cutime > NDMXTIME"), 4),
    (timing.I1D3, "i1d3", "last",
     ("ndsamps = i < NDSAMPS ? i + 1 : i;", "ndsamps = i;"), 4),
    (timing.KLEIN, "kleink10", "last",
     ("ndsamps = i < NDSAMPS ? i + 1 : i;", "ndsamps = i;"), 4),
], ids=["elapsed-clock", "i1d3-last-count", "klein-last-count"])
def test_deliberate_regressions_change_actual_method(argyll_archive, sources, tmp_path,
                                                     path, driver, case, mutation, expected):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    _, current = sources
    patched = dict(current)
    old, new = mutation
    function = _function(patched[path], timing.FUNCTIONS[path])
    assert function.count(old) == 1
    patched[path] = patched[path].replace(function, function.replace(old, new))
    executable = timing._compile(patched, tmp_path / "mutant", "cc", True)
    observed = timing._run(executable, driver, case, tmp_path / "mutant")
    assert observed[0] == expected
    if case == "aged":
        assert observed[1] == 1
    if driver == "kleink10":
        # Both return success, but the old count discards the last real sample.
        correct = timing._compile(current, tmp_path / "correct", "cc", True)
        assert observed[6:8] != timing._run(correct, driver, case, tmp_path / "correct")[6:8]


# The sanitized baseline must both fail and name the samp[-1] read. Either alone is
# another fault, so each one-sided outcome is refused, not only a clean exit.
@pytest.mark.parametrize("returncode,stderr", [
    (0, ""),
    (1, "AddressSanitizer: heap-buffer-overflow"),
    (0, "runtime error: index -1 out of bounds for type 'double [40]'"),
])
def test_baseline_aged_receipt_requires_observed_sanitizer_failure(
        argyll_archive, tmp_path, monkeypatch, sources, returncode, stderr):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    import subprocess

    actual_run = timing.subprocess.run

    def missed_fault(argv, **kwargs):
        if (len(argv) == 3 and Path(argv[0]).stem == "probe"
                and Path(argv[0]).parent.name == "baseline"
                and argv[1:] == ["kleink10", "aged"]):
            return subprocess.CompletedProcess(argv, returncode, "", stderr)
        return actual_run(argv, **kwargs)

    monkeypatch.setattr(timing.subprocess, "run", missed_fault)
    with pytest.raises(AssertionError, match="baseline first-read out-of-bounds not reproduced"):
        timing.prove(argyll_archive, tmp_path / "missed", sanitize=True, source=sources[0])


def test_spacing_guard_mutation_exposes_undefined_behavior(sources, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    _, current = sources
    patched = dict(current)
    path = timing.KLEIN
    function = _function(patched[path], timing.FUNCTIONS[path])
    guard = ("\tif (ndsamps < 2) {\n"
             "\t\ta1logd(p->log, 1, \"k10_meas_delay: Too few measurement "
             "samples for spacing\\n\");\n"
             "\t\treturn inst_internal_error; \n\t}\n\n")
    assert function.count(guard) == 1
    patched[path] = patched[path].replace(function, function.replace(guard, ""))
    executable = timing._compile(patched, tmp_path / "mutant", "cc", True,
                                 ("-fsanitize=float-divide-by-zero",))
    import subprocess

    run = subprocess.run([str(executable), "kleink10", "single"], capture_output=True,
                         text=True, timeout=10)
    assert run.returncode != 0
    assert "runtime error:" in run.stderr or "ERROR: AddressSanitizer" in run.stderr


@pytest.mark.parametrize(("path", "driver", "case", "changes", "result"), [
    (timing.KLEIN, "kleink10", "reversed_negative",
     (("&& del > 0.0 && del <= DBL_MAX", "&& del <= DBL_MAX"),), "published"),
    (timing.KLEIN, "kleink10", "missing_marker",
     (("if (i == ndsamps)\n\t\treturn inst_misread;", ""),), "sanitizer"),
    (timing.KLEIN, "kleink10", "nan_unused",
     (("\t\tif (!(samp[i].xyz[0] >= -DBL_MAX && samp[i].xyz[0] <= DBL_MAX\n"
       "\t\t   && samp[i].xyz[1] >= -DBL_MAX && samp[i].xyz[1] <= DBL_MAX\n"
       "\t\t   && samp[i].xyz[2] >= -DBL_MAX && samp[i].xyz[2] <= DBL_MAX)) {\n"
       "\t\t\tamutex_unlock(p->lock);\n\t\t\tp->log->debug = isdeb;\n"
       "\t\t\treturn inst_misread;\n\t\t}\n", ""),), "published"),
    (timing.I1D3, "i1d3", "overflow_sum",
     (("\t\tif (!(samp[i].sec >= -DBL_MAX && samp[i].sec <= DBL_MAX\n"
       "\t\t   && samp[i].tot >= -DBL_MAX && samp[i].tot <= DBL_MAX)) {\n"
       "\t\t\tp->log->debug = isdeb;\n\t\t\tp->th_en = isth;\n"
       "\t\t\treturn inst_misread;\n\t\t}\n", ""),), "published"),
    (timing.I1D3, "i1d3", "old_stamp",
     (("if (!(delayms >= (double)INT_MIN && delayms <= (double)INT_MAX))\n"
       "\t\treturn inst_internal_error;", ""),), "sanitizer"),
    (timing.KLEIN, "kleink10", "old_stamp",
     (("if (!(delayms >= (double)INT_MIN && delayms <= (double)INT_MAX))\n"
       "\t\treturn inst_internal_error;", ""),), "sanitizer"),
    (timing.KLEIN, "kleink10", "future_stamp",
     (("if (dispmsec < INT_MIN - instmsec)\n\t\treturn inst_internal_error;", ""),),
     "sanitizer"),
], ids=["positive-contrast", "missing-marker", "nonfinite-xyz",
        "nonfinite-sum", "i1d3-integer-range", "klein-integer-range",
        "klein-lookahead-range"])
def test_transition_mutations_expose_original_failures(sources, tmp_path, path, driver,
                                                       case, changes, result):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    _, current = sources
    patched = dict(current)
    function = _function(patched[path], timing.FUNCTIONS[path])
    mutant = function
    for old, new in changes:
        assert mutant.count(old) == 1
        mutant = mutant.replace(old, new)
    patched[path] = patched[path].replace(function, mutant)
    executable = timing._compile(patched, tmp_path / "mutant", "cc", True)
    import subprocess

    run = subprocess.run([str(executable), driver, case], capture_output=True,
                         text=True, timeout=10)
    if result == "published":
        assert run.returncode == 0
        assert run.stdout.split()[0] == "0"
    else:
        assert run.returncode != 0
        assert "runtime error:" in run.stderr or "ERROR: AddressSanitizer" in run.stderr
