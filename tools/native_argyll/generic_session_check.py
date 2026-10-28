"""Exercise the shared session against substituted instrument personas and, when
the CR series is composed, the real CR driver over a simulated serial link.

This is the artifact that turns "is the ladder generic?" from an argument into a
build result. The same ``session.c`` is linked to two different substituted
drivers and driven through the four implemented acquisition verbs:

======================  =========================  ==============================
persona                 modelled on                workflow proved
======================  =========================  ==============================
i1 Display 3            ``i1d3.c:3902-3919``       spot, telephoto, ambient; no
                                                   spectrum at all
SpectroScan             ``ss.c:385-432``           ``ref_xy`` sheet, positioned
                                                   spot on a motorised table
SpectroScanT            ``ss.c:413-422``           capabilities withdrawn by
                                                   ``set_mode``
DTP51                   ``dtp51.c:805-809``        switch trigger ONLY: the
                                                   instrument a ladder that
                                                   assumes inst_opt_trig_prog
                                                   cannot bind at all
DTP20                   ``dtp20.c:1456-1461``      a stored chart, and the runtime
                                                   status that decides whether to
                                                   read one
DTP41                   ``dtp41.c:1131-1158``         strip pass, no scan tolerance
ColorMunki              ``munki.c:129-160``        strip pass with scan tolerance
======================  =========================  ==============================

Every mode word is composed in Python by ``argyll_capability.mode_word`` and
handed to the helper as a number. Selection passes the instrument name to C;
shared capability negotiation and bounded native driver policies determine
behavior. Later comparisons to per-device helpers refer to retired code.

No hardware. The stubs are wholly substituted and talk to nothing.
"""

import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RUN_TIMEOUT_S = 30

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from native_argyll import sanitizers  # noqa: E402

from color_measurement.argyll_capability import (  # noqa: E402
    CAPABILITY2,
    decode_modes,
    mode_word,
    parse_mode_name,
    workflow_kind,
)
from color_measurement.native_protocol import _closed_pipe  # noqa: E402


def require(condition: bool, message: str) -> None:
    """Active even under -O, matching the other native checks."""
    if not condition:
        raise RuntimeError(message)


# (text, accepted) for the probe below: an accepted value is 0 or 1, or a C
# expression over ``real_strtod`` for the two representable subnormals.
_NUMERIC_CASES = (("1e-4000", "0"), ("-1e-4000", "0"), ("0x1p-100000", "0"),
                  ("0e-4000", "1"), ("-0.000e-4000", "1"), ("0x0p-100000", "1"),
                  ("1e-320", 'real_strtod("1e-320",&end)!=0.0'),
                  ("0x1p-1074", 'real_strtod("0x1p-1074",&end)!=0.0'))


def _check_numeric_parser(compiler: str, output: Path) -> dict:
    """Compile the real session parser with a strtod that clears underflow errno.

    This models a C library that returns zero for an underflow without ERANGE;
    the decision must follow the supplied mantissa, not the library's errno.
    The case count the probe prints and this report carries is the length of
    ``_NUMERIC_CASES``, not a number typed beside it.
    """
    source = (Path(__file__).with_name("session.c")).read_text()
    start = "static int nonzero_mantissa("
    end = "static int ints("
    require(source.count(start) == source.count(end) == 1,
            "session numeric parser extraction changed")
    parser = source[source.index(start):source.index(end)]
    summary = f"numeric parser: {len(_NUMERIC_CASES)} cases"
    probe = ("#include <ctype.h>\n#include <errno.h>\n#include <math.h>\n"
             "#include <stdio.h>\n#include <stdlib.h>\n"
             "static double real_strtod(const char *s, char **e) { return strtod(s,e); }\n"
             "static double zero_errno_strtod(const char *s, char **e) {\n"
             "    double v = real_strtod(s,e); errno = 0; return v;\n}\n"
             "#define strtod zero_errno_strtod\n" + parser +
             "#undef strtod\n"
             "static int check(const char *text, int accept) {\n"
             "    double value = 9.0; int got = doubles(text,&value,1);\n"
             "    if (got != accept) { printf(\"FAIL numeric %s got %d\\n\",text,got); "
             "return 1; }\n"
             "    return 0;\n}\n"
             "int main(void) {\n"
             "    char *end; int failures = 0;\n"
             + "".join(f'    failures += check("{text}",{accepted});\n'
                       for text, accepted in _NUMERIC_CASES)
             + f'    printf("{summary}, %d failures\\n",failures);\n'
             "    return failures != 0;\n}\n")
    target = output / "session-numeric-probe.c"
    target.write_text(probe)
    executable = output / ("session-numeric-probe.exe" if sys.platform == "win32"
                           else "session-numeric-probe")
    built = subprocess.run([compiler, "-std=gnu99", "-O1", str(target), "-lm", "-o",
                            str(executable)], capture_output=True, text=True, timeout=120)
    (output / "session-numeric-compile.log").write_text(built.stdout + built.stderr)
    built.check_returncode()
    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    (output / "session-numeric-run.log").write_text(run.stdout + run.stderr)
    require(run.returncode == 0 and f"{summary}, 0 failures" in run.stdout,
            "session numeric parser changed under a zero-errno strtod")
    return {"status": "passed", "cases": len(_NUMERIC_CASES),
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "probe_sha256": hashlib.sha256(probe.encode()).hexdigest()}


def _run(worker: Path, mode: str, **options):
    """Drive one session (``_session``) and return its exit status and wire events."""
    return _events(_session(worker, mode, **options), options.get("fixture", "success"))


def _session(worker: Path, mode: str, *, fixture="success", persona=None, facilities=(),
             spectral=False, answers=(), variable="CM_COLORIMETER_FIXTURE",
             serial="CM-COLORIMETER-0001",
             instrument="X-Rite i1 DisplayPro, ColorMunki Display",
             identify_serial=False, hold_stdin=False, answer_delay_s=0.0, extra_env=None,
             cancel_at_read_allowance=False,
             **flags) -> subprocess.CompletedProcess:
    """Run one session and return the finished process. ``flags`` are ladder flags
    with ``_`` for ``-``.

    ``hold_stdin`` keeps the command pipe open until the worker exits, as the Python
    client does. End of input is how a supervisor cancels (session.c command()),
    so a driver that polls the operator callback while armed -- cr250.c does, before
    M -- reads a closed pipe as a cancellation. Personas that never poll during a
    read cannot tell the difference, which is why the default stays as it was.

    ``answer_delay_s`` delays the read-allowance grant after the worker's
    request, the interval session.c read_allowance() charges. Delaying from
    process start instead would charge nothing once start-up outlasts it.

    ``cancel_at_read_allowance`` queues the grant and cancel in one write. The
    grant reader consumes one line; the real driver's armed callback consumes
    the cancel before M/M5. No timing assumption or invented prompt is needed.
    """
    need2 = 0
    for flag in facilities:
        need2 |= CAPABILITY2[flag]
    # The same selection a real attach unit takes. A substituted instrument is
    # driven through the identical command line, so nothing about the caller
    # changes when the attach unit becomes the real one.
    argv = [
        str(worker),
        "--usb-index", "1",
        "--expected-serial", serial,
        "--instrument", instrument,
        # The optional fourth selection pair, always sent: a substituted
        # instrument must accept exactly the command line a real one takes.
        "--allow-serial-identification", str(int(identify_serial)),
        "--mode-word", str(mode_word(mode, spectral=spectral)),
        "--facilities", str(need2),
    ]
    # Resolve an intentionally numeric probe operand against the same native
    # catalogue a real frontend observes, before testing the selector ladder.
    if "display_type" in flags and "display_catalogue_id" not in flags:
        probe_flags = {key: value for key, value in flags.items()
                       if key != "display_type"}
        probe_flags["read"] = 0
        probe_flags["calibrate"] = 0
        probe_code, probe_events = _run(
            worker, mode, fixture=fixture, persona=persona, spectral=spectral,
            extra_env=extra_env, variable=variable, serial=serial, instrument=instrument,
            identify_serial=identify_serial, facilities=facilities, **probe_flags)
        connected = next((e for e in probe_events if e["event"] == "connected"), None)
        entries = [e for e in probe_events if e["event"] == "display_type"]
        index = flags["display_type"]
        if connected and index < len(entries):
            flags["display_catalogue_id"] = connected["display_types"]["catalogue_id"]
            flags["display_choice_id"] = entries[index]["id"]
    for flag, value in flags.items():
        argv += [f"--{flag.replace('_', '-')}", str(value)]
    environment = dict(os.environ, **{variable: fixture}, **(extra_env or {}))
    if persona:
        environment["CM_CHART_PERSONA"] = persona
    commands = "".join(f"v1 generic-check {answer}\n" for answer in answers)
    if hold_stdin or "read_allowance_s" in flags:
        deadline = time.monotonic() + RUN_TIMEOUT_S
        lines = []
        messages = queue.Queue()
        # A file drains stderr without sharing the interactive stdout pipe.
        with tempfile.TemporaryFile(mode="w+t") as errors:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=errors, text=True, env=environment)

            def read_lines():
                try:
                    for line in process.stdout:
                        messages.put(line)
                except Exception as exc:
                    messages.put(exc)
                finally:
                    messages.put(None)

            reader = None
            timeout = None
            try:
                reader = threading.Thread(target=read_lines, daemon=True)
                reader.start()
                process.stdin.write(commands)
                process.stdin.flush()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, RUN_TIMEOUT_S)
                    try:
                        line = messages.get(timeout=remaining)
                    except queue.Empty as exc:
                        raise subprocess.TimeoutExpired(argv, RUN_TIMEOUT_S) from exc
                    if line is None:
                        break
                    if isinstance(line, Exception):
                        raise line
                    lines.append(line)
                    if json.loads(line).get("event") == "read_allowance_required":
                        if answer_delay_s:
                            time.sleep(answer_delay_s)
                        seconds = flags["read_allowance_s"]
                        grant = f"v1 generic-check read_allowance {seconds}\n"
                        if cancel_at_read_allowance:
                            grant += "v1 generic-check cancel\n"
                        process.stdin.write(grant)
                        process.stdin.flush()
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                timeout = exc
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                try:
                    process.stdin.close()
                except OSError as error:
                    if not _closed_pipe(error):
                        raise
                finally:
                    if reader is not None and reader.ident is not None:
                        reader.join(timeout=1)
                    process.stdout.close()
            errors.seek(0)
            stderr = errors.read()
            if timeout is not None:
                # The reader may have queued more output during cleanup.
                while not messages.empty():
                    line = messages.get_nowait()
                    if isinstance(line, str):
                        lines.append(line)
                raise subprocess.TimeoutExpired(
                    argv, RUN_TIMEOUT_S, output="".join(lines), stderr=stderr) from timeout
            completed = subprocess.CompletedProcess(
                argv, process.returncode, "".join(lines), stderr)
    else:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
            env=environment, check=False,
            input=commands,
        )
    return completed


def _events(completed: subprocess.CompletedProcess, fixture: str):
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    require(bool(events), f"{fixture}: helper produced no wire output: {completed.stderr[-400:]}")
    for event in events:
        require(event.get("protocol_version") == 1, f"{fixture}: bad envelope")
        require(event.get("provenance") == "simulated", f"{fixture}: lost substituted provenance")
    require(events[-1]["event"] == "terminal", f"{fixture}: no terminal event")
    return completed.returncode, events


def _of(events, kind):
    return [event for event in events if event["event"] == kind]


def _failed(events, stage, label):
    error = next(event for event in events if event["event"] == "error")
    require(error["stage"] == stage, f"{label}: expected {stage}, got {error['stage']}")


def exercise_colorimeter(worker: Path, cases: list) -> None:
    """The i1 Display 3 persona: emission routes, and no spectrum anywhere."""
    run = lambda *a, **k: _run(worker, *a, **k)  # noqa: E731

    # A telephoto emission bind on a colorimeter: a route and a result shape that
    # neither per-device helper can express.
    code, events = run("emissive_tele")
    require(code == 0, "telephoto bind must succeed")
    connected = _of(events, "connected")[0]
    require(connected["spectral"] is False, "a colorimeter must not report a spectrum")
    require(connected["serial"] == "CM-COLORIMETER-0001", "serial must be reported")
    confirmed = connected["mode"]
    require(
        decode_modes(confirmed) == ("emis_tele",),
        f"confirmed mode must decode to exactly emis_tele, got {decode_modes(confirmed)}",
    )
    require(workflow_kind("emissive_tele") == "spot", "telephoto is a spot workflow")
    cases.append({"case": "telephoto_colorimeter", "status": "passed", "mode": confirmed})

    # The other two routes this instrument advertises, to show the ladder is not
    # tuned to one of them.
    for mode in ("emissive_spot", "ambient_spot"):
        code, events = run(mode)
        require(code == 0, f"{mode} bind must succeed")
        connected = _of(events, "connected")[0]
        route = parse_mode_name(mode)[0]
        require(decode_modes(connected["mode"]) == (route,), f"{mode} confirmed wrong")
        cases.append({"case": mode, "status": "passed"})

    # The whole point, end to end: a colorimeter reading. XYZ present, spectrum
    # null -- never zeros, never an empty grid. Neither per-device helper can
    # produce this shape; both hard-require a spectrum.
    code, events = run("emissive_spot", read=1)
    require(code == 0, "colorimeter read must succeed")
    reported = _of(events, "result")[0]["backend_reported"]
    require(reported["spectrum"] is None, "a colorimeter's spectrum must be null")
    require(reported["XYZ"] == [21.5, 24.0, 9.75], f"XYZ not carried: {reported['XYZ']}")
    require(reported["XYZ_v"] == 1, "XYZ must be marked valid")
    cases.append({"case": "colorimeter_read", "status": "passed"})

    # An instrument advertising no spectral bit that returns bands anyway is a
    # fault, not something to publish quietly.
    code, events = run("emissive_spot", fixture="fabricated_spectrum", read=1)
    require(code == 1, "a spectrum from a non-spectral instrument must fail")
    _failed(events, "unexpected_spectrum", "fabricated_spectrum")
    cases.append({"case": "unexpected_spectrum_refused", "status": "passed"})

    # A caller that REQUIRES a spectrum must refuse this instrument rather than
    # bind and return nothing. Missing spectral is a fault for a spectrophotometer
    # and normal for a colorimeter; no capability word distinguishes them, so the
    # caller states which it expects.
    code, events = run("emissive_spot", spectral_required=1)
    require(code == 1, "a spectral-required caller must fail on a colorimeter")
    _failed(events, "spectral_unavailable", "spectral_required")
    cases.append({"case": "spectral_required_refused", "status": "passed"})

    # A facility the instrument does not advertise must be refused before any mode
    # is set, by capability rather than by device name.
    code, events = run("emissive_spot", facilities=("xy_position",))
    require(code == 1, "an unavailable facility must fail the bind")
    _failed(events, "facilities_unavailable", "xy_position")
    cases.append({"case": "facility_unavailable", "status": "passed"})

    # Positioning is refused on an instrument with no table, by capability.
    code, events = run("emissive_spot", read=1, position=1)
    require(code == 1, "positioning a handheld colorimeter must fail")
    _failed(events, "positioning_unavailable", "position")
    cases.append({"case": "positioning_refused", "status": "passed"})

    # -- colorimeter corrections ------------------------------------------------
    # A colorimeter reads through three fixed filters, so its XYZ error depends on
    # the spectrum it is shown. Without a correction it returns numbers that look
    # valid and are wrong by a display-dependent amount. Preserve the selected
    # correction rather than publishing that bias. Both corrections are capability-gated
    # (inst.h:362-363) and both are CLEARED by set_disptype (inst.h:843-845).
    #
    # The stub checks the values, not just the call: a matrix that arrives
    # transposed or rescaled still binds cleanly and then biases every reading.
    matrix = "0.5,-0.25,0.125,1.5,2.25,-3.375,-0.0625,4.5,6.75"
    correction = dict(correction_matrix=matrix, correction_dtech=0x3510, correction_cbid=1)
    code, events = run("emissive_spot", fixture="matrix_roundtrip", read=1, **correction)
    require(code == 0, "a ccmx matrix must bind and read")
    require(len(_of(events, "result")) == 1, "a corrected read still returns one patch")
    cases.append({"case": "correction_matrix_applied", "status": "passed"})

    # A SECOND matrix scenario, disjoint in every field. One scenario pins only
    # "the values that scenario uses" -- a helper hardcoding the matrix, the
    # dtech or the cbid passes it. Two that disagree cannot both be satisfied by
    # any constant, which is what actually closes the class.
    alternate = dict(
        correction_matrix="1.25,0.0625,-0.5,-2.75,0.375,8.0,0.03125,-1.5,0.25",
        correction_dtech=0x4010,   # "LED AMOLED", disptechs.c
        correction_cbid=3,
    )
    code, events = run("emissive_spot", fixture="matrix_roundtrip_alt", read=1, **alternate)
    require(code == 0, "a second, different matrix must bind and read")
    cases.append({"case": "correction_matrix_second_values", "status": "passed"})

    # The same matrix WITH a display type. set_disptype clears any correction, so
    # this passes only because the ladder applies the correction afterwards. The
    # stub refuses to read when a correction was applied and then cleared, which is
    # otherwise silent: every call returns inst_ok and the XYZ is just uncorrected.
    code, events = run("emissive_spot", fixture="matrix_roundtrip", read=1,
                       display_type=0, **correction)
    require(code == 0, "a correction must outlive the display type that clears it")
    cases.append({"case": "correction_survives_display_type", "status": "passed"})

    # An ambient reading does NOT carry the correction, and no capability word
    # says so: i1d3.c:2336-2360 multiplies ambient by ambi_cal alone, and
    # spyd2.c:3105-3127 branches on IMODETST(p->mode, inst_mode_emis_ambient) and
    # applies ccmat only in the other arm. Accepted, it would bind, return inst_ok
    # throughout, and publish an uncorrected reading that looks corrected.
    code, events = run("ambient_spot", fixture="matrix_roundtrip", read=1, **correction)
    require(code == 1, "a correction on an ambient read must be refused")
    _failed(events, "correction_not_applied_to_ambient", "ambient_correction")
    cases.append({"case": "correction_refused_for_ambient", "status": "passed"})

    # The same ambient read without a correction still works: the refusal is about
    # the combination, not about ambient.
    code, events = run("ambient_spot", read=1)
    require(code == 0, "an uncorrected ambient read must still succeed")
    cases.append({"case": "ambient_read_without_correction", "status": "passed"})

    # A spectral set travels as a file, not an argument: 601 bands x N samples
    # exceeds the 128 KiB Linux allows in one argv element (MAX_ARG_STRLEN).
    with tempfile.TemporaryDirectory(prefix="cm-ccss-check-") as directory:
        spectra = Path(directory) / "spectra-é-中-🧪.txt"
        rows = "\n".join(
            " ".join(repr((sample * 4 + band) * 0.25) for band in range(4))
            for sample in range(5)
        )
        # Five samples and norm 2.5: a hardcoded count or norm in the helper would
        # survive a fixture of three samples at norm 100.0, and a truncated count
        # changes i1d3's algorithm (i1d3.c:2584 branches on nsamp == 3).
        spectra.write_text(f"4 400.0 700.0 2.5 5\n{rows}\n")
        # display_type too, and for the same reason as the matrix case:
        # inst.h:844-845 says set_disptype clears BOTH slots, so pinning the
        # ordering for col_cor_mat alone leaves col_cal_spec_set free to move.
        code, events = run("emissive_spot", fixture="spectra_roundtrip", read=1,
                           display_type=0, calibration_spectra=str(spectra),
                           correction_dtech=0x3510)
        require(code == 0, "a ccss spectral set must bind, survive the display type, and read")
        cases.append({"case": "correction_spectra_applied", "status": "passed"})

        # The spectral counterpart: three samples of six bands over a different
        # grid at norm 1.0, so the count, the band count, the endpoints and the
        # norm are all unhardcodable.
        alt = Path(directory) / "spectra_alt-é-中-🧪.txt"
        alt_rows = "\n".join(
            " ".join(repr((sample * 6 + band) * 0.5) for band in range(6))
            for sample in range(3)
        )
        alt.write_text(f"6 380.0 730.0 1.0 3\n{alt_rows}\n")
        code, events = run("emissive_spot", fixture="spectra_roundtrip_alt", read=1,
                           calibration_spectra=str(alt), correction_dtech=0x4010)
        require(code == 0, "a second, different spectral set must bind and read")
        cases.append({"case": "correction_spectra_second_values", "status": "passed"})

        # Each correction is gated on its OWN capability bit. Ten drivers offer
        # inst2_ccmx and only two offer inst2_ccss, so a persona advertising both
        # cannot show the ladder checks the right one -- swapping the gates passed.
        code, events = run("emissive_spot", fixture="ccss_only", read=1, **correction)
        require(code == 1, "a matrix must be refused by an instrument offering only ccss")
        _failed(events, "correction_matrix_unavailable", "ccss_only")
        code, events = run("emissive_spot", fixture="ccmx_only", read=1,
                           calibration_spectra=str(spectra), correction_dtech=0x3510)
        require(code == 1, "a spectral set must be refused by an instrument offering only ccmx")
        _failed(events, "correction_spectral_unavailable", "ccmx_only")
        cases.append({"case": "correction_gated_on_its_own_capability", "status": "passed"})

        # The driver's own refusal is reported, not swallowed.
        code, events = run("emissive_spot", fixture="correction_failure", read=1, **correction)
        require(code == 1, "a refused correction must fail the bind")
        _failed(events, "set_correction_matrix", "correction_failure")
        cases.append({"case": "correction_refusal_reported", "status": "passed"})

    # A spectral file that is absent, malformed or inconsistent with its own
    # header must refuse the session rather than bind without the correction the
    # caller asked for. That path exits before the wire opens, so it is checked
    # directly rather than through the event stream.
    with tempfile.TemporaryDirectory(prefix="cm-ccss-bad-") as directory:
        bad = Path(directory)
        cases_by_name = {
            # header promises three samples of four bands, body holds two samples
            "short_body": "4 400.0 700.0 100.0 3\n0 0.25 0.5 0.75\n1 1.25 1.5 1.75\n",
            # body holds one value more than the header accounts for
            "long_body": ("4 400.0 700.0 100.0 3\n" + ("0 0.25 0.5 0.75\n" * 3) + "9\n"),
            "not_numeric": "4 400.0 700.0 100.0 3\nred green blue white\n",
            "non_finite": "4 400.0 700.0 100.0 3\n" + ("inf 0.25 0.5 0.75\n" * 3),
            "bands_over_limit": "602 400.0 700.0 100.0 3\n",
            "bands_integer_overflow": "2147483648 400.0 700.0 100.0 3\n",
            "bands_integer_underflow": "-2147483649 400.0 700.0 100.0 3\n",
            "bands_token_overlong": ("0" * 128 + "4 400.0 700.0 100.0 3\n"
                                     + ("0 0.25 0.5 0.75\n" * 3)),
            "bands_embedded_nul": ("4\x00junk 400.0 700.0 100.0 3\n"
                                   + ("0 0.25 0.5 0.75\n" * 3)),
            "zero_bands": "0 400.0 700.0 100.0 3\n",
            "negative_bands": "-1 400.0 700.0 100.0 3\n",
            "zero_samples": "4 400.0 700.0 100.0 0\n",
            "samples_over_limit": "4 400.0 700.0 100.0 1001\n",
            "samples_integer_overflow": "4 400.0 700.0 100.0 2147483648\n",
            "samples_token_overlong": ("4 400.0 700.0 100.0 " + "0" * 128 + "3\n"
                                       + ("0 0.25 0.5 0.75\n" * 3)),
            # norm is a divisor downstream (xspect.c:4380-4382), so zero or
            # negative is infinities and sign flips, not a scaled correction.
            "zero_norm": "4 400.0 700.0 0.0 3\n" + ("0 0.25 0.5 0.75\n" * 3),
            "negative_norm": "4 400.0 700.0 -1.0 3\n" + ("0 0.25 0.5 0.75\n" * 3),
            "non_finite_header": "4 400.0 inf 100.0 3\n" + ("0 0.25 0.5 0.75\n" * 3),
            # A non-finite NORM specifically: `norm <= 0.0` is false for inf and
            # for nan, so !isfinite(norm) is the only guard that can refuse them.
            "infinite_norm": "4 400.0 700.0 inf 3\n" + ("0 0.25 0.5 0.75\n" * 3),
            "nan_norm": "4 400.0 700.0 nan 3\n" + ("0 0.25 0.5 0.75\n" * 3),
            # A body long enough to reach the sample ceiling. Without one the file
            # is refused by the per-value fscanf instead, so the ceiling itself
            # went untested: deleting `count > CM_MAX_CAL_SAMPLES` survived.
            "samples_over_limit_with_body": ("4 400.0 700.0 100.0 1001\n"
                                             + ("0 0.25 0.5 0.75\n" * 1001)),
            # fscanf("%lf") returns 0 on a matching failure, not 1, so a tail that
            # is a word or a comment is not a "trailing number".
            "trailing_word": ("4 400.0 700.0 100.0 3\n" + ("0 0.25 0.5 0.75\n" * 3)
                              + "this is not a number at all\n"),
            "trailing_comment": ("4 400.0 700.0 100.0 3\n" + ("0 0.25 0.5 0.75\n" * 3)
                                 + "# comment\n"),
            "control_z_tail": ("4 400.0 700.0 100.0 3\n" + ("0 0.25 0.5 0.75\n" * 3)
                               + "\x1aunparsed bytes\n"),
            "empty": "",
        }
        attempts = [("missing", str(bad / "absent.txt"))]
        for name, body in cases_by_name.items():
            path = bad / f"{name}.txt"
            path.write_text(body)
            attempts.append((name, str(path)))
        for label, argument in attempts:
            refused = subprocess.run(
                [str(worker), "--usb-index", "1", "--expected-serial", "CM-COLORIMETER-0001",
                 "--instrument", "X-Rite i1 DisplayPro, ColorMunki Display",
                 "--allow-serial-identification", "0",
                 "--mode-word", str(mode_word("emissive_spot", spectral=False)),
                 "--facilities", "0", "--read", "1", "--calibration-spectra", argument],
                capture_output=True, text=True, timeout=30, check=False,
                env=dict(os.environ, CM_COLORIMETER_FIXTURE="success"), input="",
            )
            require(refused.returncode == 2,
                    f"{label} spectral file must be refused, got {refused.returncode}")
            require(not refused.stdout.strip(), f"{label} spectral file must publish nothing")
    cases.append({"case": "correction_spectra_file_validated",
                  "status": "passed", "rejected": len(attempts)})

    # The selection parser itself. session_attach.c is only compiled and
    # link-checked -- running it enumerates devices -- but cm_parse_selection
    # lives in session.c and IS linked into the substituted workers, so the
    # parsing half of serial identification is testable at no hardware risk and
    # was not being tested: dropping the 0/1 validation, forcing the flag on, and
    # breaking the three-pair form all survived.
    selection = [str(worker), "--usb-index", "1",
                 "--expected-serial", "CM-COLORIMETER-0001",
                 "--instrument", "X-Rite i1 DisplayPro, ColorMunki Display"]
    tail = ["--mode-word", str(mode_word("emissive_spot", spectral=False)),
            "--facilities", "0", "--read", "1"]
    environment = dict(os.environ, CM_COLORIMETER_FIXTURE="success")
    for label, pairs, expected in (
        ("three_pair_form", [], 0),
        ("permission_denied", ["--allow-serial-identification", "0"], 0),
        ("permission_granted", ["--allow-serial-identification", "1"], 0),
        ("not_a_flag", ["--allow-serial-identification", "2"], 2),
        ("empty_value", ["--allow-serial-identification", ""], 2),
        ("word_value", ["--allow-serial-identification", "yes"], 2),
    ):
        done = subprocess.run(selection + pairs + tail, capture_output=True, text=True,
                              timeout=30, env=environment, input="", check=False)
        require(done.returncode == expected,
                f"selection {label}: expected rc {expected}, got {done.returncode}")
    cases.append({"case": "selection_parsing_validated", "status": "passed"})

    # Unsigned settings require canonical decimal: no sign, base prefix or
    # leading zero. Windows unsigned long is 32-bit, where strtoul("-1")
    # can otherwise return UINT32_MAX without reporting overflow.
    for label, flag, value in (
        ("signed_mode", "--mode-word", "-1"),
        ("positive_sign_mode", "--mode-word", "+1"),
        ("signed_facilities", "--facilities", "-1"),
        ("hex_mode", "--mode-word", "0x1"),
        ("leading_zero_mode", "--mode-word", "010"),
    ):
        done = subprocess.run(selection + [flag, value] + tail,
                              capture_output=True, text=True, timeout=30,
                              env=environment, input="", check=False)
        require(done.returncode == 2,
                f"unsigned {label}: expected rc 2, got {done.returncode}")
        require(not done.stdout.strip(), f"unsigned {label} must publish nothing")
    cases.append({"case": "unsigned_arguments_canonical_decimal", "status": "passed"})

    # Nine drivers inherit inst.c:99-101's default get_serial_no returning "", so a
    # mandatory pin would make them unattachable. An empty pin means "do not pin".
    code, events = run("emissive_spot", serial="")
    require(code == 0, f"an unpinned instrument must bind: {events[-2:]}")
    require(_of(events, "connected")[0]["serial"] == "CM-COLORIMETER-0001",
            "the driver's own serial is still reported when nothing is pinned")
    cases.append({"case": "unpinned_serial_binds", "status": "passed"})

    # These two native byte strings display as the same Unicode text. The
    # encoding marker keeps their identities distinct; an empty driver serial
    # has no identity and must use the null/null pair.
    serial_pairs = []
    for fixture, marker in (("invalid_serial_bytes", "bytes"),
                            ("utf8_serial_collision", "utf8")):
        code, events = run("emissive_spot", fixture=fixture, serial="")
        require(code == 0, f"{fixture} must bind without a serial pin")
        connected = _of(events, "connected")[0]
        require(connected["serial"] == "\u00ed\u00a0\u0080",
                f"{fixture} changed the serial text")
        require(connected["serial_encoding"] == marker,
                f"{fixture} must report its byte representation")
        serial_pairs.append((connected["serial"], connected["serial_encoding"]))
    require(serial_pairs[0] != serial_pairs[1],
            "distinct native serial byte strings must remain distinct")
    code, events = run("emissive_spot", fixture="empty_serial", serial="")
    require(code == 0, "empty driver serial must bind without a pin")
    connected = _of(events, "connected")[0]
    require(connected["serial"] is None and connected["serial_encoding"] is None,
            "empty driver serial must be an absent identity")
    cases.append({"case": "serial_representation_is_injective", "status": "passed"})

    # Driver-reported failures keep their own stage names.
    for fixture, stage in (
        ("attach_failure", "attach"),
        ("init_coms_failure", "init_coms"),
        ("init_failure", "init_inst"),
        ("serial_mismatch", "serial_mismatch"),
        ("check_mode_failure", "check_mode"),
        ("trigger_failure", "set_trigger"),
    ):
        code, events = run("emissive_spot", fixture=fixture)
        require(code == 1, f"{fixture} must fail")
        _failed(events, stage, fixture)
        cases.append({"case": fixture, "status": "passed"})

    # Calibration, needed-driven, with the operator answering. The condition and
    # its identifier are published as numbers; nothing here names an instrument.
    # The refresh display (i1d3.c:4040-4048, index 1) is the one that needs it.
    code, events = run("emissive_spot", calibrate=1, display_type=1, answers=("acknowledge",))
    require(code == 0, "acknowledged calibration must complete")
    prompt = _of(events, "instruction_required")[0]
    require(prompt["skippable"] is False, "a required condition must not offer skip")
    require(prompt["calibration_condition"] != 0, "the condition must be published")
    ready = _of(events, "calibration_ready")[0]
    require(ready["performed"] is True, "must report performed")
    require(ready["skipped"] is False, "an acknowledged condition was not skipped")
    # The driver re-requests until the condition is handed back. Two rounds, not
    # eight: a ladder that cleared *calc between rounds would exhaust the cap here
    # (the stub aborts past four) instead of settling.
    require(len(_of(events, "instruction_required")) == 1,
            "the condition must be established in one exchange, not re-requested")
    cases.append({"case": "calibration_acknowledged", "status": "passed"})

    # An instrument needing nothing must not invent a prompt.
    code, events = run("emissive_spot", calibrate=1, read=1, fixture="no_calibration_needed")
    require(code == 0, "an instrument needing no calibration must succeed")
    require(not _of(events, "instruction_required"), "invented a prompt")
    ready = _of(events, "calibration_ready")[0]
    require(ready["performed"] is False and ready["skipped"] is False, "nothing was performed")
    require(len(_of(events, "result")) == 1, "a placed sample was not read immediately")
    cases.append({"case": "calibration_not_needed", "status": "passed"})

    # Nor must the default display: i1d3's default type is a non-refresh one
    # (i1d3.c:4030-4039), which has no refresh-rate calibration at all
    # (:3346-3355). A stub that always asked would put an 80% patch in front of
    # every LCD reading.
    code, events = run("emissive_spot", calibrate=1)
    require(code == 0, "the default display must bind and settle")
    require(not _of(events, "instruction_required"), "the default display invented a prompt")
    ready = _of(events, "calibration_ready")[0]
    require(ready["needed_calibrations"] == 0 and ready["available_calibrations"] == 0,
            "a non-refresh display has no refresh-rate calibration")
    cases.append({"case": "non_refresh_display_needs_none", "status": "passed"})

    # The skip verb. The driver offers it via inst_calc_optional_flag; skipping
    # calls calibrate() again with the flag STILL SET (instappsup.c:411-413). The
    # stub aborts if the flag is cleared, because clearing means "proceed" and the
    # operator declined.
    code, events = run("emissive_spot", calibrate=1, fixture="skippable_calibration",
                       answers=("skip",))
    require(code == 0, "a skipped optional calibration must still complete")
    require(_of(events, "instruction_required")[0]["skippable"] is True, "skip not offered")
    # A declined calibration must NOT be published as a performed one. The stub
    # aborts if the ladder proceeds after the flag came back set.
    ready = _of(events, "calibration_ready")[0]
    require(ready["skipped"] is True, "a declined condition must say it was declined")
    require(ready["performed"] is False, "a declined calibration was not performed")
    cases.append({"case": "calibration_skipped", "status": "passed"})

    # Skip must be refused when the driver did not offer it.
    code, events = run("emissive_spot", calibrate=1, display_type=1, answers=("skip",))
    require(code == 1, "skipping a required calibration must fail")
    _failed(events, "calibration_not_skippable", "skip_refused")
    cases.append({"case": "calibration_skip_refused", "status": "passed"})

    # Each acknowledgement takes exactly "acknowledge". A well-formed answer of
    # another verb is a protocol fault that ends the session with nothing read and
    # nothing retried -- the placement before a read, the wrong-position retry and
    # the calibration condition alike. "skip" is a real verb elsewhere, which is
    # why it must not pass here.
    for label, answers, kw in (
        ("sample_ready", ("acknowledge", "skip"), dict(calibrate=1, display_type=1)),
        ("position_retry", ("skip",), dict(extra_env={"CM_SENSOR_POSITION": "read"})),
        ("calibration_setup", ("navigate",), dict(calibrate=1, display_type=1)),
    ):
        code, events = run("emissive_spot", read=1, answers=answers, **kw)
        require(code == 1, f"{label}: a {answers[-1]} answer must fail")
        _failed(events, "protocol", f"{label}_{answers[-1]}")
        require(not _of(events, "result"), f"{label}: read after a wrong answer")
        require(len(_of(events, "instruction_required")) == len(answers),
                f"{label}: asked again after a wrong answer")
        cases.append({"case": f"{label}_answer_is_acknowledge_only", "status": "passed"})

    # A cancel can meet a driver that fails with another code anyway. After the
    # operator asked to stop, neither retry may ask anything more: no position
    # prompt (reposition) and no calibration retry (recalibrate_read). The
    # driver's own failure stays as the evidence of how the read ended.
    for fixture, calibrate, answers, asked in (
        ("abort_then_wrong_position", 0, ("cancel",), []),
        ("abort_then_needs_calibration", 1, ("cancel",), []),
    ):
        code, events = run("emissive_spot", fixture=fixture, read=1, calibrate=calibrate,
                           answers=answers)
        require(code == 1, f"{fixture}: the read must not complete")
        require([e["request_id"] for e in _of(events, "instruction_required")] == asked,
                f"{fixture}: asked the operator something after the cancel")
        require(not _of(events, "calibration_retry"), f"{fixture}: retried after the cancel")
        _failed(events, "read_sample", fixture)
        cases.append({"case": f"{fixture}_asks_nothing_more", "status": "passed"})


def exercise_chart(worker: Path, cases: list) -> None:
    """The table and strip personas: the two workflows no per-device helper has."""
    def run(mode, *, persona="spectroscan", **kw):
        kw.setdefault("spectral", True)
        return _run(worker, mode, variable="CM_CHART_FIXTURE", persona=persona,
                    serial="CM-CHART-0001",
                    instrument={"dtp51": "X-Rite DTP51",
                                "spectroscan": "GretagMacbeth SpectroScan",
                                "spectroscant": "GretagMacbeth SpectroScanT",
                                "dtp20": "X-Rite DTP20",
                                "dtp41": "X-Rite DTP41",
                                "munki": "X-Rite ColorMunki",
                                "i1pro3": "X-Rite i1 Pro 3",
                                "i1pro2": "X-Rite i1 Pro 2"}[persona], **kw)

    # A spectrometer advertises neither correction bit (no chart persona sets
    # inst2_ccmx or inst2_ccss). Asking for one must be refused by capability --
    # applying it and ignoring the result would corrupt every reading silently.
    code, events = run("reflective_spot", read=1,
                       correction_matrix="1,0,0,0,1,0,0,0,1",
                       correction_dtech=0, correction_cbid=1)
    require(code == 1, "a correction on a spectrometer must be refused")
    _failed(events, "correction_matrix_unavailable", "spectrometer_correction")
    cases.append({"case": "correction_refused_by_capability", "status": "passed"})

    # No trigger is required of any workflow: the ladder negotiates one from the
    # capability word. Only the two bits a sheet genuinely cannot be located
    # without are asked for -- upstream guards inst2_xy_holdrel rather than
    # requiring it (chartread.c:1071, :1142, :1344).
    sheet = dict(read_xy=6, xy_passes=2, xy_steps=3, xy_locate=3,
                 facilities=("xy_locate", "xy_position"))
    # Three sighted fiducials, then the navigation vectors the caller derived from
    # them. The stub aborts unless read_xy receives exactly these eight numbers in
    # chartread's order, so a ladder that invented them cannot pass.
    sighting = ("acknowledge",) * 4 + ("navigate 1,2,3,4,5,6,7,8",)

    code, events = run("reflective_xy", persona="spectroscan", answers=sighting, **sheet)
    require(code == 0, f"SpectroScan sheet must succeed: {events[-2:]}")
    confirmed = decode_modes(_of(events, "connected")[0]["mode"])
    require(confirmed == ("ref_xy",), f"ref_xy not confirmed: {confirmed}")
    located = _of(events, "xy_location")
    require([(e["x"], e["y"]) for e in located] == [(10.0, 20.0), (11.0, 22.0), (12.0, 24.0)],
            f"the ladder must publish each sighted point once: {located}")
    patches = _of(events, "xy_patch")
    require(len(patches) == 6, f"a 2x3 sheet is six patches, got {len(patches)}")
    require([p["index"] for p in patches] == list(range(6)), "sheet patches must carry their order")
    spectrum = patches[0]["backend_reported"]["spectrum"]
    require(spectrum["spec_n"] == 36 and spectrum["wavelength_start_nm"] == 380.0,
            "a spectrometer's sheet patch must carry its spectrum")
    cases.append({"case": "spectroscan_sheet", "status": "passed", "patches": len(patches)})
    spectral_sheet = [{k: v for k, v in p["backend_reported"].items() if k != "spectrum"}
                      for p in patches]

    # The same sheet XYZ-only. The stub's read_xy hands the worker a spectrum
    # whatever the bound mode (chart_stub.c fill(), as ss.c:846-852 does), and
    # the instrument advertises the spectral bit, so session.c leaves the
    # spectrum out rather than refusing the reading. The colorimeter case above
    # (`unexpected_spectrum_refused`) is the other half of that predicate: no
    # spectral bit advertised, bands returned, refused.
    code, events = run("reflective_xy", persona="spectroscan", answers=sighting, xyz_only=1,
                       **sheet)
    require(code == 0, f"an XYZ-only SpectroScan sheet must succeed: {events[-2:]}")
    require(_of(events, "connected")[0]["spectral"] is False,
            "xyz_only binds the SpectroScan without the spectral bit")
    patches = _of(events, "xy_patch")
    require(len(patches) == 6, f"a 2x3 XYZ-only sheet is six patches, got {len(patches)}")
    require(all(p["backend_reported"]["spectrum"] is None for p in patches),
            "an XYZ-only sheet publishes no spectrum")
    require([p["index"] for p in patches] == list(range(6)),
            "an XYZ-only sheet preserves patch order")
    require([{k: v for k, v in p["backend_reported"].items() if k != "spectrum"}
             for p in patches] == spectral_sheet,
            "an XYZ-only sheet preserves every native XYZ and its metadata")
    cases.append({"case": "spectroscan_sheet_xyz_only", "status": "passed",
                  "patches": len(patches)})

    # set_mode withdraws the three inst2_xy_* bits in transmission (ss.c:413-422).
    # The ladder's SECOND capability read is what catches it; without that read
    # this binds and then fails at the table.
    code, events = run("transmission_spot", persona="spectroscant", read=1, position=1,
                       facilities=("xy_position",))
    require(code == 1, "transmission must lose the table facilities")
    _failed(events, "facilities_changed", "spectroscant_transmission")
    cases.append({"case": "capabilities_withdrawn_by_set_mode", "status": "passed"})

    # A positioned spot: sight, drive the head there, read, drive the sight back
    # (spotread.c:2165-2277). Only the capability pair gates it.
    code, events = run("reflective_spot", persona="spectroscan", read=1, position=1,
                       answers=("acknowledge",),
                       facilities=("xy_locate", "xy_position"))
    require(code == 0, f"positioned spot must succeed: {events[-2:]}")
    require(len(_of(events, "xy_location")) == 1, "the sighted point must be published once")
    require(_of(events, "result")[0]["backend_reported"]["XYZ_v"] == 1, "no reading published")
    cases.append({"case": "positioned_spot", "status": "passed"})

    # A misread must STILL return the sight to the sighted point. The stub counts
    # head moves against sight restores and aborts in del() if they do not balance,
    # so a ladder that returns early on the read result fails here rather than
    # leaving a real table parked over the patch.
    code, events = run("reflective_spot", persona="spectroscan", fixture="read_failure",
                       read=1, position=1, answers=("acknowledge",),
                       facilities=("xy_locate", "xy_position"))
    require(code == 1, "a misread must fail")
    _failed(events, "read_sample", "positioned_read_failure")
    cases.append({"case": "positioned_read_restores_the_sight", "status": "passed"})

    strip = dict(read_strip=4, strip_pass="B", strip_guide=15, strip_widths="6.0,1.5,3.0")

    # A DTP41 strip. Its capability word carries no inst2_has_scan_toll, which the
    # per-device i1Pro3 helper requires of every strip -- a rule no driver states.
    code, events = run("reflective_strip", persona="dtp41", **strip)
    require(code == 0, f"DTP41 strip must succeed: {events[-2:]}")
    patches = _of(events, "strip_patch")
    require(len(patches) == 4 and [p["index"] for p in patches] == [0, 1, 2, 3],
            f"four ordered patches expected, got {[p.get('index') for p in patches]}")
    require(all(p["patch_count"] == 4 for p in patches), "each patch carries the pass length")
    cases.append({"case": "dtp41_strip", "status": "passed"})

    # Asking that instrument for a scan tolerance is refused, not silently dropped.
    code, events = run("reflective_strip", persona="dtp41", scan_tolerance=0.75, **strip)
    require(code == 1, "an unavailable scan tolerance must be refused")
    _failed(events, "scan_tolerance_unavailable", "dtp41_tolerance")
    cases.append({"case": "scan_tolerance_refused", "status": "passed"})

    # The ColorMunki advertises it, so the same request is honoured. The stub
    # aborts unless it receives exactly the value asked for.
    code, events = run("reflective_strip", persona="munki", scan_tolerance=0.75, **strip)
    require(code == 0, f"ColorMunki strip with tolerance must succeed: {events[-2:]}")
    require(len(_of(events, "strip_patch")) == 4, "four patches expected")
    cases.append({"case": "munki_strip_scan_tolerance", "status": "passed"})

    # inst_user_trig is a hint, not a failure (chartread.c:1551-1552). Treating it
    # as one would discard a pass the instrument actually read.
    code, events = run("reflective_strip", persona="munki", fixture="strip_user_trigger", **strip)
    require(code == 0, "a user-trigger hint must not discard the pass")
    require(len(_of(events, "strip_patch")) == 4, "the pass must still be published")
    cases.append({"case": "strip_user_trigger_kept", "status": "passed"})

    # A malformed final patch must publish NOTHING. A client must never hold a
    # short pass it believes is complete.
    code, events = run("reflective_strip", persona="dtp41", fixture="strip_malformed_last", **strip)
    require(code == 1, "a malformed patch must fail the pass")
    require(not _of(events, "strip_patch"), "a failed pass must publish no patch at all")
    _failed(events, "invalid_spectrum", "strip_malformed_last")
    cases.append({"case": "partial_strip_never_published", "status": "passed"})

    # The DTP41 gains transmission only when its initialised probe found it
    # (dtp41.c:1148-1154); until then check_mode refuses the route.
    code, events = run("transmission_strip", persona="dtp41", **strip)
    require(code == 1, "an unprobed transmission route must be refused")
    _failed(events, "check_mode", "dtp41_transmission_absent")
    code, events = run("transmission_strip", persona="dtp41", fixture="transmission_capable",
                       **strip)
    require(code == 0, f"a probed transmission route must bind: {events[-2:]}")
    require(len(_of(events, "strip_patch")) == 4, "transmission strip must publish its pass")
    cases.append({"case": "conditional_route_follows_capability", "status": "passed"})

    # --- configuration, between the mode and the trigger ------------------------
    #
    # High resolution is DEFERRED to a second set_mode. inst_mode_highres is mode
    # dependent on the i1Pro3 (i1pro3.c:143-146, gated on !s->reflective); the
    # ColorMunki's own is static (munki.c:144), so the stub borrows the i1Pro3's
    # form onto this persona to exercise the path. A ladder that composed the bit
    # into the first word would be refused by check_mode before the instrument ever
    # had a chance to offer it.
    code, events = run("reflective_strip", persona="munki", highres=1, **strip)
    require(code == 0, f"a deferred high-resolution round must succeed: {events[-2:]}")
    require(len(_of(events, "strip_patch")) == 4, "the pass must still be read")
    cases.append({"case": "highres_second_round", "status": "passed"})

    code, events = run("reflective_strip", persona="munki", fixture="no_highres", highres=1,
                       **strip)
    require(code == 1, "an unavailable resolution must be refused, not silently dropped")
    _failed(events, "highres_unavailable", "no_highres")
    cases.append({"case": "highres_refused", "status": "passed"})

    # The filter and the calibration standard go without a capability pre-check,
    # as spotread.c:1631-1637 and :1670-1677 do, and in that order -- the stub
    # aborts if the standard arrives before the filter.
    #
    # Driven on the SpectroScan, because it is one of only two drivers in the
    # pinned tree that implement inst_opt_set_filter at all (ss.c:1908 and
    # i1pro3.c:824) and it advertises the matching cap3 bits (ss.c:424-429). This
    # ran on the DTP41 until the stub stopped accepting any filter regardless of
    # capability: dtp41.c:1145 sets `p->cap3 = inst3_none` and dtp41.c implements
    # no filter option, so a real DTP41 refuses a filter and the passing case was
    # an artefact of the stub being more permissive than any driver.
    code, events = run("reflective_spot", persona="spectroscan", read=1, filter=4, xcalstd=2)
    require(code == 0, f"filter and calibration standard must be set: {events[-2:]}")
    cases.append({"case": "filter_then_xcalstd", "status": "passed"})

    # Inconsistent upstream XYZ-only conversion must fail before initialization,
    # even if a later driver call would otherwise fail for a different reason.
    for standard in (0, 1, 2):
        code, events = run("reflective_spot", persona="spectroscan",
                           xyz_only=1, xcalstd=standard, read=1)
        require(code == 1, "XYZ-only calibration-standard conversion was accepted")
        _failed(events, "xcalstd_requires_spectrum", "XYZ-only conversion")
        require(not any(e["event"] == "connected" for e in events),
                "Conflicting configuration reached acquisition")
    cases.append({"case": "xcalstd_requires_spectrum", "status": "passed"})
    code, events = run("emissive_spot", xcalstd=2, read=1)
    require(code == 1, "An emissive reading accepted a reflective conversion")
    _failed(events, "xcalstd_requires_reflection", "emissive conversion")
    cases.append({"case": "xcalstd_requires_reflection", "status": "passed"})

    code, events = run("reflective_spot", persona="spectroscan", fixture="filter_failure",
                       read=1, filter=4)
    require(code == 1, "a filter the instrument refuses must fail the bind")
    _failed(events, "set_filter", "filter_failure")
    cases.append({"case": "filter_refused_by_driver", "status": "passed"})

    # And a condition the instrument never advertised is refused rather than
    # quietly accepted. There is no capability pre-check on this path, so the
    # driver's own refusal is the only thing standing between a caller and a
    # reading taken under a condition it did not ask for.
    code, events = run("reflective_strip", persona="dtp41", filter=4, **strip)
    require(code == 1, "a DTP41 advertises no filter and must refuse one")
    _failed(events, "set_filter", "filter_unadvertised")
    cases.append({"case": "filter_refused_when_unadvertised", "status": "passed"})

    # Averaging and the display type ARE capability-gated, and a caller asking for
    # one the instrument lacks is refused rather than quietly given one reading.
    code, events = run("reflective_strip", persona="dtp41", averages=20, **strip)
    require(code == 1, "averaging without inst3_average must be refused")
    _failed(events, "averaging_unavailable", "averages")
    code, events = run("reflective_strip", persona="dtp41", fixture="averaging_capable",
                       averages=20, **strip)
    require(code == 0, f"advertised averaging must be honoured: {events[-2:]}")
    cases.append({"case": "averaging_follows_capability", "status": "passed"})

    code, events = run("reflective_strip", persona="dtp41", display_type=2, **strip)
    require(code == 1, "a display type without inst2_disptype must be refused")
    _failed(events, "display_type_unavailable", "display_type")
    code, events = run("reflective_strip", persona="dtp41", fixture="display_type_capable",
                       display_type=2, **strip)
    require(code == 0, f"an advertised display type must be honoured: {events[-2:]}")
    cases.append({"case": "display_type_follows_capability", "status": "passed"})

    # --- the trigger is negotiated, never assumed --------------------------------
    #
    # dtp51.c:809 advertises inst2_switch_trig and nothing else. Every upstream
    # application walks the capability bits (chartread.c:1412-1432); a ladder that
    # asked for inst_opt_trig_prog unconditionally could not bind this instrument
    # at all -- and the DTP51 is the one the strip parameters are shaped for.
    code, events = run("reflective_strip", persona="dtp51", spectral=False, **strip)
    require(code == 0, f"a switch-triggered instrument must bind: {events[-2:]}")
    connected = _of(events, "connected")[0]
    require(connected["trigger"] == 0x12,
            f"inst_opt_trig_switch (0x12) expected, got {connected['trigger']:#x}")
    require(connected["spectral"] is False, "the DTP51 carries no spectral bit")
    patches = _of(events, "strip_patch")
    require(len(patches) == 4, f"four patches expected, got {len(patches)}")
    require(all(p["backend_reported"]["spectrum"] is None for p in patches),
            "a colorimeter strip reader publishes no spectrum")
    cases.append({"case": "switch_trigger_negotiated", "status": "passed"})

    # A programmatic instrument still gets the programmatic trigger.
    code, events = run("reflective_strip", persona="dtp41", **strip)
    require(_of(events, "connected")[0]["trigger"] == 0x10, "prog trigger expected first")
    cases.append({"case": "programmatic_trigger_preferred", "status": "passed"})

    # --- a stored chart, and the runtime status that decides to read one ---------
    #
    # inst_stat_saved_readings is NOT a capability: an instrument that can store a
    # chart may or may not be holding one now (inst.h:467-468). It is the deciding
    # input chartread.c:900-931 uses, and no capability word answers it -- which is
    # why rank_routes refuses to order a saved route against a live one.
    chart = dict(read_chart=12, chart_passes=4, chart_steps=3, chart_sheets="2,2",
                 chart_id=7, saved_readings=1)
    code, events = run("saved_chart", persona="dtp20", **chart)
    require(code == 0, f"a stored chart must read: {events[-2:]}")
    confirmed = decode_modes(_of(events, "connected")[0]["mode"])
    require(confirmed == ("s_ref_chart",), f"s_ref_chart not confirmed: {confirmed}")
    stored = _of(events, "saved_readings")
    require(len(stored) == 1 and stored[0]["stored"] != 0,
            f"the runtime status must be published: {stored}")
    patches = _of(events, "chart_patch")
    require(len(patches) == 12 and [p["index"] for p in patches] == list(range(12)),
            f"a 4x3 chart is twelve ordered patches, got {len(patches)}")
    cases.append({"case": "dtp20_stored_chart", "status": "passed"})
    # DTP20 fills its full grid even when the final row has fewer requested patches.
    code, events = run("saved_chart", persona="dtp20", fixture="partial_chart",
                       **{**chart, "read_chart": 5, "chart_passes": 2,
                          "chart_steps": 3, "chart_sheets": "2"})
    patches = _of(events, "chart_patch")
    require(code == 0 and len(patches) == 5
            and [p["index"] for p in patches] == list(range(5)),
            "a six-slot grid must retain exactly the five requested patches")
    cases.append({"case": "dtp20_partial_final_row", "status": "passed"})
    code, events = run("saved_chart", persona="dtp20", fixture="partial_chart",
                       **{**chart, "read_chart": 4096, "chart_passes": 2,
                          "chart_steps": 2049, "chart_sheets": "2"})
    require(code == 1 and not _of(events, "chart_patch"),
            "an oversized full grid must refuse before entering the driver")
    _failed(events, "chart_geometry", "oversized stored chart grid")
    cases.append({"case": "dtp20_full_grid_capacity_refused", "status": "passed"})

    # An instrument holding nothing says so, rather than reporting an empty chart.
    code, events = run("saved_spot", persona="dtp20", fixture="nothing_stored",
                       saved_readings=1)
    require(code == 0, "asking what is stored must succeed even when nothing is")
    require(_of(events, "saved_readings")[0]["stored"] == 0, "nothing stored must say zero")
    cases.append({"case": "saved_readings_status", "status": "passed"})

    # ... and READING what is not there is refused before the read, by the status
    # that already answered. inst.h:467-468 makes this a runtime status so it can
    # be asked first; chartread.c:900-931 asks it before choosing the branch.
    code, events = run("saved_chart", persona="dtp20", fixture="nothing_stored", **chart)
    require(code == 1, "reading a stored chart that is not stored must be refused")
    _failed(events, "no_saved_readings", "nothing stored")
    require(not _of(events, "chart_patch"), "a refused stored read must publish no patch")
    cases.append({"case": "stored_read_with_nothing_stored", "status": "passed"})

    # Per-sheet pass counts that do not account for the chart's passes would walk a
    # driver off the end of the array this call allocated.
    for bad in ("2,3", "1", "4,1"):
        code, events = run("saved_chart", persona="dtp20",
                           **{**chart, "chart_sheets": bad})
        require(code == 1, f"chart sheets {bad} must be refused")
        _failed(events, "chart_geometry", f"chart_sheets={bad}")
    cases.append({"case": "chart_sheet_counts_must_account_for_the_passes", "status": "passed"})

    # Driver-reported failures in the sheet sequence keep their own stage names.
    for fixture, stage, answers in (
        ("xy_clear_failure", "xy_clear", ()),
        ("xy_hold_failure", "xy_sheet_hold", ("acknowledge",)),
        ("xy_locate_failure", "xy_locate_start", ("acknowledge",)),
        ("read_xy_failure", "read_xy", sighting),
    ):
        code, events = run("reflective_xy", persona="spectroscan", fixture=fixture,
                           answers=answers, **sheet)
        require(code == 1, f"{fixture} must fail")
        _failed(events, stage, fixture)
        cases.append({"case": fixture, "status": "passed"})

    # A refusal to answer the sheet prompt is a cancellation, not a silent read.
    code, events = run("reflective_xy", persona="spectroscan", answers=(), **sheet)
    require(code == 1, "an unanswered sheet prompt must not read")
    _failed(events, "cancelled", "sheet_unanswered")
    cases.append({"case": "sheet_cancelled", "status": "passed"})

    # The placement after a read-discovered calibration (recalibrate_read, the
    # DTP41's late need) takes "acknowledge" only, like every other: the placement
    # and the condition are answered, and a "skip" there ends it with no read.
    code, events = run("reflective_spot", persona="dtp41", fixture="late_calibration",
                       calibrate=1, read=1, answers=("acknowledge", "skip"))
    require(code == 1, "a skip at the retried placement must fail")
    _failed(events, "protocol", "sample_retry_skip")
    require([e["request_id"] for e in _of(events, "instruction_required")]
            == ["calibration-0", "sample-retry"],
            "the retried placement must be the one refused")
    require(not _of(events, "result"), "read after a wrong answer at the retry")
    cases.append({"case": "sample_retry_answer_is_acknowledge_only", "status": "passed"})


def exercise_i1pro3(worker: Path, cases: list) -> None:
    """The generic ladder driving the instrument the PER-DEVICE helper exists for.

    Every other persona here proves the ladder reaches instruments no bespoke
    helper covers. This one proves the converse, which is the harder claim and the
    one that decides whether `i1pro3.py` and `i1pro3_frontend.py` can go: a
    modular path that cannot be demonstrated on the one instrument the hard-coded
    path was written for has not replaced it.

    Modelled on i1pro3.c:127-175. Nothing here is per-device code -- the same
    ladder, the same flags, a different capability word.
    """
    def run(mode, **kw):
        kw.setdefault("spectral", True)
        return _run(worker, mode, variable="CM_CHART_FIXTURE", persona="i1pro3",
                    serial="CM-CHART-0001", instrument="X-Rite i1 Pro 3", **kw)

    # The six routes the per-device helper serves, each read through the ladder.
    for mode, flags in (
        ("emissive_spot", {"read": 1}),
        ("emissive_tele", {"read": 1}),
        ("reflective_spot", {"read": 1}),
        ("transmission_spot", {"read": 1}),
        ("ambient_spot", {"read": 1}),
        ("ambient_flash", {"read": 1}),
    ):
        code, events = run(mode, **flags)
        require(code == 0, f"the ladder must drive an i1Pro3 {mode}")
        require(any(event["event"] == "result" for event in events),
                f"{mode} produced no reading")
        cases.append({"case": f"i1pro3_{mode}", "status": "passed"})

    # Both scanned routes, with the geometry the CHART owns rather than the
    # instrument (session.h).
    strip = dict(read_strip=4, strip_pass="B", strip_guide=15, strip_widths="6.0,1.5,3.0")
    for mode in ("reflective_strip", "emissive_strip", "transmission_strip"):
        code, events = run(mode, **strip)
        require(code == 0, f"the ladder must drive an i1Pro3 {mode}")
        require(sum(event["event"] == "strip_patch" for event in events) == 4,
                f"{mode} did not return four patches")
        cases.append({"case": f"i1pro3_{mode}", "status": "passed"})

    # High resolution is MODE DEPENDENT on this instrument (i1pro3.c:145-148):
    # offered only once a non-reflective mode is current, which is exactly what
    # the ladder's deferred second round exists for. A reflective request for it
    # must be refused rather than silently ignored.
    code, _ = run("emissive_spot", read=1, highres=1)
    require(code == 0, "highres must be accepted for an emissive i1Pro3 mode")
    cases.append({"case": "i1pro3_highres_emissive", "status": "passed"})
    code, events = run("reflective_spot", read=1, highres=1)
    require(code == 1, "highres must be refused for a reflective i1Pro3 mode")
    _failed(events, "highres_unavailable", "i1pro3_highres_reflective")
    cases.append({"case": "i1pro3_highres_refused_when_reflective", "status": "passed"})

    # The measurement conditions, which exist only for a reflective state
    # (i1pro3.c:169-176). M3 needs I1PRO3_CAP_POL and is refused without it.
    for condition, value in (("m0", 0), ("m1", 1), ("m2", 4)):
        code, _ = run("reflective_spot", read=1, filter=value)
        require(code == 0, f"an i1Pro3 must accept {condition}")
        cases.append({"case": f"i1pro3_filter_{condition}", "status": "passed"})
    code, events = run("reflective_spot", read=1, filter=8)
    require(code == 1, "M3 must be refused without the polarisation option")
    _failed(events, "set_filter", "i1pro3_filter_m3")
    cases.append({"case": "i1pro3_filter_m3_refused", "status": "passed"})
    code, _ = run("reflective_spot", read=1, filter=8, fixture="polarisation_capable")
    require(code == 0, "M3 must be accepted with the polarisation option")
    cases.append({"case": "i1pro3_filter_m3_with_option", "status": "passed"})

    # Ambient is conditional on I1PRO3_CAP_AMBIENT, read from the instrument and
    # not from the model, so an i1Pro3 without it must refuse rather than assume.
    code, events = run("ambient_spot", read=1, fixture="no_ambient")
    require(code == 1, "an i1Pro3 without CAP_AMBIENT must refuse the route")
    _failed(events, "check_mode", "i1pro3_no_ambient")
    cases.append({"case": "i1pro3_ambient_refused_without_capability", "status": "passed"})

    # A correction on a spectrophotometer: no i1Pro3 advertises either bit.
    code, events = run("emissive_spot", read=1, correction_matrix="1,0,0,0,1,0,0,0,1",
                       correction_dtech=0, correction_cbid=1)
    require(code == 1, "a correction on an i1Pro3 must be refused by capability")
    _failed(events, "correction_matrix_unavailable", "i1pro3_correction")
    cases.append({"case": "i1pro3_correction_refused", "status": "passed"})


def exercise_i1pro2(worker: Path, cases: list) -> None:
    """The other instrument a per-device helper exists for (i1pro.c:130-190).

    Three peculiarities separate it from the i1Pro3, and each is the kind of
    thing a bespoke helper used to be written to hold: highres is static rather
    than mode dependent, inst_mode_ref_uv is an i1Pro2-only bit, and the
    measurement conditions are M2-or-nothing. The generic ladder reads all three
    off the capability words.
    """
    def run(mode, **kw):
        kw.setdefault("spectral", True)
        return _run(worker, mode, variable="CM_CHART_FIXTURE", persona="i1pro2",
                    serial="CM-CHART-0001", instrument="X-Rite i1 Pro 2", **kw)

    for mode in ("emissive_spot", "emissive_tele", "reflective_spot",
                 "transmission_spot", "ambient_spot", "ambient_flash"):
        code, events = run(mode, read=1)
        require(code == 0, f"the ladder must drive an i1Pro2 {mode}")
        require(any(event["event"] == "result" for event in events), f"{mode} read nothing")
        cases.append({"case": f"i1pro2_{mode}", "status": "passed"})

    strip = dict(read_strip=4, strip_pass="B", strip_guide=15, strip_widths="6.0,1.5,3.0")
    for mode in ("reflective_strip", "emissive_strip", "transmission_strip"):
        code, events = run(mode, **strip)
        require(code == 0, f"the ladder must drive an i1Pro2 {mode}")
        require(sum(event["event"] == "strip_patch" for event in events) == 4,
                f"{mode} did not return four patches")
        cases.append({"case": f"i1pro2_{mode}", "status": "passed"})

    # Static highres: accepted for a REFLECTIVE mode too, which is exactly where
    # the i1Pro3 refuses it. Same ladder, same flag, opposite answer -- read from
    # the capability word rather than from a per-device rule.
    code, _ = run("reflective_spot", read=1, highres=1)
    require(code == 0, "an i1Pro2 offers highres for a reflective mode")
    cases.append({"case": "i1pro2_highres_is_static", "status": "passed"})
    code, events = run("reflective_spot", read=1, highres=1, fixture="no_highres")
    require(code == 1, "an i1Pro2 without highres must refuse it")
    _failed(events, "highres_unavailable", "i1pro2_no_highres")
    cases.append({"case": "i1pro2_highres_refused_when_absent", "status": "passed"})

    # M2 is the one condition it carries; M1 is refused because i1pro.c never
    # sets inst3_filter_D50 for any i1Pro.
    code, _ = run("reflective_spot", read=1, filter=4)
    require(code == 0, "an i1Pro2 with the UV filter must accept M2")
    cases.append({"case": "i1pro2_filter_m2", "status": "passed"})
    code, events = run("reflective_spot", read=1, filter=1)
    require(code == 1, "no i1Pro advertises M1")
    _failed(events, "set_filter", "i1pro2_filter_m1")
    cases.append({"case": "i1pro2_filter_m1_refused", "status": "passed"})
    code, events = run("reflective_spot", read=1, filter=4, fixture="no_uv_filter")
    require(code == 1, "an i1Pro2 without the UV filter must refuse M2")
    _failed(events, "set_filter", "i1pro2_no_uv_filter")
    cases.append({"case": "i1pro2_filter_m2_refused_without_the_filter", "status": "passed"})


#: What cr250.c's error_text says per driver code, as the persona provokes it.
_CR250_TEXT = {
    "cr250_warning": "last capture reports warnings",
    "cr250_setup": "CR-250: SM UserCalibMode 0 left RS UserCalibMode at Match, not None",
    "cr250_firmware": "measurement requires firmware 1.26",
    "cr250_spectrum_mismatch": "CR-250: spectrum/instrument power 1000",
}


def exercise_cr250(worker: Path, cases: list) -> None:
    """The REAL cr250.c over the series' scripted instrument (cr250_persona.c).

    What each case must catch: a native fact dropped or reconstructed on the way
    out; a warning-bearing or refused attempt that still publishes a reading; a
    clamp turning a signed native XYZ into a fabricated zero; driver text parsed
    or lost instead of carried; a refused setup that nonetheless measured; a
    spectrum published in a unit the instrument's own totals contradict; a setting
    the driver wrote that goes unrecorded; an XYZ-only request that still paid for
    a spectrum; a CR-100 that claims one; a unit-check number published for a
    comparison not made, or one an overflow left without a value.
    """
    from color_measurement.instrument import native_facts

    def run(fixture, **kw):
        return _run(worker, "emissive_spot", fixture=fixture, serial="SYNTHETIC-250",
                    instrument="Colorimetry Research CR-250", read=1, hold_stdin=True, **kw)

    def facts_of(events, label):
        # The client's own joiner: the check and the client cannot disagree about
        # what a whole set of facts is.
        require(len(_of(events, "native_facts")) == 1,
                f"{label}: one native_facts event per attempt")
        try:
            return native_facts(events)
        except Exception as error:     # a gap, reordering or miscount
            raise AssertionError(f"{label}: {error}") from error

    def setting(facts, key):
        return next(item for item in facts["settings"] if item["key"] == key)

    code, events = run("cr250")
    require(code == 0, "CR-250 spot must succeed")
    require(not _of(events, "instruction_required"),
            "Placed programmatic CR spot invented an operator instruction")
    facts = facts_of(events, "cr250")
    names = [event["event"] for event in events]
    require(names.index("native_facts") < names.index("result"),
            "facts must precede the reading they describe")
    identity = facts["identity"]
    require(identity == {"verified": True, "model": "CR-250", "serial": "SYNTHETIC-250",
                         "firmware": "1.34.0-S28", "firmware_components": [1, 34, 0],
                         "instrument_type": 2}, "verified identity lost or reshaped")
    capture = facts["capture"]
    reported = _of(events, "result")[0]["backend_reported"]
    require(capture["valid"] is True and capture["xyz"] == reported["XYZ"] == [12.5, 25, 6.25],
            "native XYZ and the published reading disagree")
    require(capture["measurement_budget_s"] == 120 and capture["budget_policy"]["source"] == "host",
            "budget lost or presented as a device fact")
    require({"Accessory", "Aperture"} <= {r["key"] for r in capture["matched_records"]}
            and capture["range_record"]["name"] == "A", "inventory records lost")
    contexts = [r["context"] for r in facts["replies"]]
    require(contexts.count("M") == 1, "exactly one M in the attempt")
    require(contexts.index("RS UserCalibMode") < contexts.index("M")
            < contexts.index("RM UserCalibMode"), "current and capture settings out of order")
    require(facts["factory_calibration"] == "unknown", "factory calibration asserted")
    # Every setting the row checks, with the value accepted from RS, and no write
    # on an instrument already set as the read needs.
    rs = {r["context"][3:]: r["value"] for r in facts["replies"] if r["context"].startswith("RS ")}
    require(facts["settings"] and all(item["value"] == rs[item["key"]]
                                      for item in facts["settings"] if item["value"] is not None),
            "a checked setting lost or not the value RS returned")
    require(setting(facts, "CMF")["driver_set"] and not setting(facts, "Speed")["driver_set"],
            "which settings the driver writes was lost")
    require(facts["writes"] == [], "a write recorded on an instrument that needed none")

    code, cancelled_events = run("cr250", read_allowance_s=300, cancel_at_read_allowance=True)
    require(code == 1 and not _of(cancelled_events, "result")
            and not _of(cancelled_events, "instruction_required"),
            "CR driver cancellation published a reading or invented a prompt")
    _failed(cancelled_events, "cancelled", "cr250_user_cancel")
    cancelled_facts = facts_of(cancelled_events, "cr250_user_cancel")
    require(cancelled_facts["capture"]["valid"] is False
            and not any(r["context"] == "M" for r in cancelled_facts["replies"]),
            "CR armed cancellation sent M or retained a valid capture")
    # The actor's atexit guard also requires exactly one port release. An abort
    # there cannot masquerade as the expected exit status 1.
    require(_of(cancelled_events, "terminal")[0]["software_stopped"] is True,
            "CR armed cancellation did not stop its software")
    cases.append({"case": "cr250_user_cancel_before_measurement", "status": "passed"})
    # Keep the original unknown-unit bins even when the same-capture model
    # qualifies an Argyll mW/(m^2.sr.nm) publication (xspect.h:40-42).
    spectrum, sp = reported["spectrum"], capture["spectrum"]
    require(_of(events, "native_spectrum") and len(sp["values"]) == sp["count"],
            "a published reading lost its original native spectrum")
    require(sp["unit"] is None and sp["inferred_unit"] == "W/(sr.m^2.nm)"
            and sp["unit_basis"] == "same_capture_consistency_under_driver_model"
            and sp["published_unit"] == "mW/(m^2.sr.nm)" and sp["published_scale"] == 1000
            and spectrum["spec_n"] == sp["count"] == 81 and spectrum["norm"] == 1
            and spectrum["wavelength_start_nm"] == sp["start_nm"] == 380
            and spectrum["wavelength_end_nm"] == sp["end_nm"] == 780
            and spectrum["samples"] == [value * sp["published_scale"] for value in sp["values"]],
            "the published spectrum is not the native one on its grid")
    unit = capture["unit_check"]
    require(unit["verdict"] == "consistent" and all(unit["discriminating"].values())
            and abs(unit["power_ratio"] - 1) < 1e-3 and abs(unit["y_ratio"] - 1) < 1e-3,
            "a consistent spectrum was not judged consistent")
    require(abs(unit["spectrum_w"] - capture["radiometric"]["radiance_w_sr_m2"])
            < 1e-3 * capture["radiometric"]["radiance_w_sr_m2"],
            "the unit check's power and RM Radiometric disagree")
    spectral_required = capture["required_s"]
    cases.append({"case": "cr250_real_driver_spot", "status": "passed",
                  "native_replies": len(facts["replies"])})

    # XYZ only, from the same instrument: bound without the spectral bit, no RM
    # Spectrum sent, no unit check, and a smaller requirement than the spectral read.
    code, events = run("cr250", xyz_only=1)
    require(code == 0 and _of(events, "connected")[0]["spectral"] is False,
            "an XYZ-only read was bound spectral")
    facts = facts_of(events, "cr250 xyz_only")
    require("RM Spectrum" not in [r["context"] for r in facts["replies"]]
            and _of(events, "result")[0]["backend_reported"].get("spectrum") is None,
            "an XYZ-only read still took the spectrum")
    require(facts["capture"]["unit_check"]["verdict"] == "not_checked"
            and facts["capture"]["required_s"] < spectral_required,
            "an XYZ-only read still paid for a spectrum")
    cases.append({"case": "cr250_xyz_only", "status": "passed"})

    code, events = run("cr250_negative", xyz_only=1)
    require(code == 0, "a signed reading is still a reading")
    reading = _of(events, "result")[0]["backend_reported"]["XYZ"]
    require(reading == [-0.5, 0.25, -0.001], "signed native XYZ clamped or altered")
    cases.append({"case": "cr250_signed_xyz", "status": "passed"})

    # An operator's user calibration left active: the driver sets None, measures,
    # and records the write with both values. It is not restored.
    code, events = run("cr250_writes")
    require(code == 0 and len(_of(events, "result")) == 1, "a settable instrument was refused")
    facts = facts_of(events, "cr250_writes")
    require(facts["writes"] == [{"key": "UserCalibMode", "before": "Match", "target": "None",
                                 "after": "None", "confirmed": True}],
            "the driver's write was lost or reshaped")
    cases.append({"case": "cr250_driver_write_recorded", "status": "passed"})

    for fixture, stage in (("cr250_warning", "read_sample"), ("cr250_setup", "read_sample"),
                           ("cr250_native_error", "read_sample"),
                           ("cr250_spectrum_mismatch", "read_sample"),
                           ("cr250_firmware", "init_inst"), ("cr250_model", "init_coms")):
        code, events = run(fixture)
        require(code != 0 and not _of(events, "result"), f"{fixture}: published a reading")
        _failed(events, stage, fixture)
        error = _of(events, "error")[0]
        require(isinstance(error.get("native_text"), str) and error["native_text"]
                and error["native_text_truncated"] is False, f"{fixture}: driver text lost")
        if fixture in _CR250_TEXT:
            require(_CR250_TEXT[fixture] in error["native_text"], f"{fixture}: wrong text")
        facts = facts_of(events, fixture)
        # Refused, so never valid; the native XYZ and RM Radiometric are kept only
        # when the refusal came after they were parsed -- the spectrum's unit
        # check. Every other refusal here precedes RM Radiometric, so a value there
        # would be invented.
        late = fixture == "cr250_spectrum_mismatch"
        require(facts["capture"]["valid"] is False
                and (facts["capture"]["xyz"] == [12.5, 25, 6.25] if late
                     else facts["capture"]["xyz"] is None)
                and ((facts["capture"]["radiometric"] or {}).get("radiance_w_sr_m2", 0) > 0
                     if late else facts["capture"]["radiometric"] is None),
                f"{fixture}: a failed attempt kept a valid capture, lost a parsed native "
                "value or published one it never parsed")
        contexts = [r["context"] for r in facts["replies"]]
        if fixture == "cr250_warning":
            warning = next(r for r in facts["replies"] if r["context"] == "RM Warnings")
            require(warning["value"] == "3", "native warning body lost")
        if fixture == "cr250_setup":
            # The unit kept its value: the write is recorded unconfirmed, and the
            # refusal comes before M.
            require("M" not in contexts, "a refused setup still measured")
            require(facts["writes"] == [{"key": "UserCalibMode", "before": "Match",
                                         "target": "None", "after": "Match",
                                         "confirmed": False}],
                    "an unconfirmed write was lost or claimed as confirmed")
            require(facts["capture"]["problem"].startswith("SM UserCalibMode 0"),
                    "the refusing check was not kept")
        if fixture == "cr250_native_error":
            last = facts["last_reply"]
            require(last["status"] == "ER" and last["code"] == -17
                    and last["value"] == "Not enough light", "native error reply lost")
            require("-17" in error["native_text"], "native code missing from driver text")
        if fixture == "cr250_spectrum_mismatch":
            require(error["native_code"] & 0xFFFF == 0xFF24, "not the spectrum mismatch")
            unit = facts["capture"]["unit_check"]
            require(unit["verdict"] == "mismatch" and abs(unit["power_ratio"] - 1000) < 1,
                    "the refused unit check lost its numbers")
            require(contexts.count("M") == 1, "the mismatch was not judged on one capture")
            # No reading, so the instrument's own spectrum is the evidence of which
            # factor is wrong: it must arrive, whole, in the unit the instrument sent.
            spectrum = facts["capture"]["spectrum"]
            values = spectrum.get("values")
            require(values is not None and len(values) == spectrum["count"] == 81,
                    "a refused spectrum's values were lost")
            radiance = facts["capture"]["radiometric"]["radiance_w_sr_m2"]
            require(abs(sum(values) * spectrum["step_nm"] / radiance - 1000) < 1,
                    "the refused spectrum is not the one the instrument sent")
        if fixture == "cr250_firmware":
            require(facts["identity"]["firmware"] == "1.25", "refused firmware not retained")
        if fixture == "cr250_model":
            require(facts["identity"]["verified"] is False
                    and facts["last_reply"]["value"] == "CR-300",
                    "an unverified identity was published or the refusing reply lost")
        cases.append({"case": fixture, "status": "passed", "stage": stage})

    # The unit check publishes no stand-in numbers (cr250.h CR250_UNIT_*): a value
    # the driver did not form is null, never 0, and a comparison made without one
    # refuses. The failure each case catches is a 0 or an overflow read as a
    # result -- the pass a wrong unit could otherwise get.
    def unit_of(events, label):
        unit = facts_of(events, label)["capture"]["unit_check"]
        for made, value in (("power", "power_ratio"), ("axis", "mean_nm"),
                            ("luminance", "y_ratio"), ("chromaticity", "chroma_error")):
            require(unit["discriminating"][made] or unit[value] is None,
                    f"{label}: {value} published for a comparison not made")
        return unit

    for fixture in ("cr250_spectrum_overflow", "cr250_spectrum_cancel"):
        code, events = run(fixture)
        require(code != 0 and not _of(events, "result"), f"{fixture}: published a reading")
        _failed(events, "read_sample", fixture)
        require(_of(events, "error")[0]["native_code"] & 0xFFFF == 0xFF24,
                f"{fixture}: not the spectrum mismatch")
        unit = unit_of(events, fixture)
        require(unit["verdict"] == "mismatch" and not any(unit["discriminating"].values())
                and all(unit[key] is None for key in ("spectrum_w", "spectrum_mean_nm", "xyz")),
                f"{fixture}: an overflowing spectrum published a unit number")
        spectrum = facts_of(events, fixture)["capture"]["spectrum"]
        require(len(spectrum.get("values") or ()) == spectrum["count"] >= 4,
                f"{fixture}: the values as sent were not kept")
        cases.append({"case": fixture, "status": "passed", "stage": "read_sample"})

    code, events = run("cr250_near_floor")
    require(code == 0 and len(_of(events, "result")) == 1, "a dim consistent spectrum was refused")
    unit = unit_of(events, "cr250_near_floor")
    require(unit["verdict"] == "consistent" and unit["discriminating"]["luminance"]
            and not unit["discriminating"]["chromaticity"] and unit["y_ratio"] is not None,
            "the luminance comparison was not made, or chromaticity was without native light")
    cases.append({"case": "cr250_near_floor", "status": "passed"})

    code, events = run("cr250_native_sum_overflow")
    require(code != 0 and not _of(events, "result"), "native X+Z overflow published a reading")
    _failed(events, "read_sample", "cr250_native_sum_overflow")
    unit = unit_of(events, "cr250_native_sum_overflow")
    require(unit["verdict"] == "mismatch" and unit["discriminating"]["chromaticity"]
            and unit["chroma_error"] is not None and abs(unit["chroma_error"] - 0.5) < 1e-9,
            "an overflowing native total hid the chromaticity disagreement")
    facts = facts_of(events, "native_sum_overflow")
    xyz = next(r for r in facts["replies"] if r["context"] == "RM XYZ")
    require(xyz["value"] == "1e308,25,1e308" and facts["capture"]["xyz"] == [1e308, 25, 1e308]
            and facts["capture"]["valid"] is False,
            "the refusing native XYZ was not kept, or the refused read was marked valid")
    cases.append({"case": "cr250_native_sum_overflow", "status": "passed",
                  "stage": "read_sample"})

    # A malformed RM Radiometric: triplet() may have written part of it before
    # failing, and none of that may be published.
    code, events = run("cr250_broken_radiometric", xyz_only=1)
    require(code != 0 and not _of(events, "result"), "a malformed radiometric reply was accepted")
    _failed(events, "read_sample", "cr250_broken_radiometric")
    capture = facts_of(events, "cr250_broken_radiometric")["capture"]
    require(capture["radiometric"] is None and capture["xyz"] is None,
            "a half-parsed radiometric triplet was published")
    cases.append({"case": "cr250_broken_radiometric", "status": "passed", "stage": "read_sample"})

    # A dark capture: RM Radiometric all zero is a value, not "not read". XYZ
    # only, since a bright spectrum beside a zero radiance is rightly refused.
    code, events = run("cr250_zero_radiometric", xyz_only=1)
    require(code == 0 and len(_of(events, "result")) == 1, "a dark capture was refused")
    require(facts_of(events, "cr250_zero_radiometric")["capture"]["radiometric"]
            == {"type": 0, "radiance_w_sr_m2": 0, "photon_radiance_sr_m2_s": 0},
            "a zero radiance was published as not read")
    cases.append({"case": "cr250_zero_radiometric", "status": "passed"})

    # The host allows 120 s; this unit's 60 s exposure ceiling makes the driver's
    # budget 185 s, and its reads after M add their worst case to that. The
    # failure this catches: the host's allowance never reaching the driver, so M is
    # sent and the host cuts the exposure off mid-way. The refusal must come
    # before M, and keep the budget it refused and what remained against it.
    code, events = run("cr250_long_exposure", read_allowance_s=120)
    require(code != 0 and not _of(events, "result"), "long exposure published a reading")
    _failed(events, "read_sample", "cr250_long_exposure")
    error = _of(events, "error")[0]
    require(error["native_code"] & 0xFFFF == 0xFF0D, "not the budget refusal")
    facts = facts_of(events, "cr250_long_exposure")
    capture = facts["capture"]
    require("M" not in [r["context"] for r in facts["replies"]], "measured despite the refusal")
    require(capture["measurement_budget_s"] == 185, "refused budget not kept")
    require(capture["required_s"] > capture["measurement_budget_s"],
            "the reads after M were not reserved")
    require(0 < capture["host_allowance_s"] <= 120, "allowance not recorded")
    require(0 < capture["remaining_s"] <= capture["host_allowance_s"] < capture["required_s"],
            "what remained before M was not recorded")
    cases.append({"case": "cr250_budget_refused_before_m", "status": "passed"})
    # The counterpart: the same unit, allowed enough, measures -- once. The
    # allowance refuses only a budget that will not fit, not every long exposure.
    code, events = run("cr250_long_exposure", read_allowance_s=400)
    require(code == 0 and len(_of(events, "result")) == 1, "an ample allowance refused")
    facts = facts_of(events, "cr250_long_exposure ample")
    require([r["context"] for r in facts["replies"]].count("M") == 1, "not exactly one M")
    cases.append({"case": "cr250_budget_fits_allowance", "status": "passed"})
    # The native ladder conservatively deducts the entire allowance exchange
    # (session.c read_allowance), without assuming a shared clock epoch. CR needs
    # no operator setup, so a 2 s delayed grant is transport time, not a paused
    # operator acknowledgement. It must refuse a grant with only 1.5 s spare.
    code, events = run("cr250", read_allowance_s=spectral_required + 1.5, calibrate=1,
                       answer_delay_s=2.0)
    require(code == 1 and not _of(events, "result"),
            "the allowance exchange wait was not deducted")
    _failed(events, "read_sample", "cr250_allowance_exchange_wait")
    facts = facts_of(events, "cr250_allowance_exchange_wait")
    require(facts["capture"]["host_allowance_s"] < facts["capture"]["required_s"],
            "the delayed allowance was not reduced below the read requirement")
    require("M" not in [r["context"] for r in facts["replies"]], "measured after refusal")
    require(not _of(events, "instruction_required"), "CR invented an operator setup")
    cases.append({"case": "cr250_allowance_exchange_wait_is_charged", "status": "passed"})

    # The allowance is asked of the client once per read, and only for a driver that
    # takes one: a colorimeter without an adapter given the same flag must read
    # without the exchange, or every instrument pays a round trip for nothing.
    code, events = run("cr250", read_allowance_s=300)
    require(code == 0 and [e["event"] for e in events].count("read_allowance_required") == 1,
            "the CR-250 read was not granted its allowance exactly once")
    code, events = _run(worker, "emissive_spot", read=1, hold_stdin=True,
                        read_allowance_s=300)
    require(code == 0 and len(_of(events, "result")) == 1
            and not _of(events, "read_allowance_required"),
            "a driver without an allowance adapter was asked for one")
    cases.append({"case": "cr250_allowance_asked_only_of_adapters", "status": "passed"})

    # The CR-100 row of the same driver: an XYZ colorimeter. It identifies as
    # itself, advertises no spectrum, and refuses to have one left out.
    def cr100(**kw):
        return _run(worker, "emissive_spot", fixture="cr100", serial="SYNTHETIC-100",
                    instrument="Colorimetry Research CR-100", read=1, hold_stdin=True, **kw)

    code, events = cr100()
    require(code == 0 and _of(events, "connected")[0]["spectral"] is False,
            "the CR-100 claimed a spectrum")
    facts = facts_of(events, "cr100")
    require(facts["identity"]["model"] == "CR-100" and facts["identity"]["instrument_type"] == 1
            and facts["model_policy"] == "CR-100", "the CR-100 identity was lost")
    require(_of(events, "result")[0]["backend_reported"]["XYZ"] == [12.5, 25, 6.25]
            and "RM Spectrum" not in [r["context"] for r in facts["replies"]],
            "the CR-100 read was not an XYZ read")
    require(facts["capture"]["radiometric"] is None,
            "the CR-100 reads no RM Radiometric, yet one was published")
    code, events = cr100(xyz_only=1)
    require(code != 0, "XYZ-only accepted on an instrument with no spectrum")
    _failed(events, "xyz_only_unavailable", "cr100 xyz_only")
    cases.append({"case": "cr100_real_driver_spot", "status": "passed"})


def _check_sanitized_grid(workers: dict | None, output: Path) -> dict:
    """The DTP20 partial final row on sanitized chart workers.

    ``cm_read_chart`` allocates every passes x steps slot because DTP20 fills its
    whole grid. Without a sanitizer, ``dtp20_partial_final_row`` passes whether
    or not that allocation covers the grid: the extra write lands silently. The
    stage therefore also builds the chart worker sanitized, as composed and with
    the earlier ``patch_count`` allocation. The first must run clean; the second
    must stop on a heap-buffer-overflow, or this control could not fail for its
    reason.
    """
    if workers is None:
        return {"status": "unavailable",
                "reason": "the MinGW toolchain has no AddressSanitizer runtime"}
    # Leaks at exit are outside this property, and macOS has no LeakSanitizer.
    # Disabling it on Linux keeps the result the same across platforms.
    session = dict(variable="CM_CHART_FIXTURE", persona="dtp20", fixture="partial_chart",
                   serial="CM-CHART-0001", instrument="X-Rite DTP20", spectral=True,
                   read_chart=5, chart_passes=2, chart_steps=3, chart_sheets="2",
                   chart_id=7, saved_readings=1, extra_env={"ASAN_OPTIONS": "detect_leaks=0"})
    run = _session(workers["repaired"], "saved_chart", **session)
    (output / "sanitized-grid-repaired.log").write_text(run.stderr, encoding="utf-8")
    sanitizers.refuse(run.stderr, "sanitized dtp20_partial_final_row (sanitized-grid-repaired.log)")
    code, events = _events(run, "partial_chart")
    require(code == 0 and [p["index"] for p in _of(events, "chart_patch")] == list(range(5)),
            "the sanitized six-slot grid must retain exactly the five requested patches")
    # This control deliberately overflows. Its diagnostic is the witness; an
    # external OS symbolizer can stall while formatting the expected failure.
    control = dict(session, extra_env={"ASAN_OPTIONS": "detect_leaks=0:symbolize=0"})
    run = _session(workers["patch_count_allocation"], "saved_chart", **control)
    (output / "sanitized-grid-patch_count_allocation.log").write_text(run.stderr,
                                                                     encoding="utf-8")
    diagnostic = sanitizers.diagnostic(run.stderr) or ""
    require("AddressSanitizer: heap-buffer-overflow" in diagnostic,
            f"the patch_count allocation must overflow its grid: {run.stderr[-400:]}")
    return {"status": "passed", "case": "dtp20_partial_final_row",
            "control": "patch_count allocation stops on a heap-buffer-overflow"}


def patch_count_allocation(session: str) -> str:
    """``session.c`` with the grid allocation sized to the requested patches."""
    grid = "calloc((size_t)grid_patches, sizeof(ipatch))"
    require(session.count(grid) == 1, "the chart grid allocation changed")
    return session.replace(grid, "calloc((size_t)request->patch_count, sizeof(ipatch))")


def _check_native_debug(worker: Path, output: Path) -> dict:
    """Execute the real debug flag over a substituted worker; stdout stays JSON."""
    cases = []
    for level in (0, 1, 8, "-1", "9", "1.5"):
        run = _session(worker, "emissive_spot", mode_snapshot=1, read=0, calibrate=0,
                       native_debug_level=level)
        for name, text in (("stdout", run.stdout), ("stderr", run.stderr)):
            (output / f"native-debug-{level}-{name}.log").write_text(text, encoding="utf-8")
        if isinstance(level, int):
            code, events = _events(run, f"native_debug_{level}")
            require(code == 0 and len(_of(events, "connected")) == 1
                    and not _of(events, "result"),
                    f"Native debug level {level} must preserve the complete JSON query protocol")
            marker = f"CM native diagnostic logging level {level}"
            require(("CM native diagnostic logging level" in run.stderr) == (level != 0)
                    and (level == 0 or marker in run.stderr),
                    f"Native debug level {level} did not control actual stderr logging")
            require("CM native diagnostic logging level" not in run.stdout,
                    "Native diagnostic logging leaked into the JSON wire")
            cases.append(dict(level=level, exit=code, stderr_marker=marker in run.stderr))
        else:
            require(run.returncode == 2 and not run.stdout,
                    f"Invalid native debug level {level} must refuse before any wire event")
            cases.append(dict(level=level, exit=run.returncode, wire_events=0))
    return dict(status="passed",
                scope="substituted worker only; stderr logging and JSON wire isolation",
                cases=cases)


def exercise_pr655(worker: Path, cases: list) -> None:
    """Both real PR drivers, controls and failed mappings in the shared session."""
    for model, count in ((655, 101), (670, 201)):
        def run(kind="", **flags):
            return _run(worker, "emissive_spot", fixture=f"pr{model}{kind}",
                        serial="SYNTHETIC-P7", instrument=f"Photo Research PR-{model}",
                        hold_stdin=True, read_allowance_s=500, **flags)

        code, events = run(read=1)
        require(code == 0 and len(_of(events, "result")) == 1, f"PR-{model} shared capture failed")
        require(not _of(events, "instruction_required"),
                f"Placed programmatic PR-{model} spot invented an operator instruction")
        capture = _of(events, "native_facts")[0]["capture"]
        require(capture["valid"] and capture["geometry_valid"]
                and capture["spectrum"]["count"] == count, f"PR-{model} native grid lost")
        require(sum(len(e["values"]) for e in _of(events, "native_spectrum")) == count,
                f"PR-{model} native spectrum chunks lost")
        cases.append({"case": f"pr{model}_shared_capture", "status": "passed"})

        code, events = run("_cancel", read=1, cancel_at_read_allowance=True)
        require(code == 1 and not _of(events, "result")
                and not _of(events, "instruction_required"),
                f"PR-{model} cancellation published a reading or invented a prompt")
        _failed(events, "cancelled", f"pr{model}_user_cancel")
        require(_of(events, "native_facts")[0]["capture"]["valid"] is False,
                f"PR-{model} armed cancellation retained a valid capture")
        require(not any(e["command"] == "M5" for e in _of(events, "native_raw")),
                f"PR-{model} armed cancellation sent M5")
        # persona_release requires all scripted steps, including Q and port
        # release, before this process may exit with the expected status 1.
        require(_of(events, "terminal")[0]["software_stopped"] is True,
                f"PR-{model} armed cancellation did not stop its software")
        cases.append({"case": f"pr{model}_user_cancel_before_measurement", "status": "passed"})

        code, events = _run(worker, "ambient_spot", fixture=f"pr{model}_ambient",
                            serial="SYNTHETIC-P7", instrument=f"Photo Research PR-{model}",
                            hold_stdin=True, read_allowance_s=500, read=1, spectral=True)
        result = _of(events, "result")
        require(code == 0 and len(result) == 1 and result[0]["backend_reported"]["mtype"] == 2,
                f"PR-{model} did not publish native ambient quantity")
        capture = _of(events, "native_facts")[0]["capture"]
        require(capture["valid"] and capture["xyz_publication"]["unit"] == "lux"
                and capture["spectrum"]["published_unit"] == "mW/(m^2.nm)"
                and capture["spectrum"]["count"] == count,
                f"PR-{model} ambient units or native grid changed")
        cases.append({"case": f"pr{model}_ambient_capture", "status": "passed"})

        for kind, action in (("_controls_read", "read"), ("_controls_apply", "apply"),
                             ("_control_capture", "apply"), ("_control_failed", "apply")):
            code, events = run(kind, read=int(kind == "_control_capture"),
                               calibrate=int(kind == "_control_capture"), controls_action=action,
                               controls_fields="" if action == "read" else "cycles=3")
            report = _of(events, "native_control")
            failed = kind == "_control_failed"
            require(code == int(failed) and len(report) == 1, f"PR-{model} controls {kind} failed")
            require(report[0]["failed"] == failed, f"PR-{model} control status lost")
            require(len(_of(events, "result")) == int(kind == "_control_capture"),
                    "controls published an unexpected measurement")
            if not failed and action == "apply":
                require(report[0]["values"]["cycles"] == 3, "confirmed cycles lost")
            require(len(_of(events, "read_allowance_required")) ==
                    (2 if kind == "_control_capture" else 1),
                    "allowance must refresh before each owned controls/capture operation")
            cases.append({"case": f"pr{model}{kind}", "status": "passed"})

        for kind in ("_geometry_changed", "_d2_failure"):
            code, events = run(kind, read=1)
            require(code == 1 and not _of(events, "result"), "failed mapping published a reading")
            capture = _of(events, "native_facts")[0]["capture"]
            require(capture["spectrum_parsed"] and capture["spectrum"]["count"] == count
                    and capture["xyz_publication"] is None,
                    "complete native spectrum was lost on refusal")
            cases.append({"case": f"pr{model}{kind}", "status": "passed"})


def exercise_generic_session(colorimeter: Path, chart: Path, output: Path, *,
                             cr250: bool = False, pr655: bool = False, compiler: str,
                             sanitized_chart: dict | None = None) -> dict:
    cases: list[dict] = []
    numeric_parser = _check_numeric_parser(compiler, output)
    native_debug = _check_native_debug(colorimeter, output)
    exercise_colorimeter(colorimeter, cases)
    exercise_chart(chart, cases)
    exercise_i1pro3(chart, cases)
    exercise_i1pro2(chart, cases)
    if cr250:
        exercise_cr250(colorimeter, cases)
        code, events = _run(colorimeter, "emissive_spot", fixture="cr300", serial="SYNTHETIC-300",
                            instrument="Colorimetry Research CR-300", read=1, hold_stdin=True,
                            read_allowance_s=300)
        require(code == 0 and _of(events, "native_facts")[0]["identity"]["model"] == "CR-300",
                "CR-300 shared driver route failed")
        cases.append({"case": "cr300_real_driver_spot", "status": "passed"})
    if pr655:
        exercise_pr655(colorimeter, cases)
    report = {
        "status": "passed",
        "scope": ("substituted upstream instruments and contributed drivers over simulated "
                  "serial transport; no hardware" if cr250 or pr655 else
                  "wholly substituted instruments; no hardware, no real driver"),
        "personas": ["i1d3", "spectroscan", "spectroscant", "dtp20", "dtp41", "dtp51",
                     "munki", "i1pro3", "i1pro2",
                     *(["cr250 (real driver)", "cr100 (real driver)", "cr300 (real driver)"]
                       if cr250 else []),
                     *(["pr655 (real driver)", "pr670 (real driver)"] if pr655 else [])],
        "routes_exercised": ["emis_tele", "emis_spot", "emis_ambient", "ref_spot",
                             "ref_xy", "ref_strip", "trans_spot", "trans_strip",
                             "s_ref_spot", "s_ref_chart"],
        "workflows_exercised": ["spot", "positioned_spot", "strip", "xy", "chart"],
        "configuration_exercised": ["highres", "filter", "xcalstd", "averages", "display_type"],
        # The family helpers have been removed. These personas exercise their
        # replacements through the same session as all other instruments.
        "served_by_existing_helpers": False,
        "formerly_served_by_family_helpers": ["i1pro3", "i1pro2"],
        "cases": cases,
        "numeric_parser": numeric_parser,
        "native_debug": native_debug,
        "sanitized_grid": _check_sanitized_grid(sanitized_chart, output),
    }
    (output / "generic-session-check.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return report
