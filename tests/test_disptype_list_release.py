"""Owned display-list release; the actual list helpers and destructors, no hardware."""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import disptype_list_release as proof  # noqa: E402
from native_argyll import spyder_calibration_slots as slots  # noqa: E402
from native_argyll.proof_inputs import before_source  # noqa: E402

RELEASE = "inst_del_disptype_list(p->dtlist, p->ndtlist);"


@pytest.fixture(scope="module")
def inputs(argyll_archive):
    return {name: before_source("disptype_list_release", name, argyll_archive)
            for name in proof.DRIVERS}


@pytest.fixture(scope="module")
def production(argyll_archive, tmp_path_factory):
    source, _ = native.prepare(argyll_archive, tmp_path_factory.mktemp("display-release") / "build",
                               contribution=native.NATIVE / "contributions/cr_pr")
    return source


def test_unknown_source_and_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_current_destructors_release_the_owned_list_before_destroying_the_object(
        inputs, production):
    for name in proof.DRIVERS:
        before = inputs[name]
        after = (production / name).read_text()
        destructor = proof.DESTRUCTORS[name]
        old, new = slots._function(before, destructor), slots._function(after, destructor)
        assert RELEASE not in old and new.count(RELEASE) == 1
        # After the transport is released and before the virtual destructor and the object.
        lines = new.splitlines()
        at, = [i for i, line in enumerate(lines) if RELEASE in line]
        assert "p->icom->del(p->icom);" in lines[at - 1] or "amutex_del(p->lock);" in lines[at - 1]
        assert lines[at + 1].strip() == "p->vdel(pp);"
        assert "free(p);" in lines[at + 2]


def _check(report, output, inputs, production):
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    assert report["patched_sha256"] == {
        name: proof.digest((production / name).read_bytes()) for name in proof.DRIVERS}
    assert report["input_sha256"] == {
        name: proof.digest(text.encode()) for name, text in inputs.items()}
    for driver in proof.DRIVERS.values():
        baseline, patched = (report["results"][variant][driver] for variant in
                             ("baseline", "patched"))
        expected = proof.EXPECTED["baseline"][driver]
        # The original leaves every list it built: the array, plus a moved path per
        # matching installed correction. Deleting before any list exists leaves nothing.
        assert set(baseline["failed_scenarios"].split(",")) == expected["failed"]
        assert baseline["left"] == {name: proof.LEFT[name] for name in baseline["left"]}
        assert baseline["left"]["del_only"] == 0 and baseline["left"]["ccmx"] == 8
        # The repaired destructor leaves nothing, with the same checks and commands.
        assert (patched["failures"], patched["failed_scenarios"], patched["returncode"]) == (
            0, "", 0)
        assert set(patched["left"].values()) == {0}
        assert patched["checks"] == baseline["checks"] == expected["checks"]
        assert patched["commands"] == baseline["commands"]
        assert patched["untracked_frees"] == baseline["untracked_frees"] == 0
        # Recorded selection summaries are identical: the destructor is the change.
        assert patched["state"] == baseline["state"]
        assert "ccmx:n=" in patched["state"] and "init:n=" in patched["state"]
    # Reuse the actual observations to isolate each result and state validator.
    for variant in ("baseline", "patched"):
        row = report["results"][variant]["kleink10"]
        mutations = {
            "driver": "other", "checks": row["checks"] + 1,
            "failures": row["failures"] + 1, "failed_scenarios": "other",
            "left": {**row["left"], "ccmx": row["left"]["ccmx"] + 1},
            "untracked_frees": 1,
        }
        for field, value in mutations.items():
            changed = copy.deepcopy(row)
            changed[field] = value
            with pytest.raises(AssertionError, match=f"{variant}/kleink10"):
                proof._validate_result(variant, "kleink10", changed, "", row["returncode"])
        with pytest.raises(AssertionError, match=f"{variant}/kleink10"):
            proof._validate_result(variant, "kleink10", row, "", row["returncode"] + 1)
    patched = report["results"]["patched"]["kleink10"]
    baseline = report["results"]["baseline"]["kleink10"]
    with pytest.raises(AssertionError, match="patched/kleink10"):
        proof._validate_result("patched", "kleink10", patched, "unexpected", 0)
    for field, suffix, message in (("commands", 1, "command count"),
                                   ("state", "changed", "selection state")):
        changed = copy.deepcopy(patched)
        changed[field] += suffix
        with pytest.raises(AssertionError, match=message):
            proof._validate_state_pair("kleink10", baseline, changed)
    # The original's failed checks, one line each as "scenario:line: expression": every
    # scenario that built a list fails at the delete check; deleting before any list passes.
    failed_lines = [line for line in (output / "baseline/kleink10/run.log").read_text()
                    .splitlines() if ": live_blocks == 0" in line]
    assert {line.split(":")[0] for line in failed_lines} == proof.EXPECTED["baseline"][
        "kleink10"]["failed"]
    assert not any(line.startswith("del_only:") for line in failed_lines)


def test_every_destructor_releases_its_list(argyll_archive, tmp_path, inputs, production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output, source=production)
    assert report["sanitize"] is False
    _check(report, output, inputs, production)
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=production)


def test_the_release_is_clean_under_sanitizers(argyll_archive, tmp_path, inputs, production):
    """No use after free or double free in either variant's create, reuse, recreate,
    correct and delete sequences; the same counts as the unsanitized proof."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "sanitized"
    report = proof.prove(argyll_archive, output, source=production, sanitize=True)
    assert report["sanitize"] is True
    assert any({"address", "undefined"} <= {
        name for argument in command if argument.startswith("-fsanitize=")
        for name in argument.split("=", 1)[1].split(",")
    } for command in report["commands"])
    _check(report, output, inputs, production)


@pytest.mark.parametrize(("name", "old", "new", "seen_as"), [
    # The Klein destructor keeps the original text: its lists stay allocated.
    (proof.KLEINK10_PATH, RELEASE, "", "patched/kleink10"),
    # The SpyderX destructor frees the array but not the correction paths it holds.
    (proof.SPYDX_PATH, "inst_del_disptype_list(p->dtlist, p->ndtlist);", "free(p->dtlist);",
     "patched/spydX"),
    # The Spyder X2 destructor releases zero entries: the paths stay allocated.
    (proof.SPYDX2_PATH, "inst_del_disptype_list(p->dtlist, p->ndtlist);",
     "inst_del_disptype_list(p->dtlist, 0);", "patched/spydX2"),
], ids=["not-released", "array-only", "no-entries"])
def test_a_regressed_repair_is_seen_by_the_proof(argyll_archive, tmp_path, production,
                                                 name, old, new, seen_as):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    target = production / name
    original = target.read_text()
    destructor = slots._function(original, proof.DESTRUCTORS[name])
    assert destructor.count(old) == 1
    try:
        target.write_text(original.replace(destructor, destructor.replace(old, new), 1))
        with pytest.raises(AssertionError, match=seen_as):
            proof.prove(argyll_archive, tmp_path / "regressed", source=production)
    finally:
        target.write_text(original)


def test_runner_refuses_changed_command_count(argyll_archive, tmp_path, production):
    """Exercise prove itself: a native build does not call this file's _check."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    target = production / proof.KLEINK10_PATH
    original = target.read_text()
    destructor = slots._function(original, proof.DESTRUCTORS[proof.KLEINK10_PATH])
    assert destructor.count(RELEASE) == 1
    try:
        changed = destructor.replace(RELEASE, RELEASE + "\n\t\tcommands++;")
        target.write_text(original.replace(destructor, changed, 1))
        with pytest.raises(AssertionError, match="changed kleink10 command count"):
            proof.prove(argyll_archive, tmp_path / "extra-command", source=production)
    finally:
        target.write_text(original)


def test_allocation_tracker_preserves_ownership_across_realloc(tmp_path):
    """Run the actual tracker over growth, deterministic failure and release.

    This checks bookkeeping; sanitizers do not establish that merely comparing
    an expired pointer is defined. That separate lifetime constraint is reviewed
    in cm_realloc, which saves a slot number before calling realloc.
    """
    cc = shutil.which(native.compiler_name())
    if cc is None:
        pytest.skip("C compiler unavailable for allocation tracker check")
    harness = Path(proof.__file__).with_name("disptype_list_release_probe.c").read_text()
    allocator = harness.split("/* ---- reduced common base", 1)[0]
    controlled = """
static int fail_reallocation;
static void *controlled_realloc(void *p, size_t n) {
    if (fail_reallocation) { fail_reallocation = 0; return NULL; }
    return realloc(p, n);
}
#define realloc controlled_realloc
"""
    allocator = allocator.replace("static void *cm_realloc", controlled + "static void *cm_realloc")
    allocator = allocator.replace("static void cm_free", "#undef realloc\nstatic void cm_free")
    source = tmp_path / "tracker.c"
    source.write_text(allocator + """
#include <assert.h>
int main(void) {
    char *p = malloc(4), *q;
    memcpy(p, "abc", 4);
    assert(allocations == 1);
    p = realloc(p, 64);
    assert(p && !strcmp(p, "abc") && live_blocks == 1 && live_bytes == 64 && allocations == 2);
    fail_reallocation = 1;
    q = realloc(p, 128);
    assert(q == NULL && !strcmp(p, "abc") && live_blocks == 1 && live_bytes == 64
           && allocations == 2);
    free(p);
    p = realloc(NULL, 8);
    assert(p && live_blocks == 1 && live_bytes == 8 && allocations == 3);
    assert(realloc(p, 0) == NULL && live_blocks == 0 && live_bytes == 0 && allocations == 3);
    free(strdup("abc"));
    free(calloc(2, 4));
    assert(live_blocks == 0 && live_bytes == 0 && untracked_frees == 0 && allocations == 5);
    return 0;
}
""")
    binary = native.executable_path(tmp_path, "tracker")
    built = subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                            *proof.sanitizers.flags(True), str(source), "-o", str(binary)],
                           capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0 and run.stderr == "", run.stderr
