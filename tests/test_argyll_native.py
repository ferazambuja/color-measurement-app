"""Adversarial synthetic protocol and source-pin checks; no upstream runtime needed."""

import ast
import copy
import importlib.util
import json
import platform
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from color_measurement import native_protocol as native_io
from color_measurement.native_receipt import native_worker_name

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


native = load("argyll_native_check", TOOLS / "argyll_native_check.py")
protocol = load("argyll_native_protocol", TOOLS / "native_argyll/protocol.py")


def test_native_diagnostics_preserve_binary_head_tail_cap_digest_and_drain(tmp_path):
    import hashlib

    raw = bytes(range(256)) * 700
    target = tmp_path / "diagnostic.log"
    capture = native_io.NativeDiagnosticCapture(target, debug_level=8, limit_bytes=1025)
    assert capture.snapshot()["state"] == "prepared" and not target.exists()
    command = [sys.executable, "-I", "-c",
               "import sys;sys.stderr.buffer.write(bytes(range(256))*700)"]
    with protocol.HelperSession(command, diagnostics=capture) as session:
        assert session.process.wait(timeout=3) == 0
    metadata = capture.snapshot()
    assert metadata["state"] == "complete" and metadata["worker_started"] is True
    assert metadata["received_bytes"] == len(raw) and metadata["retained_bytes"] == 1025
    assert metadata["written_bytes"] == 1025 and metadata["truncated"] is True
    assert metadata["drain_complete"] is metadata["file_complete"] is True
    selected = raw[:512] + raw[-513:]
    assert metadata["schema_version"] == 2 and metadata["retention"] == "head_tail"
    assert metadata["segments"] == [dict(offset=0, length=512),
                                    dict(offset=len(raw)-513, length=513)]
    assert capture.original_bytes() == target.read_bytes() == selected
    assert metadata["sha256"] == metadata["file_sha256"] == hashlib.sha256(selected).hexdigest()
    assert bytes(session.stderr) == raw[-4096:]
    assert all(not thread.is_alive() for thread in session.threads)


@pytest.mark.parametrize("cap,raw,selected,segments", [
    (1, b"", b"", []),
    (1, b"a", b"a", [dict(offset=0, length=1)]),
    (1, b"abcdef", b"f", [dict(offset=5, length=1)]),
    (5, b"abcd", b"abcd", [dict(offset=0, length=4)]),
    (5, b"abcde", b"abcde", [dict(offset=0, length=5)]),
    (5, b"abcdef", b"abdef", [dict(offset=0, length=2), dict(offset=3, length=3)]),
    (6, b"abcdefghij", b"abchij", [dict(offset=0, length=3), dict(offset=7, length=3)]),
])
@pytest.mark.parametrize("chunk_size", [1, 3, 20])
def test_native_diagnostic_segments_preserve_exact_stream_offsets(
    tmp_path, cap, raw, selected, segments, chunk_size,
):
    import hashlib

    target = tmp_path / "diagnostic.log"
    capture = native_io.NativeDiagnosticCapture(target, debug_level=1, limit_bytes=cap)
    capture.open()
    capture.start_writer()
    try:
        for offset in range(0, len(raw), chunk_size):
            capture.record(raw[offset:offset + chunk_size])
        assert capture.original_bytes() is None and capture.snapshot()["segments"] == []
        assert target.read_bytes() == b""
    finally:
        capture.finish(drain_complete=True)
    metadata = capture.snapshot()
    assert metadata["state"] == "complete" and metadata["segments"] == segments
    assert metadata["received_bytes"] == len(raw)
    assert metadata["retained_bytes"] == metadata["written_bytes"] == len(selected)
    assert metadata["truncated"] is (len(raw) > cap)
    assert capture.original_bytes() == target.read_bytes() == selected
    assert b"".join(raw[item["offset"]:item["offset"] + item["length"]]
                    for item in segments) == selected
    assert metadata["sha256"] == metadata["file_sha256"] == hashlib.sha256(selected).hexdigest()
    capture.record(b"ignored after closure")
    capture.finish(drain_complete=True)
    assert capture.snapshot() == metadata


def test_native_diagnostic_partial_sink_hash_identifies_selected_artifact_prefix(tmp_path):
    import hashlib

    raw = b"setup-0123456789-FAILURE"
    selected = raw[:4] + raw[-5:]
    capture = native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=4,
                                              limit_bytes=9)
    capture.open()
    stream = capture._stream

    class PartialStream:
        def __getattr__(self, name):
            return getattr(stream, name)

        def write(self, data):
            return stream.write(data[:6])

    capture._stream = PartialStream()
    capture.record(raw)
    capture.finish(drain_complete=True)
    metadata = capture.snapshot()
    assert metadata["state"] == "incomplete" and metadata["sink_stopped"] is True
    assert metadata["file_complete"] is False and metadata["written_bytes"] == 6
    assert capture.original_bytes() == selected and capture.target.read_bytes() == selected[:6]
    assert metadata["sha256"] == hashlib.sha256(selected).hexdigest()
    assert metadata["file_sha256"] == hashlib.sha256(selected[:6]).hexdigest()


def test_native_diagnostic_join_control_preserves_closed_original_and_sink_facts(
    tmp_path, monkeypatch,
):
    import hashlib
    import threading

    entered, release = threading.Event(), threading.Event()
    raw = b"SETUP-0123456789-LATE-FAILURE"
    selected = raw[:4] + raw[-5:]
    capture = native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=4,
                                              limit_bytes=9)
    capture.open()
    stream = capture._stream
    control = KeyboardInterrupt("Original diagnostic join interruption")
    join = threading.Thread.join

    class BlockedStream:
        def __getattr__(self, name):
            return getattr(stream, name)

        def write(self, data):
            entered.set()
            assert release.wait(5), "Test must release its blocked sink"
            return stream.write(data)

    def interrupted_join(thread, *args, **kwargs):
        if thread is capture._writer:
            raise control
        return join(thread, *args, **kwargs)

    capture._stream = BlockedStream()
    capture.record(raw)
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(threading.Thread, "join", interrupted_join)
            with pytest.raises(KeyboardInterrupt) as caught:
                capture.finish(drain_complete=True)
        assert caught.value is control and entered.wait(2)
        metadata = capture.snapshot()
        assert metadata["state"] == "incomplete" and metadata["sink_stopped"] is False
        assert metadata["file_complete"] is False and metadata["file_sha256"] is None
        assert metadata["segments"] == [dict(offset=0, length=4),
                                        dict(offset=len(raw)-5, length=5)]
        assert metadata["sha256"] == hashlib.sha256(selected).hexdigest()
        assert capture.original_bytes() == selected and str(control) in metadata["error"]
    finally:
        release.set()
        capture.finish(drain_complete=True)
        join(capture._writer, 2)
        assert not capture._writer.is_alive()


@pytest.mark.parametrize("enabled", [False, True])
def test_large_stderr_drains_without_stdin_and_keeps_unique_crash_tail(tmp_path, enabled):
    tail = b"UNIQUE FINAL CRASH DIAGNOSTIC\n"
    raw = b"x" * 200000 + tail
    capture = (native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=8,
                                               limit_bytes=1024) if enabled else None)
    command = [sys.executable, "-I", "-c",
               "import sys;sys.stderr.buffer.write(b'x'*200000+"
               "b'UNIQUE FINAL CRASH DIAGNOSTIC\\n');sys.exit(2)"]
    with protocol.HelperSession(command, diagnostics=capture) as session:
        assert session.process.wait(timeout=3) == 2
        session.threads[1].join(2)
        with pytest.raises(protocol.ProtocolError) as failed:
            session.receive_message()
        assert tail.decode().strip() in str(failed.value)
    assert bytes(session.stderr) == raw[-4096:]
    if capture is not None:
        assert capture.original_bytes() == raw[:512] + raw[-512:]
        assert capture.snapshot()["received_bytes"] == len(raw)


def test_blocked_diagnostic_sink_does_not_block_drain_cancel_or_getter(tmp_path, monkeypatch):
    import threading

    entered, release = threading.Event(), threading.Event()
    capture = native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=8,
                                              limit_bytes=1024)
    opened = capture.open

    class Stream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, data):
            entered.set()
            assert release.wait(5), "Test must release its blocked sink"
            return self.stream.write(data)

    def open_stream():
        opened()
        capture._stream = Stream(capture._stream)

    monkeypatch.setattr(capture, "open", open_stream)
    session = protocol.HelperSession([sys.executable, "-I", "-c",
        "import sys,time;sys.stderr.buffer.write(b'x'*200704);sys.stderr.flush();time.sleep(10)"],
        diagnostics=capture)
    try:
        deadline = time.monotonic() + 2
        while capture.snapshot()["received_bytes"] != 200704:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        began = time.monotonic()
        session.close()
        assert entered.is_set()
        assert time.monotonic() - began < 2
        assert session.process.poll() is not None and session.stopped == "terminated"
        metadata = capture.snapshot()
        assert metadata["state"] == "incomplete" and metadata["sink_stopped"] is False
        assert metadata["file_complete"] is False and metadata["file_sha256"] is None
        assert capture.original_bytes() == b"x" * 1024
        assert all(not thread.is_alive() for thread in session.threads)
    finally:
        release.set()
        session.close()
        capture._writer.join(2)
        assert not capture._writer.is_alive()


@pytest.mark.parametrize("failure", ["write", "sync", "close"])
def test_native_diagnostic_sink_failure_keeps_closed_original_bytes(tmp_path, monkeypatch, failure):
    import hashlib

    raw = bytes(range(256)) * 3
    capture = native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=4)
    opened = capture.open

    class Stream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, data):
            if failure == "write":
                raise OSError("Original diagnostic write failure")
            return self.stream.write(data)

        def close(self):
            self.stream.close()
            if failure == "close":
                raise OSError("Original diagnostic close failure")

    def open_stream():
        opened()
        capture._stream = Stream(capture._stream)

    monkeypatch.setattr(capture, "open", open_stream)
    if failure == "sync":
        def refuse_sync(*args):
            raise OSError("Original diagnostic sync failure")
        monkeypatch.setattr(native_io.os, "fsync", refuse_sync)
    with protocol.HelperSession([sys.executable, "-I", "-c",
                               "import sys;sys.stderr.buffer.write(bytes(range(256))*3)"],
                               diagnostics=capture) as session:
        assert session.process.wait(timeout=3) == 0
    metadata = capture.snapshot()
    assert metadata["state"] == "incomplete" and metadata["file_complete"] is False
    assert metadata["error"] and metadata["drain_complete"] is True
    assert metadata["truncated"] is False and metadata["retained_bytes"] == len(raw)
    assert capture.original_bytes() == raw and metadata["sha256"] == hashlib.sha256(raw).hexdigest()
    written = capture.target.read_bytes()
    assert metadata["written_bytes"] == len(written)
    assert metadata["file_sha256"] == hashlib.sha256(written).hexdigest()


def test_native_diagnostic_actual_popen_refusal_reports_not_started_after_empty_file_close(
    tmp_path,
):
    capture = native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=8,
                                              limit_bytes=1024)
    prepared = capture.snapshot()
    with pytest.raises(FileNotFoundError):
        protocol.HelperSession([str(tmp_path / "missing-worker")], diagnostics=capture)
    metadata = capture.snapshot()
    assert metadata.keys() == prepared.keys() and len(metadata) == 19
    assert metadata["state"] == "not_started" and metadata["worker_started"] is False
    assert all(metadata[key] == 0 for key in ("received_bytes", "retained_bytes", "written_bytes"))
    assert metadata["sha256"] is metadata["file_sha256"] is None
    assert metadata["drain_complete"] is metadata["file_complete"] is False
    assert metadata["sink_stopped"] is True and capture._stream.closed
    assert metadata["path"] == "diagnostic.log" and metadata["debug_level"] == 8
    assert metadata["limit_bytes"] == 1024 and "FileNotFoundError" in metadata["error"]
    assert capture.original_bytes() is None and capture.target.read_bytes() == b""
    capture.finish(drain_complete=True)
    assert capture.snapshot() == metadata


@pytest.mark.parametrize("failure_type", [PermissionError, KeyboardInterrupt, SystemExit])
def test_native_diagnostic_popen_refusal_preserves_original_error_and_closed_unstarted_facts(
    tmp_path, monkeypatch, failure_type,
):
    capture = native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=4)
    original = failure_type("Original worker startup refusal")

    def refuse(*args, **kwargs):
        raise original

    monkeypatch.setattr(native_io.subprocess, "Popen", refuse)
    with pytest.raises(failure_type) as caught:
        protocol.HelperSession(["never-run"], diagnostics=capture)
    assert caught.value is original
    metadata = capture.snapshot()
    assert metadata["state"] == "not_started" and metadata["worker_started"] is False
    assert metadata["sink_stopped"] and capture._stream.closed
    assert metadata["sha256"] is metadata["file_sha256"] is None
    assert metadata["error"] == f"{failure_type.__name__}: {original}"


@pytest.mark.parametrize("failure", ["sync", "close", "unconfirmed"])
def test_native_diagnostic_popen_and_empty_file_cleanup_failure_stays_incomplete(
    tmp_path, monkeypatch, failure,
):
    capture = native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=4)
    original = PermissionError("Original worker startup refusal")
    opened = capture.open
    streams = []

    class Stream:
        def __init__(self, stream):
            self.stream = stream
            streams.append(stream)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self):
            if failure == "close":
                raise OSError("Original empty-file close failure")
            if failure == "unconfirmed":
                return
            self.stream.close()

    def open_stream():
        opened()
        capture._stream = Stream(capture._stream)

    def refuse(*args, **kwargs):
        raise original

    def refuse_sync(*args):
        raise OSError("Original empty-file sync failure")

    monkeypatch.setattr(capture, "open", open_stream)
    monkeypatch.setattr(native_io.subprocess, "Popen", refuse)
    if failure == "sync":
        monkeypatch.setattr(native_io.os, "fsync", refuse_sync)
    try:
        with pytest.raises(PermissionError) as caught:
            protocol.HelperSession(["never-run"], diagnostics=capture)
        assert caught.value is original
        metadata = capture.snapshot()
        assert metadata["state"] == "incomplete" and metadata["worker_started"] is False
        assert metadata["file_complete"] is False and metadata["sha256"] is not None
        expected = ("closure was not confirmed" if failure == "unconfirmed"
                    else f"empty-file {failure} failure")
        assert expected in metadata["error"]
        assert capture.original_bytes() == b""
    finally:
        for stream in streams:
            stream.close()


def test_native_diagnostic_startup_and_cleanup_preserve_first_control(tmp_path, monkeypatch):
    capture = native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=8)
    original = KeyboardInterrupt("Original startup control")
    later = SystemExit("Later diagnostic sync control")

    def interrupted(*args, **kwargs):
        raise original

    def sync_interrupted(*args):
        raise later

    monkeypatch.setattr(native_io.subprocess, "Popen", interrupted)
    monkeypatch.setattr(native_io.os, "fsync", sync_interrupted)
    with pytest.raises(KeyboardInterrupt) as caught:
        protocol.HelperSession(["never-run"], diagnostics=capture)
    assert caught.value is original and original.__cause__ is later
    assert capture.snapshot()["state"] == "incomplete"
    assert capture.snapshot()["worker_started"] is False
    assert capture.original_bytes() == b"" and capture.target.read_bytes() == b""


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failure", [OSError, ValueError])
def test_stderr_reader_failure_preserves_preview_and_exposes_incomplete_drain(
    tmp_path, enabled, failure,
):
    from types import SimpleNamespace

    class BrokenStderr:
        first = True

        def read(self, count):
            if self.first:
                self.first = False
                return b"original crash tail"
            raise failure("Original stderr read failure")

    capture = (native_io.NativeDiagnosticCapture(tmp_path / "diagnostic.log", debug_level=1)
               if enabled else None)
    if capture is not None:
        capture.open()
    session = object.__new__(native_io.HelperSession)
    session.process = SimpleNamespace(stderr=BrokenStderr())
    session.stderr = bytearray()
    session.stderr_error = None
    session._stderr_eof = False
    session.diagnostics = capture
    session.timeout = 1
    session.threads = [None, SimpleNamespace(join=lambda **kwargs: None)]
    try:
        session._read_stderr()
        assert bytes(session.stderr) == b"original crash tail" and not session._stderr_eof
        detail = session._stderr_detail()
        assert "original crash tail" in detail and "stderr preview incomplete" in detail
        assert failure.__name__ in detail and "Original stderr read failure" in detail
        if capture is not None:
            assert "Original stderr read failure" in capture.snapshot()["error"]
    finally:
        if capture is not None:
            capture.finish(drain_complete=False)


@pytest.mark.parametrize("level,cap", [(True, 10), (9, 10), (4, True), (4, 0), (4, 1048577)])
def test_native_diagnostic_limits_refuse_before_file_or_worker(tmp_path, level, cap):
    target = tmp_path / "diagnostic.log"
    with pytest.raises(ValueError):
        native_io.NativeDiagnosticCapture(target, debug_level=level, limit_bytes=cap)
    assert not target.exists()


def test_scoped_binding_recipe_reuses_shared_builder_and_declares_omitted_regressions(
    tmp_path, monkeypatch,
):
    calls = []
    output = tmp_path / "scoped"
    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native.shutil, "which", lambda name: name)

    def prepare(archive, destination, **kwargs):
        destination.mkdir()
        calls.append(("prepare", kwargs))
        return destination / "source", dict(status="failed", commands=[])

    def library(source, destination, report, cc):
        calls.append(("library", source, destination, cc))
        return ["recorded-flags"]

    def substitutes(source, destination, report, cc, flags, **kwargs):
        calls.append(("substitutes", flags, kwargs))
        report.update(description_workflow=dict(status="passed"),
                      generic_session=dict(status="passed"))

    monkeypatch.setattr(native, "prepare", prepare)
    monkeypatch.setattr(native, "prove_platform_io", lambda *args: {"status": "passed"})
    monkeypatch.setattr(native, "build_library", library)
    monkeypatch.setattr(native, "build_substituted_workers", substitutes)
    monkeypatch.setattr(native, "command", lambda *args: "Inert compiler version")
    assert native.main(["--archive", "unused.zip", "--output", str(output),
                        "--substituted-binding"]) == 0
    report = json.loads((output / "report.json").read_bytes())
    assert report["status"] == "passed" and report["check_scope"] == "substituted_binding"
    assert "not full native regression acceptance" in report["scope"]
    assert [item[0] for item in calls] == ["prepare", "library", "substitutes"]
    assert calls[-1][-1] == dict(system="Darwin")
    assert report["generic_session"]["status"] == "passed"
    assert report["description_workflow"]["status"] == "passed"


@pytest.mark.parametrize("extra", [["--all-drivers"], ["--single-driver"], ["--family", "i1pro"]])
@pytest.mark.parametrize("binding", [False, True])
def test_retired_driver_scope_options_are_refused_before_build(
    tmp_path, monkeypatch, extra, binding,
):
    monkeypatch.setattr(native, "prepare", lambda *a, **k: pytest.fail("Unexpected preparation"))
    with pytest.raises(SystemExit) as failure:
        native.main(["--archive", "unused.zip", "--output", str(tmp_path / "out"),
                     *(["--substituted-binding"] if binding else []), *extra])
    assert failure.value.code == 2 and not (tmp_path / "out").exists()


@pytest.mark.parametrize("system", ["Windows", "Linux", "Darwin"])
@pytest.mark.parametrize("sanitize", [False, True])
def test_substituted_builder_scopes_startup_flags_to_worker_links(
    tmp_path, monkeypatch, system, sanitize,
):
    """Record real builder commands; substitute all execution and device checks."""
    from native_argyll import description_check, driver_manifest, generic_session_check, sanitizers

    monkeypatch.setattr(native.platform, "system", lambda: system)
    monkeypatch.setattr(native, "INSTRUMENT_TYPES", set())
    constructors = {"instCR250": "new_cr250", "instPR655": "new_pr655"}
    report = dict(commands=[], driver_scope="all_public", no_kill={},
                  generic_worker_build_link={},
                  contribution=dict(instrument_types_added=list(constructors)),
                  driver_manifest=dict(instrument_types=[dict(enum=enum, constructor=constructor)
                      for enum, constructor in constructors.items()]))
    calls = []

    def record(args, output, current):
        calls.append(list(args))
        log = f"command-{len(current['commands']):02}.log"
        current["commands"].append(dict(argv=list(args), log=log))
        (output / log).write_text("Inert command assembly check\n")
        if args[0] == "inert-cc":
            if "-o" in args:
                Path(args[args.index("-o") + 1]).write_bytes(b"Inert artifact; never executed")
            return ""
        if args[0] == "nm":
            return "new_cr250\nnew_pr655\n"
        suites = {"cr-driver-unit": "cr250_driver_test", "cr-host-test": "cr250_control_test",
                  "pr-host-test": "pr655_host_test"}
        assert len(args) == 1 and Path(args[0]).stem in suites
        return f"{suites[Path(args[0]).stem]}: 1 checks, 0 failures\n"

    def instrumented(output, current, *args):
        current["contributed_sanitizers"] = dict(enabled=sanitize)

    def inert_probe(source, output, current, cc, flags, *, system):
        assert not set(native.process_entry_flags(system)).intersection(flags)
        name = "pr-host-test" if "pr" in current.get("probe", "") else "cr-driver-unit"
        executable = native.executable_path(output, name)
        executable.write_bytes(b"Inert artifact; never executed")
        return executable

    monkeypatch.setattr(native, "command", record)
    monkeypatch.setattr(native, "contributed_sanitizer_support", instrumented)
    monkeypatch.setattr(native, "build_contributed_test_support",
                        lambda *args: ([], set(constructors.values())))
    monkeypatch.setattr(native, "forbid_linked_transport", lambda *args: None)
    monkeypatch.setattr(native, "cr_read_allowance_floors", lambda *args: {"fixture": 1.0})
    monkeypatch.setattr(native, "build_cr250_driver_unit", inert_probe)

    def pr_probe(*args, **kwargs):
        args[2]["probe"] = "pr"
        return inert_probe(*args, **kwargs)

    monkeypatch.setattr(native, "build_pr655_host_check", pr_probe)
    monkeypatch.setattr(generic_session_check, "exercise_generic_session",
                        lambda *args, **kwargs: dict(personas=["CR-250 (real driver)"]))
    monkeypatch.setattr(driver_manifest, "parse_dispatch", lambda *args: constructors)
    for name in ("exercise_description", "prove_session_selection", "prove_selection_guard"):
        monkeypatch.setattr(description_check, name, lambda *args, **kwargs: {})
    flags = ["-DCM_RECORDED_COMMON_FLAGS"]
    native.build_substituted_workers(tmp_path / "source", tmp_path, report, "inert-cc",
                                     flags, system=system)
    compiled = [args for args in calls if args[0] == "inert-cc" and "-o" in args]
    outputs = {Path(args[-1]).stem for args in compiled}
    assert {"generic-session-stub-worker", "generic-chart-stub-worker", "description-stub-worker",
            "description-chart-persona", "cr-floor", "pr-floor", "cr-host-test"} <= outputs
    for args in compiled:
        worker = any(Path(arg).name in {"session.c", "description.c"} for arg in args)
        for flag in ("-municode", "-UUNICODE"):
            assert (flag in args) == (system == "Windows" and worker)
        assert "-DCM_RECORDED_COMMON_FLAGS" in args
        if Path(args[-1]).stem != "pr-floor":
            assert set(sanitizers.flags(sanitize)) <= set(args)
    assert flags == ["-DCM_RECORDED_COMMON_FLAGS"]


@pytest.mark.parametrize(
    "diagnostic",
    [
        "",
        "Unsupported native stub fixture: strip_wait_armed. Valid fixtures: success armed_timeout",
    ],
)
@pytest.mark.parametrize("before_command", [False, True])
def test_startup_refusal_preserves_bounded_worker_diagnostic(diagnostic, before_command):
    command = [
        sys.executable,
        "-I",
        "-c",
        "import sys; sys.stderr.write(sys.argv[1]); sys.exit(2)",
        diagnostic,
    ]
    with protocol.HelperSession(command, timeout=2) as session:
        if before_command:
            assert session.process.wait(timeout=2) == 2
            with pytest.raises(OSError) as error:
                session.begin("startup-refusal", "success")
            assert "Helper refused the initial command" in str(error.value)
            assert diagnostic in str(error.value)
        else:
            with pytest.raises(protocol.ProtocolError) as error:
                session.receive_message()
            assert str(error.value) == "Helper exited before the expected event" + (
                f": {diagnostic}" if diagnostic else ""
            )
    assert session.process.poll() == 2
    assert all(
        s.closed for s in (session.process.stdin, session.process.stdout, session.process.stderr)
    )


@pytest.fixture
def reading():
    return {
        "protocol_version": 1,
        "provenance": "synthetic",
        "operation_id": "unit",
        "sequence": 2,
        "event": "result",
        "argyll_version": "3.5.0",
        "requested_mode": "synthetic_reflective",
        "confirmed_mode": "synthetic_reflective",
        "calibration_context": "synthetic_not_calibrated",
        "project_computed": {},
        "backend_reported": {
            "XYZ_v": 1,
            "XYZ": [2.0, 3.0, 5.0],
            "mtype": 5,
            "mcond": 1,
            "duration": 0.25,
            "spectrum": {
                "spec_n": 4,
                "wavelength_start_nm": 402.5,
                "wavelength_end_nm": 417.5,
                "norm": 100.0,
                "samples": [-0.125, 1.2345678901234567, 120.25, 0.0],
            },
        },
    }


def test_native_fields_preserve_signed_data_precision_grid_and_norm(reading):
    result = protocol.NativeReading.parse(reading)
    assert result.xyz == (2, 3, 5)
    assert result.spectrum.samples == (-0.125, 1.2345678901234567, 120.25, 0)
    assert result.spectrum.wavelengths_nm == (402.5, 407.5, 412.5, 417.5)
    assert result.spectrum.norm == 100
    assert result.duration_seconds == 0.25
    assert result.mcond == 1


@pytest.mark.parametrize("condition", [0, 1, 2, 3, 4, 5])
def test_condition_codes_survive_without_becoming_custom(reading, condition):
    reading["backend_reported"]["mcond"] = condition
    assert protocol.NativeReading.parse(reading).mcond == condition


def test_invalid_xyz_is_unavailable_but_valid_black_is_present(reading):
    reading["backend_reported"].update(XYZ_v=0, XYZ=None)
    assert protocol.NativeReading.parse(reading).xyz is None
    reading["backend_reported"].update(XYZ_v=1, XYZ=[0.0, 0.0, 0.0], spectrum=None)
    result = protocol.NativeReading.parse(reading)
    assert result.xyz == (0, 0, 0) and result.xyz_valid and result.spectrum is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("XYZ_v", True),
        ("XYZ", [float("nan"), 0, 0]),
        ("mcond", 6),
        ("mtype", 1),
        ("duration", -1),
    ],
)
def test_malformed_active_native_fields_are_rejected(reading, field, value):
    reading["backend_reported"][field] = value
    with pytest.raises(protocol.ProtocolError):
        protocol.NativeReading.parse(reading)


@pytest.mark.parametrize(
    "field,value",
    [
        ("norm", 0),
        ("norm", float("inf")),
        ("wavelength_end_nm", 400),
        ("samples", [0.0, 1.0]),
    ],
)
def test_active_spectral_shape_and_scaling_are_checked(reading, field, value):
    reading["backend_reported"]["spectrum"][field] = value
    with pytest.raises(protocol.ProtocolError):
        protocol.NativeReading.parse(reading)


@pytest.mark.parametrize("count", [1, 602])
def test_active_spectral_count_bounds_with_complete_consistent_fields(reading, count):
    reading["backend_reported"]["spectrum"].update(spec_n=count, samples=[0.0] * count)
    with pytest.raises(protocol.ProtocolError, match="Invalid active spectral count"):
        protocol.NativeReading.parse(reading)


def test_absence_and_provenance_cannot_become_a_measurement(reading):
    absent = copy.deepcopy(reading)
    absent["backend_reported"].update(XYZ_v=0, XYZ=None, spectrum=None)
    with pytest.raises(protocol.ProtocolError):
        protocol.NativeReading.parse(absent)
    reading["provenance"] = "physical"
    with pytest.raises(protocol.ProtocolError):
        protocol.NativeReading.parse(reading)


def child(events, tail=""):
    text = "".join(json.dumps(event) + "\n" for event in events)
    return [
        sys.executable,
        "-u",
        "-c",
        f"import sys,time;sys.stdout.write({text!r});sys.stdout.flush();{tail}",
    ]


def event(name, sequence, operation_id="unit"):
    return {
        "protocol_version": 1,
        "provenance": "synthetic",
        "operation_id": operation_id,
        "sequence": sequence,
        "event": name,
    }


@pytest.mark.parametrize(
    "malformed", [event("ready", 0), event("started", 5), event("started", 0, "wrong-operation")]
)
def test_event_order_sequence_and_operation_are_checked(malformed):
    with protocol.HelperSession(child([malformed])) as session:
        session.operation_id = "unit"
        with pytest.raises(protocol.ProtocolError):
            session.receive()


def test_result_that_beats_a_cancel_request_is_kept_and_the_race_recorded(reading):
    """A cancel can lose: Argyll's uicallback ignores its return value for
    inst_triggered (3.5.0 spectro/inst.h:1136), so a helper may already have
    committed the measurement. Upstream callers branch on the call's return
    code and never discard data a driver returned, so the reading stands."""
    reading["sequence"] = 2
    events = [event("started", 0), event("ready", 1), reading]
    with protocol.HelperSession(child(events)) as session:
        session.operation_id = "unit"
        session.receive()
        session.receive()
        session.cancel_sent = True          # cancel is in flight, unacknowledged
        received = session.receive()        # the helper had already finished
        assert received.event == "result" and received.reading is not None
        assert session.cancel_lost_race is True
        session.finish()


def test_finish_tolerates_a_cancel_the_exited_helper_never_read(reading):
    """stop() in instrument.py tolerates a closed pipe when a cancel loses to the
    helper's exit, but the unsent bytes stay buffered and closing stdin flushes
    them again; the helper's exit code is the verdict, not that second flush."""
    reading["sequence"] = 2
    events = [event("started", 0), event("ready", 1), reading]
    with protocol.HelperSession(child(events)) as session:
        session.operation_id = "unit"
        for _ in range(3):
            session.receive()
        assert session.process.wait(timeout=2) == 0  # the helper is gone
        with pytest.raises(OSError) as closed:
            session.send("cancel")  # what stop() tolerates; the bytes stay buffered
        assert native_io._closed_pipe(closed.value)
        session.finish()


def test_late_result_after_cancel_is_rejected_and_child_reaped(reading):
    reading["sequence"] = 3
    events = [event("started", 0), event("ready", 1), event("cancelled", 2), reading]
    with protocol.HelperSession(child(events, "time.sleep(10)")) as session:
        session.operation_id = "unit"
        session.receive()
        session.receive()
        session.cancel_sent = True
        session.receive()
        with pytest.raises(protocol.ProtocolError, match="late event"):
            session.receive()
    assert session.process.poll() is not None


def test_finish_rejects_extra_output_after_terminal(reading):
    with protocol.HelperSession(
        child([event("started", 0), event("ready", 1), reading, event("ready", 3)])
    ) as session:
        session.operation_id = "unit"
        for _ in range(3):
            session.receive()
        with pytest.raises(protocol.ProtocolError, match="Late output"):
            session.finish()


def test_timeout_reaps_owned_process_without_restarting():
    with protocol.HelperSession(
        [sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.05
    ) as session:
        pid = session.process.pid
        with pytest.raises(TimeoutError):
            session.receive()
    assert session.process.pid == pid and session.process.poll() is not None


def test_corrupt_source_archive_refused_before_writing(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned source")
    output = tmp_path / "result"
    with pytest.raises(ValueError, match="SHA-256"):
        native.prepare(archive, output)
    assert not output.exists()


def test_existing_output_is_refused_before_prepare(tmp_path, monkeypatch, capsys):
    """main() refuses an existing output before any archive is read, so no archive is needed."""
    output = tmp_path / "keep"
    output.mkdir()
    retained = output / "report.json"
    retained.write_bytes(b"existing result")
    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native.shutil, "which", lambda _: "/usr/bin/cc")
    monkeypatch.setattr(native, "prepare", lambda *args, **kwargs:
                        pytest.fail("Existing output reached prepare"))
    assert native.main(["--archive", "unused.zip", "--output", str(output)]) == 1
    assert "Output already exists" in capsys.readouterr().err
    assert retained.read_bytes() == b"existing result"


def test_prepare_never_writes_into_an_existing_output(argyll_archive, tmp_path):
    output = tmp_path / "keep"
    output.mkdir()
    retained = output / "report.json"
    retained.write_bytes(b"existing result")
    with pytest.raises(FileExistsError):
        native.prepare(argyll_archive, output)
    assert list(output.iterdir()) == [retained]
    assert retained.read_bytes() == b"existing result"


@pytest.mark.parametrize("system", ["FreeBSD", "OpenBSD"])
def test_unsupported_native_platform_refuses_before_writes(tmp_path, monkeypatch, capsys, system):
    monkeypatch.setattr(native.platform, "system", lambda: system)
    output = tmp_path / "unused"
    assert native.main(["--archive", "unused.zip",
                        "--output", str(output)]) == 1
    assert "Native builds require macOS, Linux or Windows MinGW-w64" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_broad_platform_request_reaches_authenticated_preparation(
    tmp_path, monkeypatch, capsys, system,
):
    monkeypatch.setattr(native.platform, "system", lambda: system)
    calls = []

    def prepare(*args, **kwargs):
        calls.append(kwargs)
        raise ValueError("authenticated archive input reached")

    monkeypatch.setattr(native, "prepare", prepare)
    output = tmp_path / "unused"
    assert native.main([
        "--archive", "missing.zip", "--output", str(output),
    ]) == 1
    assert calls == [{}]
    assert "authenticated archive input reached" in capsys.readouterr().err
    assert not output.exists()


def test_missing_archiver_refused_before_extraction(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(native.platform, "system", lambda: "Linux")
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setattr(
        native.shutil, "which", lambda name: "/usr/bin/cc" if name == "cc" else None,
    )
    output = tmp_path / "unused"
    assert native.main(["--archive", "missing.zip", "--output", str(output)]) == 1
    assert "archiver is required" in capsys.readouterr().err
    assert not output.exists()


def test_linux_link_recipe_limits_whole_archive_to_instrument_library():
    library = Path("/private output/libcm_argyll_offline.a")
    flags = native.library_link_flags(library, "Linux")
    start, end = flags.index("-Wl,--whole-archive"), flags.index("-Wl,--no-whole-archive")
    assert flags[start + 1:end] == [str(library)]
    assert flags.index("-pthread") > end and flags.index("-lm") > end
    assert "-framework" not in flags


def test_windows_link_recipe_and_executable_suffix(monkeypatch):
    monkeypatch.setattr(native.platform, "system", lambda: "Windows")
    library = Path("C:/private build/argyll.a")
    flags = native.library_link_flags(library, "Windows")
    start, end = flags.index("-Wl,--whole-archive"), flags.index("-Wl,--no-whole-archive")
    assert flags[start + 1:end] == [str(library)]
    assert flags.index("-lsetupapi") > end
    assert not {"-pthread", "-framework"}.intersection(flags)
    assert native.executable_path(Path("build"), "worker").name == "worker.exe"


@pytest.mark.parametrize("system,requested,expected", [
    ("Windows", None, "clang"), ("Linux", None, "cc"), ("Darwin", None, "cc"),
    ("Windows", "custom-clang", "custom-clang"), ("Linux", "gcc", "gcc"),
])
def test_build_and_compiled_tests_share_compiler_selection(
    monkeypatch, system, requested, expected,
):
    monkeypatch.setattr(native.platform, "system", lambda: system)
    if requested is None:
        monkeypatch.delenv("CC", raising=False)
    else:
        monkeypatch.setenv("CC", requested)
    assert native.compiler_name() == expected


@pytest.mark.parametrize(("format_name", "target"), [
    ("pei-x86-64", "x86_64-w64-mingw32"), ("coff-x86-64", "x86_64-w64-windows-gnu"),
    ("pei-aarch64-little", "aarch64-w64-mingw32"),
    ("coff-arm64", "aarch64-w64-windows-gnu"),
])
def test_windows_import_inspection_accepts_gnu_and_llvm_pe_images(format_name, target):
    inspected = (f"worker.exe: file format {format_name}\nCharacteristics 0x22\n"
                 "\texecutable\nMagic                   020b\t(PE32+)\n"
                 "    DLL Name: KERNEL32.dll\n    DLL Name: UCRTBASE.dll\n"
                 "    DLL Name: KERNEL32.dll\n")
    assert native.windows_dll_imports(inspected, compiler_target=target) == [
        "KERNEL32.dll", "UCRTBASE.dll",
    ]


@pytest.mark.parametrize("inspected", [
    "file format elf64-x86-64\n\texecutable\nMagic 020b (PE32+)\n",
    "file format coff-i386\n\texecutable\nMagic 010b (PE32)\n",
    "file format coff-x86-64\n",  # A COFF object is not a PE executable.
    "file format coff-x86-64\n\texecutable\nMagic 010b (PE32)\n",
    "file format coff-x86-64\nMagic 020b (PE32+)\n",
])
def test_windows_import_inspection_refuses_other_architectures_and_objects(inspected):
    with pytest.raises(ValueError, match=r"PE32\+"):
        native.windows_dll_imports(inspected + " DLL Name: KERNEL32.dll\n",
                                   compiler_target="x86_64-w64-mingw32")


def test_windows_import_inspection_requires_a_runtime_table():
    with pytest.raises(ValueError, match="import table is empty"):
        native.windows_dll_imports("file format coff-x86-64\n\texecutable\nMagic 020b (PE32+)\n",
                                   compiler_target="x86_64-w64-mingw32")


def test_dependency_paths_preserve_windows_drives_and_make_escaping():
    text = "object: C:/source/unit.c C:/a\\ path/a$$b\\#c.h \\\n " + r"D:\sdk\header.h"
    assert native.dependency_names(text) == [
        "C:/source/unit.c", "C:/a path/a$b#c.h", r"D:\sdk\header.h",
    ]


@pytest.mark.parametrize("system", ["Windows", "Linux", "Darwin"])
@pytest.mark.parametrize("compile_dependencies", [False, True])
def test_command_preserves_runtime_argv_and_scopes_dependency_path_conversion(
    tmp_path, monkeypatch, system, compile_dependencies,
):
    monkeypatch.setattr(native.platform, "system", lambda: system)
    observed = []

    def run(args, **kwargs):
        observed.append(list(args))
        return subprocess.CompletedProcess(args, 0, "unchanged output", "")

    monkeypatch.setattr(native.subprocess, "run", run)
    if compile_dependencies:
        args = [r"C:\tool\clang.exe", "-MMD", "-MF", r"C:\build dir\unit.d",
                "-MT", "object", r"-IC:\source dir\include", "-c", r"C:\source dir\unit.c",
                "-o", r"C:\build dir\unit.o"]
        launch = native.compile_with_dependencies
    else:
        # Runtime values may resemble compiler switches. None are path rewrites.
        args = [r"C:\build dir\probe.exe", "arguments", "", "CM-ñ-1", 'a "quote"',
                "C:\\space here\\ñ\\", "\\\\", "-MMD"]
        launch = native.command
    original = list(args)
    expected = ([arg.replace("\\", "/") for arg in args]
                if system == "Windows" and compile_dependencies else list(args))
    report = {"commands": []}
    assert launch(args, tmp_path, report) == "unchanged output"
    assert observed == [expected] and args == original
    assert report["commands"] == [dict(argv=expected, log="command-00.log", returncode=0)]
    assert (tmp_path / "command-00.log").read_text() == "unchanged output"


def test_owned_process_io_preserves_fragmented_cancel_bytes_and_eof(tmp_path):
    cc = shutil.which(native.compiler_name())
    if cc is None or platform.system() not in {"Darwin", "Linux", "Windows"}:
        pytest.skip("Host C compiler unavailable")
    result = native.prove_platform_io(tmp_path, {"commands": []}, cc)
    assert result["status"] == "passed"
    assert result["cases"] == ["fragmented_poll_cancel_and_eof", "blocking_byte_and_eof",
                                "utf8_command_line_arguments", "utf8_input_file_bytes"]


def test_standalone_utf16_refuses_changed_authenticated_source(tmp_path):
    source = tmp_path / "upstream"
    (source / "icc").mkdir(parents=True)
    unit = source / "icc/icc_util.c"
    unit.write_text("changed after archive authentication")
    report = {"upstream_source_sha256": {"icc/icc_util.c": native.digest(b"original")}}
    with pytest.raises(ValueError, match="changed after archive authentication"):
        native.standalone_utf16(source, report)
    assert not (source / "icc/cm_utf16_compat.h").exists()


def test_session_selection_probe_uses_shared_referenced_support_library(tmp_path):
    from native_argyll.description_check import prove_session_selection

    calls = []
    library = tmp_path / "libcm_argyll_offline.a"
    link_flags = native.referenced_members_link_flags(library, platform.system())
    executable = tmp_path / native_worker_name("session-selection-substituted")

    def command(args, output, report):
        calls.append(args)
        if args[0] == "inert-compiler":
            executable.write_bytes(b"Inert selection probe; never executed")
            return ""
        assert args == [str(executable)]
        return json.dumps(dict(selection_parser_cases=11, long32_saturations=1))

    result = prove_session_selection(tmp_path, {}, "inert-compiler", ["-DUNIX"], command,
                                     link_flags=link_flags)
    assert result["status"] == "passed" and len(calls) == 2
    assert calls[0][-len(link_flags)-2:-2] == link_flags
    assert str(library) in calls[0]
    assert any(value.endswith("session_attach.c") for value in calls[0])


def test_native_link_probe_detects_dependency_in_unreferenced_object(tmp_path):
    """Archive creation and an ordinary link can hide an unusable driver object."""
    system = platform.system()
    cc, ar = shutil.which(native.compiler_name()), shutil.which("ar")
    if system not in {"Darwin", "Linux", "Windows"} or cc is None or ar is None:
        pytest.skip("Host native compiler/archive recipe unavailable")
    main = tmp_path / "main.c"
    main.write_text("int main(void) { return 0; }\n")
    unused = tmp_path / "unused.c"
    unused.write_text("extern int missing_driver_dependency(void);\n"
                      "int unused_driver(void) { return missing_driver_dependency(); }\n")
    obj = tmp_path / "unused.o"
    library = tmp_path / "libunused.a"
    subprocess.run([cc, "-c", str(unused), "-o", str(obj)], check=True, capture_output=True)
    subprocess.run([ar, "rcs", str(library), str(obj)], check=True, capture_output=True)
    subprocess.run([cc, str(main), str(library), "-o", str(tmp_path / "ordinary-never-run")],
                   check=True, capture_output=True)
    probe = subprocess.run([
        cc, str(main), *native.library_link_flags(library, system),
        "-o", str(tmp_path / "probe-never-run"),
    ], capture_output=True, text=True)
    assert probe.returncode != 0
    assert "missing_driver_dependency" in probe.stderr


def test_oversized_numeric_value_is_a_protocol_error():
    with pytest.raises(protocol.ProtocolError, match="binary64"):
        protocol.finite(10**1000)


def test_hung_helper_stderr_cannot_block_timeout_cleanup():
    script = "import sys,time;sys.stderr.write('x'*100000);sys.stderr.flush();time.sleep(10)"
    with protocol.HelperSession([sys.executable, "-u", "-c", script], timeout=0.1) as session:
        with pytest.raises(TimeoutError):
            session.receive()
    assert session.process.poll() is not None
    assert len(session.stderr) <= 4096


def test_full_native_helper_frame_above_public_caps_is_admitted():
    from color_measurement.frontend import MAX_EVENT_BYTES, MAX_REQUEST_BYTES

    # The documented 65,536-byte helper envelope carries 4,096-byte identity text
    # and PR setup reports; it must not inherit the public 16,384-byte caps.
    size = 65536
    assert size > max(MAX_EVENT_BYTES, MAX_REQUEST_BYTES)
    padding = "x" * (size - len('{"padding":""}\n'))
    script = ("import sys;sys.stdout.buffer.write(b'{\"padding\":\"' + b'x' * "
              f"{len(padding)} + b'\"}}\\n');sys.stdout.buffer.flush()")
    with protocol.HelperSession([sys.executable, "-u", "-c", script]) as session:
        assert session.receive_message() == {"padding": padding}
        assert len(session.last_raw.encode()) == size


def test_oversized_helper_stdout_is_refused_and_reaped():
    # A complete oversized line isolates the length bound from truncation.
    size = native_io.NATIVE_MESSAGE_MAX_BYTES + 1
    raw = '{"padding":"' + "x" * (size - len('{"padding":""}\n')) + '"}\n'
    assert len(raw.encode()) == size and raw.endswith("\n")
    # Generate the body in the child; a 65 KiB -c argument exceeds Windows'
    # CreateProcess command-line limit. Binary output keeps the exact LF frame.
    prefix, suffix = b'{"padding":"', b'"}\n'
    script = (f"import sys;sys.stdout.buffer.write({prefix!r} + b'x' * "
              f"{size - len(prefix) - len(suffix)} + {suffix!r});sys.stdout.buffer.flush()")
    with protocol.HelperSession([sys.executable, "-u", "-c", script]) as session:
        # Observe the reader's rejection without racing its error against a timeout.
        session.process.wait(timeout=2)
        for thread in session.threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        with pytest.raises(protocol.ProtocolError, match="Oversized or truncated helper line"):
            session.receive_message()
    assert session.process.poll() is not None


def test_unconsumed_helper_flood_is_bounded_and_reaped():
    from color_measurement.native_protocol import MAX_OPERATION_EVENTS

    count = MAX_OPERATION_EVENTS + 2
    script = f"import sys;sys.stdout.write('{{}}\\n' * {count});sys.stdout.flush()"
    with protocol.HelperSession([sys.executable, "-u", "-c", script]) as session:
        session.process.wait(timeout=2)
        for thread in session.threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        with pytest.raises(protocol.ProtocolError, match="Helper output queue overflow"):
            session.receive_message()
    assert session.process.poll() is not None


def test_version_boolean_cannot_impersonate_version_one():
    malformed = event("started", 0)
    malformed["protocol_version"] = True
    with protocol.HelperSession(child([malformed])) as session:
        session.operation_id = "unit"
        with pytest.raises(protocol.ProtocolError):
            session.receive()


@pytest.mark.parametrize(
    "raw",
    [
        '{"protocol_version":1,"provenance":"physical","provenance":"synthetic",'
        '"operation_id":"unit","sequence":0,"event":"started"}',
        '{"protocol_version":1,"provenance":"synthetic","operation_id":"unit",'
        '"sequence":0,"event":"started","nested":{"XYZ_v":0,"XYZ_v":1}}',
        '{"protocol_version":1,"provenance":"synthetic","operation_id":"unit",'
        '"sequence":0,"event":"started","unused":NaN}',
    ],
)
def test_ambiguous_or_nonstandard_json_is_refused(raw):
    with protocol.HelperSession([sys.executable, "-u", "-c", f"print({raw!r})"]) as session:
        session.operation_id = "unit"
        with pytest.raises(protocol.ProtocolError, match="Malformed JSON"):
            session.receive()


@pytest.mark.parametrize("token", ["-0", "-0.0", "-0e-999", "5e-324", "-5e-324"])
def test_native_json_numbers_preserve_sign_and_subnormals_through_patch(reading, token):
    import math

    from color_measurement.instrument import _patch

    reading["backend_reported"]["XYZ"][0] = "native-number-token"
    reading["backend_reported"]["spectrum"]["samples"][0] = "native-number-token"
    raw = json.dumps(reading).replace('"native-number-token"', token)
    payload = (raw + "\n").encode()
    script = f"import sys;sys.stdout.buffer.write({payload!r});sys.stdout.buffer.flush()"
    with protocol.HelperSession([sys.executable, "-u", "-c", script]) as session:
        message = session.receive_message()
        assert session.last_raw == raw + "\n"
    for key in ("protocol_version", "sequence"):
        assert type(message[key]) is int
    assert type(message["backend_reported"]["spectrum"]["spec_n"]) is int
    for parsed in (protocol.NativeReading.parse(message), _patch(message, spectral=True)):
        for value in (parsed.xyz[0], parsed.spectrum.samples[0]):
            assert value == float(token)
            assert math.copysign(1, value) == math.copysign(1, float(token))


def test_native_json_negative_zero_survives_raw_spectrum_chunks():
    import math

    from color_measurement.instrument import native_facts

    head = {"event": "native_facts", "driver": "scripted", "identity": {},
            "capture": {"reply_count": 0, "spectrum_chunk_count": 1,
                        "spectrum": {"start_nm": 380, "end_nm": 390,
                                     "step_nm": 5, "count": 3}}}
    chunk = ('{"event":"native_spectrum","driver":"scripted",'
             '"spectrum_chunk_index":0,"spectrum_chunk_count":1,"offset":0,'
             '"values":[-0,5e-324,-5e-324]}')
    raw = json.dumps(head) + "\n" + chunk
    with protocol.HelperSession([sys.executable, "-u", "-c", f"print({raw!r})"]) as session:
        messages = [session.receive_message(), session.receive_message()]
    assert type(messages[1]["spectrum_chunk_index"]) is int
    assert type(messages[1]["offset"]) is int
    values = native_facts(messages)["capture"]["spectrum"]["values"]
    assert values == [-0.0, 5e-324, -5e-324]
    assert math.copysign(1, values[0]) == -1


@pytest.mark.parametrize("token", ["1e-999", "-1e-999", "1e999", "-1e999"])
def test_native_json_refuses_nonzero_underflow_and_overflow(token):
    raw = '{"event":"native_spectrum","values":[' + token + ']}'
    with protocol.HelperSession([sys.executable, "-u", "-c", f"print({raw!r})"]) as session:
        with pytest.raises(protocol.ProtocolError, match="Malformed JSON"):
            session.receive_message()
        assert session.last_raw == ""


def test_native_json_negative_zero_cannot_impersonate_an_integer_sequence():
    raw = json.dumps(event("started", 0)).replace('"sequence": 0', '"sequence": -0')
    with protocol.HelperSession([sys.executable, "-u", "-c", f"print({raw!r})"]) as session:
        session.operation_id = "unit"
        with pytest.raises(protocol.ProtocolError, match="Wrong event"):
            session.receive()


@pytest.mark.parametrize("encoding,detail", [
    ("utf-16-be", "ascii"), ("utf-32-be", "ascii"),
    ("utf-16-be", "café"), ("utf-32-be", "café"),
    ("utf-16-le", "café"), ("utf-32-le", "café"),
])
def test_non_utf8_frames_are_protocol_errors_and_child_is_reaped(encoding, detail):
    raw = (json.dumps({"detail": detail}, ensure_ascii=False) + "\n").encode(encoding)
    command = [sys.executable, "-u", "-c", f"import sys;sys.stdout.buffer.write({raw!r})"]
    with protocol.HelperSession(command) as session:
        with pytest.raises(protocol.ProtocolError, match="Malformed JSON"):
            session.receive_message()
        assert session.last_raw == ""
    assert session.process.poll() is not None


def test_utf8_nonascii_frame_and_native_text_survive():
    message = {"detail": "café 測定 😀"}
    raw = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    command = [sys.executable, "-u", "-c", f"import sys;sys.stdout.buffer.write({raw!r})"]
    with protocol.HelperSession(command) as session:
        assert session.receive_message() == message
        assert session.last_raw == raw.decode("utf-8")
    assert session.process.poll() is not None


def test_function_extraction_refuses_changed_or_duplicate_boundary():
    source = "static int f(void) { return 1; }\n/* NEXT */"
    assert native.extract_function(source, "static int f(", "\n/* NEXT */").endswith("}\n")
    with pytest.raises(ValueError):
        native.extract_function(source + source, "static int f(", "\n/* NEXT */")
    with pytest.raises(ValueError):
        native.extract_function(source.replace("}", ""), "static int f(", "\n/* NEXT */")


def test_numeric_parser_probe_keeps_its_must_catch_cases():
    """The probe's count is measured from its cases, so deleting one leaves it passing.
    Pin each class it exists for: a nonzero mantissa that underflows is refused in every
    notation, an explicit zero with the same exponent is accepted, and representable
    subnormals are not confused with underflow to zero."""
    generic = load("generic_session_check", TOOLS / "native_argyll/generic_session_check.py")
    cases = dict(generic._NUMERIC_CASES)
    assert len(cases) == len(generic._NUMERIC_CASES)
    assert [cases[text] for text in ("1e-4000", "-1e-4000", "0x1p-100000")] == ["0"] * 3
    assert [cases[text] for text in ("0e-4000", "-0.000e-4000", "0x0p-100000")] == ["1"] * 3
    assert cases["1e-320"] == 'real_strtod("1e-320",&end)!=0.0'
    assert cases["0x1p-1074"] == 'real_strtod("0x1p-1074",&end)!=0.0'


def test_native_and_windows_proof_verdicts_remain_active_under_python_optimization():
    """Reject assert statements in native, Windows and fixture proof modules, in the
    top-level tools that run them (the native stage and the gate among them), and in
    every example: a new example with an `assert` verdict would otherwise print PASS
    under `python -O`, which strips assert statements, unseen.
    """
    paths = []
    for folder in (TOOLS.parent / "examples", TOOLS, TOOLS / "native_argyll", TOOLS / "windows"):
        paths.extend(sorted(folder.glob("*.py")))
    assert TOOLS.parent / "examples/generic_frontend.py" in paths
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), path


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize("fault", ["none", "few_rows", "extra_rows", "few_bands", "extra_bands"])
def test_demo_palette_calculator_shape_is_checked_before_cached_output(
        optimized, fault, tmp_path):
    """A matching malformed cache must not turn --check into a pass under -O.

    Only compiler/calculator output is substituted. The real generator, JSON
    formatting and CLI check execute in an isolated temporary repository.
    """
    root = tmp_path / "repo"
    data = root / "src/color_measurement/data"
    data.mkdir(parents=True)
    (data / "demo.json").write_text('{"personas": {}}')
    tool = root / "tools/generate_demo_palette.py"
    tool.parent.mkdir()
    shutil.copyfile(TOOLS / tool.name, tool)
    chart = root / "tools/native_argyll/chart_stub.c"
    chart.parent.mkdir()
    shutil.copyfile(TOOLS / "native_argyll/chart_stub.c", chart)
    source = tmp_path / "argyll"
    libraries = ("numlib/numsup.c", "xicc/xspect.c", "spectro/sa_conv.c",
                 "cgats/cgats.c", "cgats/pars.c", "h/aconfig.h")
    for name in libraries:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    probe = r'''
import hashlib, json, pathlib, runpy, subprocess, sys
root, source, fault = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
bands = 36 + (fault == "extra_bands") - (fault == "few_bands")
count = 12 + (fault == "extra_rows") - (fault == "few_rows")
rows = [[1.0, 2.0, 3.0] + [0.5] * bands for _ in range(count)]
stdout = "\n".join(" ".join(map(str, row)) for row in rows) + "\n"
def substitute(command, **kwargs):
    return subprocess.CompletedProcess(command, 0, stdout="" if command[0] == "cc" else stdout)
subprocess.run = substitute
tool = root / "tools/generate_demo_palette.py"
module = runpy.run_path(str(tool))
shape = (root / "tools/native_argyll/chart_stub.c").read_text()
calculator = shape[shape.index("static const double shapes[12][3][3]"):
                   shape.index("\nstatic void fill(")]
libraries = ("numlib/numsup.c", "xicc/xspect.c", "spectro/sa_conv.c",
             "cgats/cgats.c", "cgats/pars.c")
cache = json.dumps({"provenance": "simulated", "names": module["NAMES"],
    "calculator_sha256": hashlib.sha256(calculator.encode()).hexdigest(),
    "argyll_source_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                             for name in libraries},
    "palettes": {"36:380:730:1": [{"XYZ": row[:3], "samples": row[3:]} for row in rows]}},
    indent=2, sort_keys=True) + "\n"
module["OUTPUT"].write_text(cache)
sys.argv = [str(tool), "--argyll-source", str(source), "--check"]
runpy.run_path(str(tool), run_name="__main__")
'''
    result = subprocess.run([sys.executable, *(["-O"] if optimized else []), "-c", probe,
                             str(root), str(source), fault],
                            capture_output=True, text=True, timeout=10)
    if fault == "none":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "Palette calculator returned an invalid row or band count" in result.stderr
        assert "Traceback" not in result.stderr


def test_source_identity_agrees_between_records_builder_and_inventory():
    """One archive digest, and every tool bound to the same one.

    It used to be written out three times -- in `i1pro.py`, in `i1pro3.py` and in
    the checker -- and this test existed to catch the copies drifting. The package
    now defines it once in `argyll_source`, which is where a fact about the
    ARCHIVE belongs rather than inside a module for one instrument, so what is
    left to check is that the standalone tools agree with the package.
    """
    from color_measurement.argyll_source import ARCHIVE_SHA256

    inventory = load("argyll_inventory_for_pin_check", TOOLS / "argyll_inventory.py")
    assert ARCHIVE_SHA256 == native.ARCHIVE_SHA256 == inventory.EXPECTED_ARCHIVE_SHA256


def test_compiler_inventory_includes_nested_sources_and_copied_configuration(tmp_path, monkeypatch):
    source = tmp_path / "source tree"
    source.mkdir()
    output = tmp_path / "build"
    output.mkdir()
    (source / "h").mkdir()
    (source / "h/aconfig.h").write_bytes(b"configuration")
    (output / "sa_config.h").write_bytes(b"configuration")
    dependency = output / "compiled.d"
    paths = [source / "unit.c", source / "included$part.c", output / "sa_config.h"]
    escaped = [p.as_posix().replace("$", "$$").replace(" ", "\\ ") for p in paths]
    dependency.write_text("object: " + " \\\n  ".join(escaped) + "\n")
    monkeypatch.setattr(native, "SOURCES", ("unit.c",))
    assert native.upstream_dependencies([dependency], source, output) == [
        "h/aconfig.h", "included$part.c", "unit.c"
    ]
    monkeypatch.setattr(native, "SOURCES", ("unit.c", "absent.c"))
    with pytest.raises(ValueError, match="omits a compiled upstream unit"):
        native.upstream_dependencies([dependency], source, output)
    monkeypatch.setattr(native, "SOURCES", ("unit.c",))
    (output / "sa_config.h").write_bytes(b"changed")
    with pytest.raises(ValueError, match="configuration has changed"):
        native.upstream_dependencies([dependency], source, output)


def test_license_inventory_retains_nested_license_text_and_exact_notice(tmp_path, monkeypatch):
    source = tmp_path / "source"
    output = tmp_path / "output"
    (source / "cgats").mkdir(parents=True)
    output.mkdir()
    notice = '/* Copyright Example. Licensed with an "MIT" license: see License4.txt. */'
    (source / "cgats/parsstd.c").write_text(notice + "\nint sample;\n")
    license_bytes = b"Exact source license fixture\r\n"
    (source / "cgats/License4.txt").write_bytes(license_bytes)
    (output / "UPSTREAM.txt").write_text("Existing attribution.\n")
    monkeypatch.setattr(native, "SOURCES", ())
    inventory = native.retain_upstream_notices(source, output, ["cgats/parsstd.c"])
    entry = inventory["files"][0]
    assert entry["declared_license"] == "MIT"
    assert entry["role"] == "included_dependency"
    assert entry["license_texts"] == ["cgats/License4.txt"]
    assert not entry["license_text_in_notice"]
    assert notice in (output / "UPSTREAM.txt").read_text()
    assert (output / "upstream-licenses/cgats/License4.txt").read_bytes() == license_bytes
    assert inventory["license_texts"]["cgats/License4.txt"]["sha256"] == native.digest(
        license_bytes
    )
    assert not inventory["redistribution_review_completed"]


def test_license_inventory_refuses_missing_text_instead_of_inventing_terms(tmp_path):
    (tmp_path / "UPSTREAM.txt").write_text("Existing attribution.\n")
    (tmp_path / "unit.c").write_text(
        '/* Copyright Example. Licensed with an "MIT" license: see License4.txt. */'
    )
    with pytest.raises(ValueError, match="Missing upstream license text"):
        native.retain_upstream_notices(tmp_path, tmp_path, ["unit.c"])


def test_libusb_gpl_notice_retains_actual_shipped_full_license(tmp_path):
    (tmp_path / "UPSTREAM.txt").write_text("Existing attribution.\n")
    (tmp_path / "driver_api.h").write_text(
        "/* Copyright Example. GNU General Public License; either version 2 of the License,\n"
        "or (at your option) any later version. */\n"
    )
    license_bytes = b"Actual bundled GPL text fixture\n"
    (tmp_path / "License2.txt").write_bytes(license_bytes)
    inventory = native.retain_upstream_notices(tmp_path, tmp_path, ["driver_api.h"])
    entry = inventory["files"][0]
    assert entry["declared_license"] == "GPL-2.0-or-later"
    assert entry["license_texts"] == ["License2.txt"]
    assert not entry["license_text_in_notice"]
    assert (tmp_path / "upstream-licenses/License2.txt").read_bytes() == license_bytes


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
@pytest.mark.parametrize("serial", [["-DENABLE_SERIAL"], ["-DENABLE_FAST_SERIAL"],
                                    ["-DENABLE_SERIAL", "-DENABLE_FAST_SERIAL"]])
def test_compiled_serial_branch_requires_passive_metadata(monkeypatch, tmp_path, system, serial):
    monkeypatch.setattr(native.platform, "system", lambda: system)
    selected = "spectro/icoms_nt.c" if system == "Windows" else "spectro/icoms_ux.c"
    other = "spectro/icoms_ux.c" if system == "Windows" else "spectro/icoms_nt.c"
    (tmp_path / "spectro").mkdir()
    (tmp_path / other).write_text("unselected platform source still calls open(candidate);\n")

    def tree(body, *, marker=True):
        (tmp_path / selected).write_text(
            'devType fast_ser_dev_type(icoms *p, int tryhard, void *, void *);\n'
            'int serial_get_paths(icompaths *p, icom_type mask) {\n'
            + ('/* CM: passive catalog */\n' if marker else '')
            + body + '\n}\n')
        return tmp_path

    applied = {"patches": [{"path": selected}]}
    native.require_passive_serial(serial, applied, tree("return 0;"))
    with pytest.raises(ValueError, match="still calls fast_ser_dev_type"):
        native.require_passive_serial(serial, applied, tree("fast_ser_dev_type (icom, 0, 0, 0);"))
    with pytest.raises(ValueError, match="lacks the passive-serial marker"):
        native.require_passive_serial(serial, applied, tree("return 0;", marker=False))
    for call in ('open(candidate, 0)', 'CreateFile(candidate, 0)', 'CreateFileW(candidate, 0)'):
        with pytest.raises(ValueError, match="still opens a serial candidate"):
            native.require_passive_serial(serial, applied, tree(call + ';'))
    for unpatched in ({"patches": []}, {"patches": [{"path": other}]}, {}):
        with pytest.raises(ValueError, match="without driver_manifest's passive-serial"):
            native.require_passive_serial(serial, unpatched, tmp_path)
    with pytest.raises(ValueError, match="requires the compiled source"):
        native.require_passive_serial(serial, applied, None)
    native.require_passive_serial(["-DENABLE_USB"], {"patches": []}, None)


def test_the_usb_enumeration_filter_must_match_the_linked_driver_set():
    """Current workers enumerate every supported model and reject narrow filter records."""
    broad = {"driver_scope": "all_public", "patches": [{"path": "spectro/inst.c"}]}
    assert native.record_usb_match_scope(broad) == "all_supported"
    assert broad["usb_match_scope"] == "all_supported"

    for scope, patches in (
        ("all_public", [{"path": "spectro/insttypes.c"}]),   # broad set, narrow filter
        ("i1pro3", [{"path": "spectro/insttypes.c"}]),
        ("i1pro", [{"path": "spectro/insttypes.c"}]),
        ("i1pro3", [{"path": "spectro/inst.c"}]),
        ("i1pro", []),
        ("typo", []),
        (None, []),
    ):
        message = "narrowed USB matcher" if scope == "all_public" else "Unknown driver scope"
        with pytest.raises(ValueError, match=message):
            native.record_usb_match_scope({"driver_scope": scope, "patches": patches})


@pytest.mark.parametrize("action", ["ack", "connect"])
def test_removed_actions_are_refused_before_writing(action):
    from io import BytesIO
    from types import SimpleNamespace

    session = object.__new__(protocol.HelperSession)
    session.operation_id = "unsupported-action"
    session.process = SimpleNamespace(stdin=BytesIO())
    with pytest.raises(protocol.ProtocolError, match="Unsupported action"):
        session.send(action)
    assert session.process.stdin.getvalue() == b""


@pytest.mark.parametrize("argument", ["", "0", "-1", "nan", "inf", "1e999", "1\ncancel"])
def test_read_allowance_rejects_invalid_seconds_before_writing(argument):
    from io import BytesIO
    from types import SimpleNamespace

    session = object.__new__(protocol.HelperSession)
    session.operation_id = "allowance-test"
    session.process = SimpleNamespace(stdin=BytesIO())
    with pytest.raises(protocol.ProtocolError):
        session.send("read_allowance", argument)
    assert session.process.stdin.getvalue() == b""


def test_read_allowance_sends_fractional_seconds():
    from io import BytesIO
    from types import SimpleNamespace

    session = object.__new__(protocol.HelperSession)
    session.operation_id = "allowance-test"
    session.process = SimpleNamespace(stdin=BytesIO())
    session.send("read_allowance", "1.75e2")
    assert session.process.stdin.getvalue() == b"v1 allowance-test read_allowance 1.75e2\n"


@pytest.mark.parametrize(
    "behavior", [
        "hold_stdout", "wait_timeout", "stderr_full", "closed_input", "allowance", "decode_error",
        "thread_start_error"])
def test_interactive_proof_has_one_deadline_and_always_reaps_child(
        tmp_path, monkeypatch, behavior):
    generic = load("generic_session_check", TOOLS / "native_argyll/generic_session_check.py")
    monkeypatch.setattr(generic, "RUN_TIMEOUT_S", 1.0)
    worker = tmp_path / "inert-worker"
    worker.write_text("#!" + sys.executable + "\n" + """
import json, os, sys, time
def emit(event):
    print(json.dumps(dict(event=event, protocol_version=1, provenance="simulated")), flush=True)
behavior = """ + repr(behavior) + """
if behavior == "closed_input":
    os.close(0)
    sys.exit(0)
elif behavior in ("hold_stdout", "wait_timeout", "thread_start_error"):
    sys.stderr.write("retained timeout diagnostic\\n")
    sys.stderr.flush()
    emit("terminal")
    if behavior == "wait_timeout":
        os.close(1)
    time.sleep(60)
elif behavior == "stderr_full":
    sys.stderr.write("diagnostic" * 100000)
    sys.stderr.flush()
    emit("terminal")
elif behavior == "decode_error":
    emit("terminal")
elif behavior == "allowance":
    emit("read_allowance_required")
    answer = sys.stdin.readline()
    if answer != "v1 generic-check read_allowance 5\\n":
        sys.exit(2)
    emit("terminal")
""")
    worker.chmod(0o755)
    real_popen = generic.subprocess.Popen
    children = []

    def observe(argv, *args, **kwargs):
        assert Path(argv[0]) == worker
        child = real_popen([sys.executable, str(worker), *argv[1:]], *args, **kwargs)
        children.append((child, kwargs["stderr"]))
        if behavior in ("closed_input", "decode_error"):
            child.wait(timeout=2)
        if behavior == "decode_error":
            stream = child.stdout

            class FaultingOutput:
                def __iter__(self):
                    yield stream.readline()
                    raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

                @property
                def closed(self):
                    return stream.closed

                def close(self):
                    stream.close()

            child.stdout = FaultingOutput()
        return child

    monkeypatch.setattr(generic.subprocess, "Popen", observe)
    if behavior == "thread_start_error":
        def refuse_start(self):
            raise RuntimeError("inert thread start failure")

        monkeypatch.setattr(generic.threading.Thread, "start", refuse_start)
    kwargs = {"hold_stdin": True}
    if behavior == "allowance":
        kwargs["read_allowance_s"] = 5
    if behavior == "closed_input":
        kwargs["answers"] = ("acknowledge",)
    started = time.monotonic()
    try:
        if behavior in ("hold_stdout", "wait_timeout"):
            with pytest.raises(subprocess.TimeoutExpired) as failed:
                generic._run(worker, "emissive_spot", **kwargs)
            assert json.loads(failed.value.output)["event"] == "terminal"
            assert failed.value.stderr == "retained timeout diagnostic\n"
        elif behavior == "closed_input":
            with pytest.raises(OSError) as closed:
                generic._run(worker, "emissive_spot", **kwargs)
            assert native_io._closed_pipe(closed.value)
        elif behavior == "decode_error":
            with pytest.raises(UnicodeDecodeError, match="invalid start byte"):
                generic._run(worker, "emissive_spot", **kwargs)
        elif behavior == "thread_start_error":
            with pytest.raises(RuntimeError, match="inert thread start failure"):
                generic._run(worker, "emissive_spot", **kwargs)
        else:
            code, events = generic._run(worker, "emissive_spot", **kwargs)
            assert code == 0 and events[-1]["event"] == "terminal"
        assert len(children) == 1
        child, errors = children[0]
        assert child.poll() is not None
        if behavior in ("hold_stdout", "wait_timeout", "thread_start_error"):
            assert time.monotonic() - started < generic.RUN_TIMEOUT_S + 3.0
            if sys.platform != "win32":
                assert child.returncode == -signal.SIGKILL
        assert child.stdin.closed and child.stdout.closed and errors.closed
    finally:
        for child, errors in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            for stream in (child.stdin, child.stdout, errors):
                try:
                    stream.close()
                except OSError as error:
                    if not native_io._closed_pipe(error):
                        raise


@pytest.mark.parametrize("blocked_phase", [
    None, "read", "wait", "close_broken", "close_einval", "close_unrelated",
])
def test_interactive_proof_spends_one_budget_across_replies_and_exit(monkeypatch, blocked_phase):
    import errno
    import io
    import queue
    from types import SimpleNamespace

    generic = load("generic_session_check", TOOLS / "native_argyll/generic_session_check.py")
    if blocked_phase == "close_einval":
        monkeypatch.setattr(native_io, "sys", SimpleNamespace(platform="win32"))
    clock = [0.0]
    read_costs = iter([0.25, 0.25, 0.75 if blocked_phase == "read" else 0.25])
    read_budgets = []
    wait_budgets = []
    replies = "".join(json.dumps(dict(event=event, protocol_version=1,
                                    provenance="simulated")) + "\n"
                      for event in ("read_allowance_required", "terminal"))

    class TimedQueue(queue.Queue):
        def get(self, block=True, timeout=None):
            if block:
                read_budgets.append(timeout)
                cost = next(read_costs)
                if timeout < cost:
                    clock[0] += timeout
                    raise queue.Empty
                clock[0] += cost
            return super().get(block=False)

    messages = TimedQueue()

    class Input(io.StringIO):
        def close(self):
            self.commands = self.getvalue()
            super().close()
            if blocked_phase == "close_broken":
                raise BrokenPipeError(errno.EPIPE, "inert exited child")
            if blocked_phase == "close_einval":
                raise OSError(errno.EINVAL, "inert Windows exited child")
            if blocked_phase == "close_unrelated":
                raise OSError(errno.ENOSPC, "inert unrelated close failure")

    class Child:
        def __init__(self):
            self.stdin = Input()
            self.stdout = io.StringIO(replies)
            self.returncode = None
            self.killed = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            wait_budgets.append(timeout)
            if self.returncode is None:
                cost = 0.375 if blocked_phase == "wait" else 0.125
                if timeout < cost:
                    clock[0] += timeout
                    raise subprocess.TimeoutExpired("inert-worker", timeout)
                clock[0] += cost
                self.returncode = 0
            return self.returncode

    child = Child()
    error_files = []
    readers = []

    def popen(*args, **kwargs):
        error_files.append(kwargs["stderr"])
        kwargs["stderr"].write("retained budget diagnostic\n")
        return child

    class Reader:
        ident = None

        def __init__(self, *, target, daemon):
            self.target = target
            self.joined = False
            readers.append(self)

        def start(self):
            self.ident = 1
            self.target()

        def join(self, timeout):
            self.joined = True

    monkeypatch.setattr(generic, "RUN_TIMEOUT_S", 1.0)
    monkeypatch.setattr(generic, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(generic, "queue", SimpleNamespace(
        Queue=lambda: messages, Empty=queue.Empty))
    monkeypatch.setattr(generic, "threading", SimpleNamespace(Thread=Reader))
    monkeypatch.setattr(generic, "subprocess", SimpleNamespace(
        Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired,
        CompletedProcess=subprocess.CompletedProcess))
    arguments = dict(hold_stdin=True, read_allowance_s=5)
    if blocked_phase not in {"read", "wait", "close_unrelated"}:
        code, events = generic._run(Path("inert-worker"), "emissive_spot", **arguments)
        assert code == 0 and events[-1]["event"] == "terminal"
        assert clock[0] == 0.875 and not child.killed
    elif blocked_phase == "close_unrelated":
        with pytest.raises(OSError, match="inert unrelated close failure") as failed:
            generic._run(Path("inert-worker"), "emissive_spot", **arguments)
        assert failed.value.errno == errno.ENOSPC
    else:
        with pytest.raises(subprocess.TimeoutExpired) as failed:
            generic._run(Path("inert-worker"), "emissive_spot", **arguments)
        assert failed.value.timeout == 1.0 and failed.value.output == replies
        assert failed.value.stderr == "retained budget diagnostic\n"
        assert clock[0] == 1.0 and child.killed
    assert read_budgets == [1.0, 0.75, 0.5]
    assert wait_budgets == ([None] if blocked_phase == "read" else [0.25, None])
    assert child.returncode is not None and wait_budgets[-1] is None
    assert child.stdin.commands == "v1 generic-check read_allowance 5\n"
    assert child.stdin.closed and child.stdout.closed and error_files[0].closed
    assert len(readers) == 1 and readers[0].joined


@pytest.mark.parametrize("enabled", [False, True])
def test_cr_driver_unit_receipt_reports_actual_sanitizer_coverage(tmp_path, enabled):
    worker = tmp_path / "inert-worker"
    worker.write_bytes(b"record only; never executed")
    result = native.cr250_driver_unit_results(
        "cr250_driver_test: 7 checks, 0 failures", worker, sanitize=enabled)
    assert result["sanitize"] is enabled


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_contributed_sanitizer_unavailability_is_explicit(tmp_path, monkeypatch, system):
    report = {"commands": []}

    def unavailable(*args):
        raise RuntimeError("compiler does not supply the sanitizer runtime")

    monkeypatch.setattr(native, "command", unavailable)
    if system == "Windows":
        assert native.contributed_sanitizer_support(tmp_path, report, "inert-cc", system) is False
    else:
        with pytest.raises(RuntimeError, match="does not supply"):
            native.contributed_sanitizer_support(tmp_path, report, "inert-cc", system)
    assert report["contributed_sanitizers"]["enabled"] is False
    assert report["contributed_sanitizers"]["status"] == "unavailable"


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_broad_source_union_preserves_target_exclusions(tmp_path, system):
    source = tmp_path / "source/spectro"
    source.mkdir(parents=True)
    (source / "Jamfile").write_text(
        "SER_INSTS = fixture.c ;\nUSB_INSTS = ;\nFAST_SER_INSTS = ;\nSER_USB_INSTS = ;\n")
    (source / "fixture.c").write_text("/* Source selection fixture; never compiled. */")
    sources = native.library_sources(source.parent, "all_public", system)
    assert "spectro/fixture.c" in sources and "spectro/i1pro3.c" in sources
    assert ("spectro/pollem.c" in sources) is (system != "Windows")
    assert len(set(sources)) == len(sources)


def test_windows_runtime_hid_exports_match_pinned_loader(argyll_archive):
    import inspect
    import re
    import zipfile

    with zipfile.ZipFile(argyll_archive) as archive:
        source = archive.read("Argyll_V3.5.0/spectro/hidio.c").decode()
    actual = set(re.findall(r'GetProcAddress\(LoadLibrary\("HID"\), "(HidD_\w+)"', source))
    checked = set(re.findall(r'"(HidD_\w+)"', inspect.getsource(native.prove_platform_io)))
    assert checked == actual and len(actual) == 3


@pytest.mark.parametrize("target", ["aarch64-w64-mingw32", "x86_64-linux-gnu"])
def test_windows_target_abi_is_checked_before_probe_execution(tmp_path, monkeypatch, target):
    monkeypatch.setattr(native.platform, "system", lambda: "Windows")
    monkeypatch.setattr(native.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(native.shutil, "which", lambda name: name)
    output = tmp_path / "build"

    def prepare(*args, **kwargs):
        output.mkdir()
        return output / "source", {"status": "failed", "commands": []}

    monkeypatch.setattr(native, "prepare", prepare)
    monkeypatch.setattr(native, "command", lambda argv, *args:
                        target if "-dumpmachine" in argv else "test compiler")
    monkeypatch.setattr(native, "prove_platform_io", lambda *args: pytest.fail("Wrong target ran"))
    assert native.main(["--substituted-binding", "--archive", "unused.zip",
                        "--output", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed" and "Windows-GNU compiler" in report["error"]


def test_native_proof_assertion_is_retained_in_failure_report(tmp_path, monkeypatch):
    output = tmp_path / "build"
    monkeypatch.setattr(native.shutil, "which", lambda name: name)
    monkeypatch.setattr(native.platform, "machine", lambda: "AMD64")

    def prepare(*args, **kwargs):
        output.mkdir()
        return output / "source", {"status": "failed", "commands": []}

    def failed_proof(*args):
        raise AssertionError("source-proof negative control was not exercised")

    monkeypatch.setattr(native, "prepare", prepare)
    monkeypatch.setattr(native, "command", lambda argv, *args:
                        "x86_64-w64-mingw32" if "-dumpmachine" in argv else "test compiler")
    monkeypatch.setattr(native, "prove_platform_io", failed_proof)
    assert native.main(["--substituted-binding", "--archive", "unused.zip",
                        "--output", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed" and "negative control" in report["error"]


def test_source_inventory_canonicalizes_the_builder_path(argyll_archive, tmp_path, monkeypatch):
    # Python may preserve a symlink/.. spelling in __file__, while HERE is canonical.
    # Inventory keys must still bind the one actual build script in the source root.
    alias = native.HERE / ".." / "tools" / "argyll_native_check.py"
    monkeypatch.setattr(native, "__file__", str(alias))
    _, report = native.prepare(argyll_archive, tmp_path / "canonical-inventory")
    inputs = report["original_check_source_sha256"]
    assert inputs["tools/argyll_native_check.py"] == native.digest(alias.read_bytes())
    assert not any(".." in Path(name).parts for name in inputs)


@pytest.mark.parametrize(("target", "machine", "expected"), [
    ("x86_64-w64-mingw32", "AMD64", True),
    ("x86_64-w64-windows-gnu", "x86_64", True),
    ("aarch64-w64-mingw32", "ARM64", True),
    ("aarch64-w64-windows-gnu", "aarch64", True),
    ("aarch64-w64-mingw32", "AMD64", False),
    ("x86_64-w64-mingw32", "ARM64", False),
    ("aarch64-linux-gnu", "ARM64", False),
])
def test_windows_compiler_matches_native_architecture(target, machine, expected):
    assert native.windows_compiler_matches(target, machine) is expected


@pytest.mark.parametrize(("format_name", "target"), [
    ("coff-x86-64", "aarch64-w64-mingw32"),
    ("coff-arm64", "x86_64-w64-mingw32"),
])
def test_windows_imports_refuse_a_different_machine(format_name, target):
    inspected = (f"file format {format_name}\n\texecutable\nMagic 020b (PE32+)\n"
                 " DLL Name: KERNEL32.dll\n")
    with pytest.raises(ValueError, match="matching native PE32"):
        native.windows_dll_imports(inspected, compiler_target=target)
