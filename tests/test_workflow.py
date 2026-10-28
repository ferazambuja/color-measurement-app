"""Fresh synthetic fixtures; these tests neither enumerate nor operate hardware."""

import csv
import io
import json
import os
import signal
import subprocess
import sys
import warnings
from dataclasses import asdict, replace
from threading import Event, Thread

import pytest

from color_measurement import cli
from color_measurement.exports import SaveError, from_csv, from_json, save, to_csv, to_json
from color_measurement.models import (
    CalibrationContext,
    Capabilities,
    ComputedValues,
    DeviceIdentity,
    ReportedValues,
    Spectrum,
)
from color_measurement.operations import (
    OperationBusyError,
    SimulatedDevice,
)
from color_measurement.processing import compute


@pytest.fixture
def observation():
    return list(SimulatedDevice().measure())[-1].observation


def test_complete_simulation_is_labelled_with_no_spectrum(observation):
    assert observation.provenance == "simulated"
    assert observation.device.serial is None
    assert observation.calibration.status == "not_applicable"
    assert observation.backend_reported.spectrum is None
    assert observation.backend_reported.xyz.values == (20, 25, 10)
    assert json.loads(observation.raw_backend_output)["XYZ"] == [20, 25, 10]
    assert observation.requested_mode == observation.actual_mode == "relative_xyz"
    assert "SIMULATED" in observation.warnings[0]
    other = list(SimulatedDevice().measure())[-1].observation
    assert observation.operation_id != other.operation_id
    assert observation.observation_id != other.observation_id


def test_ordered_events_and_no_implicit_retry():
    events = list(SimulatedDevice().measure())
    assert [e.kind for e in events] == ["ready", "measuring", "result"]
    assert len({e.operation_id for e in events}) == 1
    assert sum(e.observation is not None for e in events) == 1


def test_pre_cancel_has_no_reading():
    token = Event()
    token.set()
    events = list(SimulatedDevice().measure(cancel=token))
    assert [e.kind for e in events] == ["ready", "cancelled"]
    assert all(e.observation is None for e in events)
    cancellation = events[-1].cancellation
    assert cancellation.requested and cancellation.software_stopped
    assert cancellation.instrument_state == "not_applicable"


def test_cancel_during_wait_releases_ownership():
    device, token, events = SimulatedDevice(), Event(), []
    measuring = Event()

    def collect():
        for event in device.measure(delay=30, cancel=token):
            events.append(event)
            if event.kind == "measuring":
                measuring.set()

    thread = Thread(target=collect)
    thread.start()
    try:
        assert measuring.wait(2)
        token.set()
        thread.join(2)
        assert not thread.is_alive()
        assert events[-1].kind == "cancelled"
        assert all(e.observation is None for e in events)
        assert list(device.measure())[-1].kind == "result"
    finally:
        token.set()
        thread.join(2)


def test_one_operation_and_explicit_generator_close():
    device = SimulatedDevice()
    first = device.measure()
    next(first)
    with pytest.raises(OperationBusyError):
        list(device.measure())
    first.close()
    assert list(device.measure())[-1].kind == "result"


@pytest.mark.parametrize(
    "options,refusal",
    [
        ({"mode": "reflectance"}, "Unsupported simulator mode"),
        ({"sample_id": ""}, "sample_id must be nonempty text"),
        ({"delay": -1}, "delay must be a finite number from 0 to 3600"),
        ({"delay": float("nan")}, "delay must be a finite number from 0 to 3600"),
        ({"delay": float("inf")}, "delay must be a finite number from 0 to 3600"),
        ({"delay": True}, "delay must be a finite number from 0 to 3600"),
        ({"delay": 3601}, "delay must be a finite number from 0 to 3600"),
        ({"delay": 10**400}, "delay must be a finite number from 0 to 3600"),
        ({"delay": -(10**400)}, "delay must be a finite number from 0 to 3600"),
    ],
)
def test_bad_request_refused_before_events(options, refusal):
    # Each option has its own check (operations.py); any other ValueError is the wrong one.
    with pytest.raises(ValueError, match=refusal):
        next(SimulatedDevice().measure(**options))


@pytest.mark.parametrize("delay", [0, 0.0, 3600, 3600.0])
def test_delay_bound_accepts_both_ends(delay):
    """The bound is inclusive at 0 and 3600 s (operations.py); the first event is
    published before the wait, so an accepted hour-long delay never sleeps here."""
    events = SimulatedDevice().measure(delay=delay)
    assert next(events).kind == "ready"
    events.close()
    for refused in (3600.000001, -0.000001):
        with pytest.raises(ValueError, match="0 to 3600"):
            next(SimulatedDevice().measure(delay=refused))


@pytest.mark.parametrize("serialize,deserialize", [(to_json, from_json), (to_csv, from_csv)])
def test_complete_roundtrip_with_signed_spectrum_and_unicode(observation, serialize, deserialize):
    spectrum = Spectrum((400, 413.5, 700), (-0.02, 0, 1.3), "reflectance_factor", "1")
    native = replace(observation.backend_reported, spectrum=spectrum)
    record = compute(
        replace(observation, sample_id="測定, café\nrow", backend_reported=native),
        reference_white=(80, 100, 120),
    )
    assert deserialize(serialize(record)) == record
    assert record.backend_reported.spectrum.values == (-0.02, 0, 1.3)


@pytest.mark.parametrize(
    "nullable_path",
    [None, ("backend_reported", "spectrum"), ("project_computed", "xyy"),
     ("project_computed", "lab")],
)
def test_matlab_empty_arrays_cannot_replace_canonical_json_nulls(observation, nullable_path):
    canonical = to_json(observation)
    data = json.loads(canonical)

    def nulls_to_empty_arrays(value):
        if value is None:
            return []
        if isinstance(value, dict):
            return {key: nulls_to_empty_arrays(item) for key, item in value.items()}
        if isinstance(value, list):
            return [nulls_to_empty_arrays(item) for item in value]
        return value

    if nullable_path is None:
        # MATLAB jsondecode/jsonencode reconstructs null fields as empty arrays.
        altered = nulls_to_empty_arrays(data)
    else:
        altered = data
        parent, field = nullable_path
        assert altered[parent][field] is None
        altered[parent][field] = []
    with pytest.raises(ValueError, match="declared optional record type"):
        from_json(json.dumps(altered))
    # The retained Python JSON remains the lossless interchange representation.
    assert from_json(canonical) == observation


@pytest.mark.parametrize(
    "changes,refusal",
    [
        ({"warnings": ["caller-owned mutable warning"]}, "Warnings must be an immutable tuple"),
        ({"warnings": ([],)}, "Warnings must be an immutable tuple"),
        ({"configuration": (("setting", []),)}, "Configuration must be immutable text"),
        ({"configuration": (([], "setting"),)}, "Configuration must be immutable text"),
        ({"device": {}}, "requires a DeviceIdentity record"),
        ({"calibration": {}}, "requires a CalibrationContext record"),
        ({"backend_reported": {"xyz": [1, 2, 3]}}, "requires a ReportedValues record"),
        ({"project_computed": {}}, "requires a ComputedValues record"),
    ],
)
def test_completed_record_rejects_mutable_metadata_before_persistence(
        observation, changes, refusal):
    with pytest.raises(ValueError, match=refusal):
        replace(observation, **changes)
    # The original completed record is still usable and identical after persistence.
    assert from_json(to_json(observation)) == observation


@pytest.mark.parametrize(
    "construct,refusal",
    [
        (lambda: Capabilities("id", ["mode"], True, False), "Capability names must be immutable"),
        (lambda: Capabilities("id", ("mode",), True, False, ["calibrate"]),
         "Capability names must be immutable"),
        (lambda: Capabilities("id", ("mode",), 1, False), "Quantity capabilities must be booleans"),
        (lambda: DeviceIdentity("id", "model", "backend", "version", serial=[]),
         "serial and encoding must be paired"),
        (lambda: CalibrationContext("calibrated_maybe", "unsupported status"),
         "Unknown calibration status"),
        (lambda: CalibrationContext("unknown", []), "Calibration detail must be text"),
        (lambda: ReportedValues(xyz={"values": [1, 2, 3]}), "Native XYZ must be an XYZ record"),
        (lambda: ReportedValues(spectrum=[]), "Native spectrum must be a Spectrum record"),
        (lambda: ComputedValues(xyy={"values": [1, 2, 3]}),
         "Computed quantities must be ComputedValue records"),
        (lambda: ComputedValues(lab=[]), "Computed quantities must be ComputedValue records"),
    ],
)
def test_nested_record_constructors_reject_invalid_or_mutable_fields(construct, refusal):
    with pytest.raises(ValueError, match=refusal):
        construct()


def test_undefined_computation_preserves_native_black_and_reason(observation):
    black = replace(
        observation,
        backend_reported=replace(
            observation.backend_reported,
            xyz=replace(observation.backend_reported.xyz, values=(0, 0, 0)),
        ),
    )
    result = compute(black, reference_white=(80, 100, 120))
    assert result.backend_reported == black.backend_reported
    assert result.raw_backend_output == black.raw_backend_output
    assert black.project_computed.xyy is None
    assert result.project_computed.xyy.values is None
    assert result.project_computed.xyy.status == "unavailable"
    assert result.project_computed.xyy.reason
    assert result.project_computed.lab.values == (0, 0, 0)
    assert from_csv(to_csv(result)) == result


def test_computed_lab_requires_explicit_white(observation):
    record = compute(observation)
    assert record.project_computed.xyy.status == "ok"
    assert record.project_computed.lab is None


@pytest.mark.parametrize(
    "wavelengths,values,refusal",
    [
        ((), (), "equal, nonempty wavelength and sample arrays"),
        ((500, 400), (1, 2), "positive and strictly increasing"),
        ((400, 400), (1, 2), "positive and strictly increasing"),
        ((400,), (1, 2), "equal, nonempty wavelength and sample arrays"),
        ((400, float("nan")), (1, 2), "finite real numbers, not booleans"),
        ((0,), (1,), "positive and strictly increasing"),
        ((400,), (float("inf"),), "finite real numbers, not booleans"),
    ],
)
def test_bad_spectrum_refused(wavelengths, values, refusal):
    with pytest.raises(ValueError, match=refusal):
        Spectrum(wavelengths, values, "reflectance_factor", "1")


@pytest.mark.parametrize(
    "mutate,refusal",
    [
        # Literal refusals are direct; optional-record refusals retain the reason.
        (lambda d: d.update(schema_version=2), "Unexpected literal value: 2"),
        (lambda d: d.update(schema_version=True), "Unexpected literal value: True"),
        (lambda d: d.update(extra="unrecognized"), r"fields differ: \['extra'\]"),
        (lambda d: d.pop("calibration"), r"fields differ: \['calibration'\]"),
        (lambda d: d.update(acquired_at="2026-09-16T10:00:00"), "must include its UTC offset"),
        (lambda d: d.update(actual_mode="reflectance"), "Unapproved mode fallback"),
        (lambda d: d.update(provenance="measured_maybe"),
         "Unexpected literal value: 'measured_maybe'"),
        (lambda d: d["backend_reported"]["xyz"].update(values=[True, 0, 1]),
         "declared optional record type"),
        (lambda d: d["backend_reported"]["xyz"].update(values=[float("nan"), 0, 1]),
         "declared optional record type"),
        (lambda d: d["backend_reported"]["xyz"].update(values=[0, 1]),
         "declared optional record type"),
        (lambda d: d.update(calibration={"status": "calibrated_maybe", "detail": "unknown"}),
         "Unexpected literal value: 'calibrated_maybe'"),
    ],
)
def test_malformed_records_fail(observation, mutate, refusal):
    data = asdict(observation)
    mutate(data)
    with pytest.raises(ValueError, match=refusal):
        from_json(json.dumps(data))


def test_optional_record_refusal_names_the_inner_reason(observation):
    """An optional record that fails to decode is refused with the record's own
    reason, so a nonfinite band inside an optional XYZ is not reported as a
    mere optional-record mismatch."""
    data = asdict(observation)
    data["backend_reported"]["xyz"]["values"] = [float("nan"), 0, 1]
    with pytest.raises(ValueError, match="declared optional record type: .*finite real numbers"):
        from_json(json.dumps(data))
    data["backend_reported"]["xyz"]["values"] = [0, 1]
    with pytest.raises(ValueError, match="declared optional record type: Incorrect array length"):
        from_json(json.dumps(data))


def test_duplicate_json_keys_rejected(observation):
    text = to_json(observation).replace(
        '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
    )
    with pytest.raises(ValueError, match="Duplicate"):
        from_json(text)


def test_formula_text_escaped_without_changing_canonical_data(observation):
    record = replace(
        observation,
        sample_id="=1+1",
        backend_reported=replace(
            observation.backend_reported,
            xyz=replace(observation.backend_reported.xyz, values=(-1, 0, 3)),
        ),
    )
    text = to_csv(record)
    row = list(csv.DictReader(io.StringIO(text)))[0]
    assert row["sample_id"] == "'=1+1"
    assert row["xyz_X"] == "-1"
    assert from_csv(text) == record


def test_csv_conflict_or_truncation_rejected(observation):
    text = to_csv(observation)
    with pytest.raises(ValueError, match="convenience columns disagree"):
        from_csv(text.replace("synthetic-sample", "modified", 1))
    with pytest.raises(ValueError, match="exactly one complete observation"):
        from_csv(text.splitlines()[0])


def test_save_failure_retains_reading_and_retry_without_acquisition(
    observation, tmp_path, monkeypatch
):
    missing = tmp_path / "absent" / "reading.json"
    with pytest.raises(SaveError) as failure:
        save(observation, missing)
    assert failure.value.observation is observation
    assert from_json(failure.value.serialized) == observation

    def refuse(*args, **kwargs):
        pytest.fail("Persistence must not acquire")

    monkeypatch.setattr(SimulatedDevice, "measure", refuse)
    path = save(failure.value.observation, tmp_path / "復元 café.json").path
    assert from_json(path.read_text()) == observation
    with pytest.raises(SaveError):
        save(observation, path)
    assert from_json(path.read_text()) == observation
    assert not list(tmp_path.glob(".measurement-*"))


def test_failed_atomic_publish_has_no_partial_destination(observation, tmp_path, monkeypatch):
    def fail_link(*args):
        raise OSError("synthetic publishing failure")

    monkeypatch.setattr(os, "link", fail_link)
    destination = tmp_path / "reading.csv"
    with pytest.raises(SaveError) as failure:
        save(observation, destination, format="csv")
    assert not destination.exists()
    assert not list(tmp_path.iterdir())
    assert from_csv(failure.value.serialized) == observation


def test_cli_save_failure_recovery_and_export(tmp_path, capsys, monkeypatch):
    result = cli.main(
        [
            "acquire",
            "--device",
            "simulator",
            "--white",
            "80",
            "100",
            "120",
            "--output",
            str(tmp_path / "missing" / "a.json"),
        ]
    )
    output = capsys.readouterr()
    assert result == 2
    record = from_json(output.out)
    assert "retained on stdout" in output.err
    recovery = tmp_path / "recovery.json"
    recovery.write_text(output.out)

    def fail_acquire(*args, **kwargs):
        pytest.fail("Export cannot acquire")

    monkeypatch.setattr(SimulatedDevice, "measure", fail_acquire)
    target = tmp_path / "reading.csv"
    assert cli.main(["export", str(recovery), "--output", str(target), "--format", "csv"]) == 0
    assert from_csv(target.read_text()) == record


def test_cli_invalid_white_refused_before_acquisition(monkeypatch, capsys):
    def fail_acquire(*args, **kwargs):
        pytest.fail("Invalid white must fail before acquisition")

    monkeypatch.setattr(SimulatedDevice, "measure", fail_acquire)
    assert cli.main(["acquire", "--device", "simulator", "--white", "0", "100", "120"]) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX signal delivery test; token cancellation tested on all OS",
)
def test_cli_sigint_stops_without_result(tmp_path):
    target = tmp_path / "cancelled.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "color_measurement",
            "acquire",
            "--device",
            "simulator",
            "--delay",
            "30",
            "--output",
            str(target),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    seen = Event()
    diagnostics = []

    def drain():
        for line in process.stderr:
            diagnostics.append(line)
            if "SIMULATED acquisition" in line:
                seen.set()

    thread = Thread(target=drain)
    thread.start()
    try:
        assert seen.wait(5)
        process.send_signal(signal.SIGINT)
        process.wait(timeout=5)
        thread.join(5)
        assert process.returncode == 130
        assert process.stdout.read() == ""
        assert not target.exists()
        assert "no physical instrument" in "".join(diagnostics)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        thread.join(5)


def test_import_and_help_have_no_acquisition(monkeypatch, capsys):
    def fail(*args, **kwargs):
        pytest.fail("help/devices must not acquire")

    monkeypatch.setattr(SimulatedDevice, "measure", fail)
    assert cli.main(["devices"]) == 0
    assert json.loads(capsys.readouterr().out)["provenance"] == "simulated"
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert stopped.value.code == 0


def test_large_csv_field_roundtrip_restores_limit(observation):
    record = replace(observation, raw_backend_output="synthetic " * 20000)
    original_limit = csv.field_size_limit()
    assert from_csv(to_csv(record)) == record
    assert csv.field_size_limit() == original_limit


def test_save_cleanup_failure_does_not_mask_recovery(observation, tmp_path, monkeypatch):
    from pathlib import Path

    def fail_link(*args):
        raise OSError("publish failed")

    def fail_unlink(*args, **kwargs):
        raise PermissionError("cleanup failed")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", fail_link)
        patch.setattr(Path, "unlink", fail_unlink)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(SaveError) as failure:
                save(observation, tmp_path / "out.json")
        assert failure.value.observation is observation
        assert from_json(failure.value.serialized) == observation
        assert "temporary file" in failure.value.__notes__[0]


def test_successful_save_reports_cleanup_failure(observation, tmp_path, monkeypatch, caplog):
    from pathlib import Path

    def fail_unlink(*args, **kwargs):
        raise PermissionError("cleanup failed")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_unlink)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            target = save(observation, tmp_path / "out.json").path
    assert "temporary file" in caplog.text
    assert from_json(target.read_text()) == observation


def test_missing_lab_white_is_rejected(observation):
    data = asdict(compute(observation, reference_white=(80, 100, 120)))
    data["project_computed"]["lab"]["reference_white"] = None
    with pytest.raises(ValueError, match="explicit reference white"):
        from_json(json.dumps(data))


def test_enormous_json_integer_is_validation_error(observation):
    data = asdict(observation)
    data["backend_reported"]["xyz"]["values"] = [10**400, 0, 0]
    # The optional-record refusal retains the invalid quantity's reason.
    with pytest.raises(ValueError, match="declared optional record type"):
        from_json(json.dumps(data))


def test_cli_csv_preserves_embedded_crlf(observation, tmp_path, capsys):
    record = replace(observation, sample_id="synthetic\r\nsample")
    source = save(record, tmp_path / "source.csv", format="csv").path
    target = tmp_path / "target.json"
    assert cli.main(["export", str(source), "--input-format", "csv", "--output", str(target)]) == 0
    assert from_json(target.read_text()) == record
    assert from_json(capsys.readouterr().out) == record


def test_cli_unicode_record_survives_ascii_stdout():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "color_measurement",
            "acquire",
            "--device",
            "simulator",
            "--sample-id",
            "測定 café",
        ],
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        capture_output=True,
        timeout=5,
    )
    assert completed.returncode == 0
    assert from_json(completed.stdout.decode("ascii")).sample_id == "測定 café"
