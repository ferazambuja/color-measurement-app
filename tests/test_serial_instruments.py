"""Direct transaction ports over scripted bytes; no OS ports or physical equipment."""

import json
import time
from threading import Event

import pytest
from test_serial_settings import cr_report
from test_serial_transport import Clock

from color_measurement import chart_context
from color_measurement.operation_records import result_from_json, result_to_json
from color_measurement.operations import OperationBusyError
from color_measurement.serial_frontend import DirectSerialFrontend
from color_measurement.serial_instruments import (
    MODELS,
    DirectSerialInstrument,
    direct_serial_catalog,
)
from color_measurement.serial_protocols import SerialInstrumentError
from color_measurement.serial_settings import cr_settings_specs
from color_measurement.serial_transport import SerialCancelled, SerialProtocolError


class ScriptedPort:
    def __init__(self, script):
        self.script = list(script)
        self.buffer = bytearray()
        self.command = bytearray()
        self.commands = []
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self.buffer)

    def read(self, count):
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value

    def write(self, data):
        self.command.extend(data)
        expected, response = self.script[0]
        assert expected.startswith(bytes(self.command)), (bytes(self.command), expected)
        if self.command == expected:
            self.commands.append(bytes(self.command))
            self.command.clear()
            self.script.pop(0)
            self.buffer.extend(response)
        return len(data)

    def close(self):
        self.is_open = False


def exchange(command, reply):
    response =reply if isinstance(reply, bytes) else (reply + "\r\n").encode() if reply else b""
    return command.encode() + b"\r\n", response


def startup(model, *, serial=None):
    if model.startswith("CR-"):
        rows = [
            ("RC Model", f"OK:0:RC Model:{model}"),
            ("RC ID", "OK:0:RC ID:" + (serial or "CR-SOFTWARE")),
            ("RC Firmware", "OK:0:RC Firmware:1.26"),
        ]
    else:
        rows = [
            ("RMTS,1", "OK00"),
            (
                "IDDR",
                f"OK00,{model},"
                + ("1" if model == "CS-2000" else "2")
                + ","
                + (serial or "1234567"),
            ),
            ("BALR", "OK00,1,1"),
            ("BALS,1,0", "OK00"),
            ("MSWE,0", "OK00"),
        ]
    return [exchange(command, reply) for command, reply in rows]


def shutdown(model):
    rows = [("BALS,1,1", "OK00"), ("RMTS,0", "OK00")] if model.startswith("CS-") else []
    return [exchange(command, reply) for command, reply in rows]


def meter(model, script):
    port, clock = ScriptedPort(script), Clock()
    instrument = DirectSerialInstrument(
        model, "software-selected-port", transport=port, clock=clock
    )
    return instrument, port


@pytest.mark.parametrize("model", ["PR-655", "PR-670"])
def test_retired_pr_direct_models_refuse_before_any_port_access(monkeypatch, model):
    from color_measurement import serial_instruments

    monkeypatch.setattr(serial_instruments, "open_serial_port",
                        lambda *args, **kwargs: pytest.fail("Retired PR route opened a port"))
    with pytest.raises(ValueError, match="supported direct model"):
        DirectSerialInstrument(model, "software-selected-port")
    assert model not in {row["model_enum"] for row in direct_serial_catalog()["models"]}


@pytest.mark.parametrize("model", MODELS)
def test_direct_connect_is_explicit_and_checks_native_identity_before_use(model):
    instrument, port = meter(model, startup(model) + shutdown(model))
    assert not port.commands and instrument.identity is None
    identity = instrument.connect()
    assert identity.model == model and identity.backend == "direct_serial"
    assert identity.serial is not None and instrument.provenance == "simulated"
    assert instrument.close() is True and not port.is_open and not port.script
    assert instrument.cleanup["port_closed"] is True


def cr_capture_script(model, *, calibration="None", sync="None", exposure="10 msec"):
    rows = [
        ("Time", "NA"), ("Accessory", "Lens"), ("Filter", "None,None,None"),
        ("Aperture", "one degree"), ("Mode", "Colorimeter" if model == "CR-100" else "Spectral"),
        ("ExposureMode", "Auto"), ("Exposure", exposure), ("MaxAutoExposure", "1000 msec"),
        ("RangeMode", "Auto"), ("Range", "D"), ("SyncMode", sync),
    ]
    if sync != "None":
        rows.append(("SyncFreq", "60 Hz"))
    rows.extend([("ExposureX", "3"), ("UserCalibMode", calibration)])
    if calibration == "unsupported":
        rows.extend([("MatrixMode", "Enabled"), ("Matrix", "legacy matrix")])
    elif calibration == "Matrix":
        rows.append(("Matrix", "native matrix"))
    elif calibration == "Match":
        rows.append(("Match", "native match"))
    if model != "CR-100":
        rows.append(("Speed", "Fast"))
    rows.append(("Warnings", "native warning"))
    if model != "CR-100":
        rows.append(("CMF", "0"))
    errors = {"unsupported": "ER:-500:RM UserCalibMode:unsupported",
              "instrument-error": "ER:-336:RM UserCalibMode:No Matrix exists for given ID"}
    return [exchange("RM " + token, errors.get(payload, f"OK:1:RM {token}:{payload}"))
            for token, payload in rows]


def acquisition_script(model):
    if model.startswith("CR-"):
        acquisition = [
            exchange("M", "OK:1:M:complete"),
            exchange("RM XYZ", "OK:0:RM XYZ:1,2,3"),
        ]
        if model != "CR-100":
            acquisition += [
                exchange("RM Radiometric", "OK:0:RM Radiometric:0,2,3"),
                exchange(
                    "RM Spectrum", "OK:0:RM Spectrum:400,700,100,4\r\n1\r\n-2\r\n3\r\n4"
                ),
            ]
        acquisition += cr_capture_script(model)
    else:
        acquisition = [exchange("MEAS,1", "OK00,002\r\nOK00")]
        acquisition += [
            exchange(f"MEDR,1,0,{block}", "OK00," + ",".join(["-1.25"] * count))
            for block, count in enumerate((100, 100, 100, 101), 1)
        ]
        acquisition += [
            exchange("MEDR,2,0,01", "OK00,1,2,3"),
            exchange("MEDR,0,0,1", "OK00,2,0,1000000,1,0,0,0,0"),
        ]
    return acquisition


@pytest.mark.parametrize("model,command,reply", [
    ("CR-100", "M", "ER:-300:M:measurement failed"),
    ("CR-100", "RM XYZ", "ER:-300:RM XYZ:unavailable"),
    ("CS-2000", "MEAS,1", "ER10"),
    ("CS-2000", "MEDR,1,0,1", "ER10"),
    ("CS-2000", "MEDR,1,0,1", "ER17"),
])
def test_direct_terminal_measurement_error_keeps_connection_and_permits_explicit_cleanup(
    model, command, reply,
):
    acquisition = acquisition_script(model)
    at = next(index for index, (sent, _) in enumerate(acquisition)
              if sent == exchange(command, "")[0])
    failed = acquisition[:at] + [exchange(command, reply)]
    instrument, port = meter(
        model, startup(model) + settings_script(model) + settings_script(model)
        + failed + shutdown(model),
    )
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, instrument.read_settings())
    operation = frontend.prepare(json.dumps(
        direct_request(frontend, options=dict(automatic=False)),
    ))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and not result.patches and result.software_stopped
    assert reply in result.error
    assert instrument._retained_capture is None
    assert port.is_open and instrument.channel.synchronized and not instrument.measurement_active
    assert json.loads(frontend.describe_json())["modes"][0]["available"]
    assert frontend.close() and not port.script
    if model.startswith("CS-"):
        assert instrument.cleanup["backlight_restored"] and instrument.cleanup["remote_released"]


@pytest.mark.parametrize("model,reply", [
    ("CR-100", "ER:-300:M:measurement failed\r\nlate extra data"),
    ("CS-2000", "ER10\r\nlate extra data"),
])
def test_terminal_error_with_surplus_does_not_authorize_restoration(model, reply):
    command = "M" if model.startswith("CR-") else "MEAS,1"
    instrument, port = meter(model, startup(model) + [exchange(command, reply)])
    instrument.connect()
    with pytest.raises(SerialProtocolError, match="surplus"):
        instrument._acquire()
    assert instrument.measurement_active and not instrument.channel.synchronized
    sent = list(port.commands)
    assert instrument.close() and port.commands == sent


@pytest.mark.parametrize("late_surplus", [False, True])
def test_cr_spectrum_processing_guard_defers_next_command_and_rejects_delayed_surplus(late_surplus):
    clock = Clock()

    class GuardedPort(ScriptedPort):
        spectrum_received_at = None
        injected = False

        @property
        def in_waiting(self):
            if (late_surplus and self.spectrum_received_at is not None and not self.injected
                    and clock.monotonic() >= self.spectrum_received_at + 0.1):
                self.buffer.extend(b"5\r\n")
                self.injected = True
            return len(self.buffer)

        def read(self, count):
            value = super().read(count)
            if self.commands[-1] == b"RM Spectrum\r\n":
                self.spectrum_received_at = clock.monotonic()
            return value

    model = "CR-250"
    port = GuardedPort(startup(model) + acquisition_script(model)
                       + [exchange("RC Model", "OK:0:RC Model:CR-250")])
    instrument = DirectSerialInstrument(
        model, "software-selected-port", transport=port, clock=clock,
    )
    instrument.connect()
    if late_surplus:
        with pytest.raises(SerialProtocolError, match="surplus"):
            instrument._acquire()
        assert port.injected and not instrument.channel.synchronized
        assert bytes.fromhex(instrument.exchanges[-1]["channel"]["received_hex"]).endswith(b"5\r\n")
        assert port.commands[-1] == b"RM Spectrum\r\n"
    else:
        instrument._acquire()
        assert clock.monotonic() - port.spectrum_received_at >= 0.2
        assert instrument._query("RC Model") == "CR-250"
    assert instrument.close()


@pytest.mark.parametrize("model", MODELS)
def test_direct_capture_preserves_only_actual_native_quantities_and_complete_model_grids(model):
    instrument, port = meter(model, startup(model) + acquisition_script(model) + shutdown(model))
    instrument.connect()
    instrument.exchanges = []
    native, metadata = instrument._acquire()
    assert instrument.channel.synchronized and instrument.measurement_active is False
    assert native.xyz.values == (1, 2, 3) and native.xyz.observer == "CIE1931_2"
    if model == "CR-100":
        assert native.spectrum is None and native.xyz.scale == "unverified"
    elif model.startswith("CR-"):
        assert native.spectrum.values == (1, -2, 3, 4)
        assert metadata["spectral_density_units_confirmed"] is False
    else:
        assert native.spectrum.unit == "W/(m^2 sr nm)" and len(native.spectrum.values) == 401
        assert metadata["integration_time_microseconds"] == 1000000
        assert metadata["announced_duration_seconds"] == 2
    assert instrument.close() and not port.script


def cr_settings_script(model, values, *, firmware=(1, 26), unavailable_bounds=None):
    report = cr_report(model)
    unavailable_bounds = unavailable_bounds or {}
    cache, rows = set(), []
    for key, spec in cr_settings_specs(model).items():
        if firmware < spec["since"]:
            continue
        token, kind = spec["token"], spec["kind"]
        control = report["controls"][key]
        if kind in {"choice", "index", "filter"}:
            token = "Filter" if kind == "filter" else token
            command = "RC " + token
            if command not in cache:
                columns = 3 if token in {"Accessory", "Filter"} else 2
                lines = [
                    f"{item['value']},{item['label']}" + (",0" if columns == 3 else "")
                    for item in control["choices"]
                ]
                rows.append((command, f"OK:0:{command}:{len(lines)}\r\n" + "\r\n".join(lines)))
                cache.add(command)
        else:
            token = "Exposure" if key == "maxAutoExposureMs" else token
            suffix = " msec" if kind == "milliseconds" else " Hz" if kind == "hertz" else ""
            for index, (prefix, field) in enumerate((("Min", "minimum"), ("Max", "maximum"))):
                command = "RC " + prefix + token
                if command not in cache:
                    payload = (unavailable_bounds[token][index] if token in unavailable_bounds
                               else f"{control[field]}{suffix}")
                    # A complete framed native error is supplied verbatim.
                    rows.append((command, payload if payload.startswith("ER:")
                                 else f"OK:0:{command}:{payload}"))
                    cache.add(command)
        if key == "maxAutoExposureMs" or token in unavailable_bounds:
            continue
        command = "RS " + token
        if command in cache:
            continue
        if kind == "filter":
            payload = ",".join(
                next(
                    item["label"]
                    for item in report["controls"][f"filter{index}"]["choices"]
                    if item["value"] == values[f"filter{index}"]
                )
                for index in (1, 2, 3)
            )
        elif kind == "choice":
            payload = next(
                item["label"] for item in control["choices"] if item["value"] == values[key]
            )
        else:
            payload = str(values[key]) + (
                " msec" if kind == "milliseconds" else " Hz" if kind == "hertz" else ""
            )
        rows.append((command, f"OK:0:{command}:{payload}"))
        cache.add(command)
    if firmware >= (1, 4):
        rows.append(("RS Aperture", "OK:0:RS Aperture:one degree"))
    return [exchange(command, reply) for command, reply in rows]


@pytest.mark.parametrize(
    "calibration", ["Matrix", "Match", "unsupported", "instrument-error", "unknown"],
)
def test_cr_capture_metadata_is_native_and_gates_dependent_queries(calibration):
    model = "CR-100"
    acquisition = acquisition_script(model)[:2] + cr_capture_script(
        model, calibration=calibration, sync="Manual", exposure="17 msec",
    )
    instrument, port = meter(model, startup(model) + acquisition + shutdown(model))
    instrument.connect()
    native, metadata = instrument._acquire()
    values = metadata["capture_settings"]
    entries = {row["field"]: row for row in metadata["capture_responses"]}
    assert native.xyz.values == (1, 2, 3) and native.spectrum is None
    assert values["exposureMs"] == 17 and values["exposureMultiplier"] == 3
    assert values["syncFrequencyHz"] == 60 and values["warnings"] == "native warning"
    assert values["deviceTime"] is None and entries["deviceTime"]["status"] == "unavailable"
    assert entries["exposureMs"]["code"] == 1 and entries["exposureMs"]["unit"] == "ms"
    assert entries["speed"]["status"] == "notApplicable" and entries["cmfIndex"]["value"] is None
    if calibration == "unknown":
        assert entries["userCalibrationMode"]["status"] == "unparsed"
        assert entries["userCalibrationMode"]["response"] == "unknown"
        assert entries["matrixId"]["status"] == "dependencyUnavailable"
    elif calibration == "unsupported":
        assert entries["userCalibrationMode"]["status"] == "unsupported"
        assert entries["userCalibrationMode"]["code"] == -500
        assert values["legacyMatrixMode"] == "Enabled" and values["matrixId"] == "legacy matrix"
    elif calibration == "instrument-error":
        # Only -500 means an unsupported query. Another native error is an
        # instrument error and does not authorize the legacy MatrixMode query.
        assert entries["userCalibrationMode"]["status"] == "instrumentError"
        assert entries["userCalibrationMode"]["code"] == -336
        assert entries["legacyMatrixMode"]["status"] == "notApplicable"
        assert entries["matrixId"]["status"] == "dependencyUnavailable"
        assert b"RM MatrixMode\r\n" not in port.commands
    else:
        assert values["matrixId" if calibration == "Matrix" else "matchId"] is not None
    assert instrument.close() and not port.script


def test_cr_unparsed_optional_metadata_does_not_discard_native_reading():
    model = "CR-250"
    acquisition = acquisition_script(model)[:4] + cr_capture_script(model, exposure="junk")
    instrument, port = meter(model, startup(model) + acquisition + shutdown(model))
    instrument.connect()
    native, metadata = instrument._acquire()
    entries = {row["field"]: row for row in metadata["capture_responses"]}
    assert native.spectrum.values == (1, -2, 3, 4) and native.xyz.values == (1, 2, 3)
    assert entries["exposureMs"]["status"] == "unparsed"
    assert metadata["capture_settings"]["exposureMs"] is None and instrument.channel.synchronized
    assert entries["syncFrequencyHz"]["status"] == "notApplicable"
    assert instrument.close() and not port.script


@pytest.mark.parametrize("failure_type", [None, KeyboardInterrupt, SystemExit, SerialCancelled])
def test_cr_complete_capture_survives_later_metadata_failure(monkeypatch, failure_type):
    # None is a malformed RM reply; the others interrupt the first RM write after
    # the complete M/RM XYZ capture. Control objects are not ordinary Exceptions.
    model = "CR-100"
    reply = "malformed" if failure_type is None else "OK:1:RM Time:NA"
    acquisition = acquisition_script(model)[:2] + [exchange("RM Time", reply)]
    instrument, port = meter(model, startup(model) + settings_script(model) * 2 + acquisition)
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, instrument.read_settings())
    if failure_type is not None:
        failure, write = failure_type("Post-capture metadata interrupted"), port.write

        def interrupted(data):
            if bytes(port.command) + data == b"RM Time\r\n":
                raise failure
            return write(data)

        monkeypatch.setattr(port, "write", interrupted)
    operation = frontend.prepare(json.dumps(
        direct_request(frontend, options=dict(automatic=False)),
    ))
    operation.dispatch()
    result, events = drain(operation)
    assert result.status == ("cancelled" if failure_type is SerialCancelled else "failed")
    assert result.software_stopped and len(result.patches) == 1
    assert result.patches[0].observation.backend_reported.xyz.values == (1, 2, 3)
    assert any(event["kind"] == "patch_reference" for event in events)
    assert ("Malformed CR response" if failure_type is None
            else "Post-capture metadata interrupted") in result.error
    assert not port.is_open and not instrument._ownership.locked()
    # The interrupted RM write is the last attempted command; no cleanup write follows.
    assert [sent for sent, _ in port.script] == ([] if failure_type is None else [b"RM Time\r\n"])


def test_cr_failed_new_capture_cannot_republish_an_earlier_completed_capture():
    model = "CR-100"
    instrument, port = meter(
        model, startup(model) + settings_script(model) * 2 + acquisition_script(model)
        + settings_script(model) + [exchange("M", "ER:-300:M:measurement failed")]
        + shutdown(model),
    )
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, instrument.read_settings())
    results = []
    for _ in range(2):
        operation = frontend.prepare(json.dumps(
            direct_request(frontend, options=dict(automatic=False)),
        ))
        operation.dispatch()
        results.append(drain(operation)[0])
    completed, failed = results
    assert completed.status == "completed" and len(completed.patches) == 1
    assert completed.patches[0].observation.backend_reported.xyz.values == (1, 2, 3)
    # The earlier capture must not become the failed exposure's reading.
    assert failed.status == "failed" and not failed.patches
    assert "ER:-300:M:measurement failed" in failed.error
    assert instrument._retained_capture is None
    assert frontend.close() and not port.script


@pytest.mark.parametrize("firmware,unsupported", [("1.03", False), ("1.04", True)])
def test_cr_aperture_respects_firmware_and_retains_unsupported_reply(firmware, unsupported):
    model = "CR-100"
    initial = startup(model)
    initial[-1] = exchange("RC Firmware", f"OK:0:RC Firmware:{firmware}")
    settings = cr_settings_script(model, cr_report(model)["values"],
                                  firmware=tuple(map(int, firmware.split("."))))
    if unsupported:
        settings[-1] = exchange("RS Aperture", "ER:-500:RS Aperture:unsupported")
    instrument, port = meter(model, initial + settings + shutdown(model))
    instrument.connect()
    report = instrument.read_settings()
    assert "aperture" not in report["values"] and not instrument.measurement_active
    if unsupported:
        assert report["exchanges"][-1]["error"] and instrument.channel.synchronized
    else:
        assert not report["exchanges"]
    assert instrument.close() and not port.script


@pytest.mark.parametrize("minimum,maximum", [(0, 1000), (1001, 1000)])
def test_cr_invalid_native_exposure_limits_refuse_before_any_setter(minimum, maximum):
    model = "CR-100"
    settings = cr_settings_script(
        model, cr_report(model)["values"],
        unavailable_bounds={"Exposure": (f"{minimum} msec", f"{maximum} msec")},
    )
    instrument, port = meter(model, startup(model) + settings + shutdown(model))
    instrument.connect()
    with pytest.raises(ValueError, match="exposureMs is unavailable"):
        instrument.apply_settings(dict(exposureMs=0))
    assert not any(command.startswith(b"SM ") for command in port.commands)
    assert port.is_open and instrument.channel.synchronized
    assert instrument.close() and not port.script


@pytest.mark.parametrize("reply,synchronized", [
    ("OK:0:wrong command:1 msec", False),
    ("OK:0:RC MinExposure:1 msec\r\nsurplus", False),
])
def test_cr_numeric_capability_framing_error_stays_fatal(reply, synchronized):
    model = "CR-100"
    settings = cr_settings_script(model, cr_report(model)["values"])
    at = next(index for index, (sent, _) in enumerate(settings)
              if sent == b"RC MinExposure\r\n")
    settings[at] = exchange("RC MinExposure", reply)
    instrument, port = meter(model, startup(model) + settings[:at + 1] + shutdown(model))
    instrument.connect()
    with pytest.raises(SerialProtocolError):
        instrument.read_settings()
    assert instrument.channel.synchronized is synchronized
    assert port.commands[-1] == b"RC MinExposure\r\n"
    assert instrument.close() and not port.script


_ORDERED = "CR {} native limits must be positive and ordered"
_UNINTERPRETED = "CR {} numeric bounds could not be interpreted"


@pytest.mark.parametrize("key,token,minimum,maximum,reason", [
    ("exposureMs", "Exposure", "0 msec", "1000 msec", _ORDERED),
    ("exposureMs", "Exposure", "1001 msec", "1000 msec", _ORDERED),
    ("exposureMs", "Exposure", "NA", "1000 msec", "CR {} numeric bounds were not reported"),
    ("exposureMs", "Exposure", "-1 msec", "1000 msec", _UNINTERPRETED),
    ("exposureMs", "Exposure", "ER:-500:RC MinExposure:unsupported", "1000 msec",
     "CR {} numeric bounds are unsupported"),
    ("exposureMultiplier", "ExposureX", "0", "1000", _UNINTERPRETED),
    ("exposureMultiplier", "ExposureX", "1.00000000000000001", "1000", _UNINTERPRETED),
    ("syncFrequencyHz", "SyncFreq", "-1 Hz", "1000 Hz", _UNINTERPRETED),
    ("syncFrequencyHz", "SyncFreq", "nan Hz", "1000 Hz", _UNINTERPRETED),
])
def test_cr_framed_invalid_bound_disables_control_without_losing_connection(
    key, token, minimum, maximum, reason,
):
    model = "CR-100"
    bounds = {token: (minimum, maximum)}
    instrument, port = meter(
        model, startup(model) + cr_settings_script(
            model, cr_report(model)["values"], unavailable_bounds=bounds,
        ) + shutdown(model),
    )
    instrument.connect()
    report = instrument.read_settings()
    frontend = DirectSerialFrontend(instrument, report)
    options = {row["id"]: row for row in json.loads(frontend.describe_json())["options"]}
    dependent = [key, "maxAutoExposureMs"] if key == "exposureMs" else [key]
    for field in dependent:
        assert not report["controls"][field]["available"] and field not in report["values"]
        assert report["controls"][field]["minimum"] is None
        assert report["controls"][field]["maximum"] is None
        assert not options[field]["available"]
        # The specific bound reason, not the generic no-choices fallback.
        assert report["controls"][field]["unavailable_reason"] == reason.format(field)
        assert options[field]["unavailable_reason"] == reason.format(field)
        assert options[field]["by_mode"]["emissive_spot"]["unavailable_reason"] == (
            reason.format(field))
    assert instrument.channel.synchronized and port.is_open
    for prefix in ("Min", "Max"):
        command = f"RC {prefix}{token}"
        assert port.commands.count(exchange(command, "")[0]) == 1
        item = next(row for row in report["exchanges"] if row["requested_command"] == command)
        assert (minimum if prefix == "Min" else maximum).encode() in bytes.fromhex(
            item["channel"]["received_hex"],
        )
    assert frontend.close() and not port.script


@pytest.mark.parametrize("model,token", [
    ("CR-100", "Exposure"), ("CR-250", "Exposure"), ("CR-300", "Exposure"),
    ("CR-100", "ExposureX"), ("CR-100", "SyncFreq"),
])
def test_cr_automatic_capture_needs_no_unavailable_manual_numeric_bound(model, token):
    values = dict(cr_report(model)["values"], exposureMode=2)
    if model == "CR-100":
        values.update(mode=9, rangeMode=3)
    minimum, maximum = {"Exposure": ("0 msec", "1000 msec"),
                        "ExposureX": ("0", "1000"), "SyncFreq": ("0 Hz", "1000 Hz")}[token]
    settings = cr_settings_script(model, values, unavailable_bounds={token: (minimum, maximum)})
    instrument, port = meter(
        model, startup(model) + settings * 2 + acquisition_script(model) + shutdown(model),
    )
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, instrument.read_settings())
    operation = frontend.prepare(json.dumps(direct_request(frontend)))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "completed" and len(result.patches) == 1, result.error
    assert result.patches[0].observation.backend_reported.xyz.values == (1, 2, 3)
    assert instrument.channel.synchronized and port.is_open
    assert not any(command.startswith(b"SM ") for command in port.commands)
    assert frontend.close() and not port.script


@pytest.mark.parametrize("model", ["CR-100", "CR-250", "CR-300"])
def test_direct_cr_recipe_is_acknowledged_then_independently_read_back(model):
    before = cr_report(model)["values"]
    requested = dict(exposureMode=2)
    commands = [("SM ExposureMode 2", "OK:0:ExposureMode:")]
    if model == "CR-100":
        requested.update(mode=9, rangeMode=3)
        commands = [
            ("SM Mode 9", "OK:0:SM Mode:"),
            *commands,
            ("SM RangeMode 3", "OK:0:RangeMode:"),
        ]
    script = (
        startup(model)
        + cr_settings_script(model, before)
        + [exchange(command, reply) for command, reply in commands]
        + cr_settings_script(model, dict(before, **requested))
        + shutdown(model)
    )
    instrument, port = meter(model, script)
    instrument.connect()
    receipt = instrument.apply_settings({}, automatic=True)
    assert receipt["requested"] == requested and receipt["status"] == "applied"
    assert all(item["readback_verified"] for item in receipt["acknowledgements"])
    assert not instrument._settings_uncertain
    assert instrument.close() and not port.script


def test_direct_incomplete_exposure_closes_host_without_sending_cleanup_or_claiming_device_abort():
    instrument, port = meter(
        "CS-2000", startup("CS-2000") + [exchange("MEAS,1", "OK00,242")]
    )
    instrument.connect()
    instrument.measurement_timeout = 10
    with pytest.raises(SerialProtocolError, match="exceeds selected host"):
        instrument._acquire()
    assert not instrument.channel.synchronized and instrument.measurement_active
    before = list(port.commands)
    assert instrument.close() and port.commands == before
    assert instrument.cleanup["remote_released"] is None


def test_direct_failed_close_keeps_owned_port_and_original_cleanup_control(monkeypatch):
    instrument, port = meter("CS-2000", startup("CS-2000") + shutdown("CS-2000"))
    instrument.connect()
    original = KeyboardInterrupt("Original cleanup control")
    original_query = instrument._query

    def interrupted(command, **kwargs):
        if command.startswith("BALS,"):
            raise original
        return original_query(command, **kwargs)

    monkeypatch.setattr(instrument, "_query", interrupted)
    with pytest.raises(KeyboardInterrupt) as caught:
        instrument.close()
    assert caught.value is original and instrument.cleanup["port_closed"] is True
    assert port.is_open is False


def direct_request(frontend, *, workflow="spot", options=None):
    descriptor = json.loads(frontend.describe_json())
    return dict(
        schema_version=1,
        device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"],
        mode="emissive_spot",
        workflow=workflow,
        patch_ids=["source-sample"] if workflow == "spot" else [],
        options={} if options is None else options,
        layout={},
        event_contract="retained_patch_v1",
    )


def drain(operation):
    events, deadline = [], time.monotonic() + 3
    while time.monotonic() < deadline:
        raw = operation.poll_event_json()
        if raw:
            event = json.loads(raw)
            events.append(event)
            if event["kind"] == "instruction_required":
                operation.respond(operation.operation_id, event["payload"]["request_id"], "accept")
        elif operation.done:
            while raw := operation.poll_event_json():
                events.append(json.loads(raw))
            return result_from_json(operation.result_json()), events
        else:
            time.sleep(0.001)
    raise AssertionError("Scripted direct operation did not complete")


def test_common_direct_frontend_is_inert_until_dispatch_and_retains_native_operation_records():
    model = "CR-100"
    current = cr_report(model)
    current["values"].update(mode=9, exposureMode=2, rangeMode=3)
    acquisition = [
        exchange("M", "OK:0:M:complete"),
        exchange("RM XYZ", "OK:0:RM XYZ:1,2,3"),
    ]
    instrument, port = meter(
        model,
        startup(model)
        + cr_settings_script(model, current["values"])
        + acquisition
        + cr_capture_script(model)
        + shutdown(model),
    )
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, current)
    request = direct_request(frontend)
    descriptor = json.loads(frontend.describe_json())
    assert descriptor["contract"] == "direct_serial_instrument_v1"
    assert (
        not {"mode_capabilities", "trigger_capabilities", "filter_capabilities"} & descriptor.keys()
    )
    before = list(port.commands)
    operation = frontend.prepare(json.dumps(request))
    assert port.commands == before and not operation.done
    operation.dispatch()
    result, events = drain(operation)
    assert result.status == "completed" and result.software_stopped is True
    assert result.instrument_state == "not_applicable" and len(result.patches) == 1
    assert result.patches[0].observation.backend_reported.xyz.values == (1, 2, 3)
    assert result.patches[0].observation.calibration.status == "unknown"
    assert any(event["kind"] == "patch_reference" for event in events)
    assert result_from_json(result_to_json(result)) == result
    assert json.loads(frontend.describe_json()) == descriptor
    assert frontend.close() and not port.script


def test_direct_query_updates_only_changed_material_settings_not_volatile_exchange_stamps():
    model = "CR-100"
    current = cr_report(model)
    instrument, port = meter(
        model,
        startup(model)
        + cr_settings_script(model, current["values"])
        + cr_settings_script(model, current["values"])
        + shutdown(model),
    )
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, current)
    first = frontend.prepare(json.dumps(direct_request(frontend, workflow="mode_snapshot")))
    first.dispatch()
    assert drain(first)[0].status == "completed"
    descriptor = frontend.describe_json()
    second = frontend.prepare(json.dumps(direct_request(frontend, workflow="mode_snapshot")))
    second.dispatch()
    assert drain(second)[0].status == "completed"
    assert frontend.describe_json() == descriptor
    assert frontend.close() and not port.script


def test_direct_prepare_and_predispatch_cancellation_never_send_bytes_or_accept_unknown_options():
    model = "CR-100"
    instrument, port = meter(model, startup(model) + shutdown(model))
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, cr_report(model))
    request = direct_request(frontend)
    before = list(port.commands)
    for options in (
        dict(automatic=1),
        dict(exposureMultiplier=True),
        dict(unknown=None),
        dict(exposureMs=float("nan")),
        dict(speedMode=0),
    ):
        with pytest.raises(ValueError):
            frontend.prepare(json.dumps(dict(request, options=options)))
    operation = frontend.prepare(json.dumps(request))
    operation.cancel(operation.operation_id)
    assert operation.done and port.commands == before
    assert result_from_json(operation.result_json()).status == "cancelled"
    assert frontend.close() and not port.script


def test_direct_failed_port_release_keeps_quarantined_port_and_allows_idle_public_retry(
    monkeypatch,
):
    model = "CR-100"
    current = cr_report(model)
    current["values"].update(mode=9, exposureMode=2, rangeMode=3)
    instrument, port = meter(
        model,
        startup(model)
        + cr_settings_script(model, current["values"])
        + [exchange("M", "unframed reply")],
    )
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, current)
    monkeypatch.setattr(port, "close", lambda: None)
    operation = frontend.prepare(json.dumps(direct_request(frontend)))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and result.software_stopped is True
    assert not result.patches and not instrument._ownership.locked()
    facts = json.loads(dict(result.configuration)["direct_exchanges_json"])
    assert b"unframed reply" in bytes.fromhex(facts[-1]["channel"]["received_hex"])
    original_result = operation.result_json()
    assert json.loads(dict(result.configuration)["direct_cleanup_json"])["port_closed"] is False
    assert not json.loads(frontend.describe_json())["modes"][0]["available"]
    with pytest.raises(SerialProtocolError, match="release"):
        frontend.close()
    monkeypatch.setattr(port, "close", lambda: setattr(port, "is_open", False))
    assert frontend.close() and operation.result_json() == original_result


def cs_settings_script(model, *, channel=0, speed="2,1,2", sync="0"):
    rows = [
        ("SPMR", "OK00," + speed),
        ("SCMR", "OK00," + sync),
        ("STSR", "OK00,0"),
        ("LNSR", "OK00,0"),
        ("NDFR", "OK00,0"),
        ("UCCR", f"OK00,{channel}"),
    ]
    if channel:
        rows.append((f"UCPR,2,{channel:02d}", "OK00,Profile  1"))
    return [exchange(command, reply) for command, reply in rows]


@pytest.mark.parametrize("model", ["CS-2000", "CS-2000A"])
def test_direct_cs_existing_channel_preflight_precedes_setter_and_independent_readback(model):
    script = (
        startup(model)
        + cs_settings_script(model)
        + [exchange("UCPR,2,01", "OK00,Profile  1"), exchange("UCCS,01", "OK00")]
        + cs_settings_script(model, channel=1)
        + shutdown(model)
    )
    instrument, port = meter(model, script)
    instrument.connect()
    receipt = instrument.apply_settings(dict(userCalibrationChannel=1))
    assert receipt["status"] == "applied" and receipt["preflight"][0]["name"] == "Profile  1"
    assert receipt["acknowledgements"][0]["readback_verified"] is True
    assert receipt["after"]["userCalibration"]["status"] == "reported-name"
    assert instrument.close() and not port.script


@pytest.mark.parametrize("name", ["", "short", "x" * 11, "bad\tname  "])
def test_direct_cs_incomplete_or_malformed_channel_preflight_never_sends_a_setter(name):
    model = "CS-2000"
    instrument, port = meter(
        model,
        startup(model) + cs_settings_script(model) + [exchange("UCPR,2,01", "OK00," + name)],
    )
    instrument.connect()
    with pytest.raises(SerialProtocolError):
        instrument.apply_settings(dict(userCalibrationChannel=1))
    assert not any(command.startswith(b"UCCS") for command in port.commands)
    assert instrument.settings_receipt["status"] == "failed"
    assert instrument.close() and not port.script


def settings_script(model):
    if model.startswith("CS-"):
        return cs_settings_script(model)
    values = cr_report(model)["values"]
    values.update(mode=9, exposureMode=2, rangeMode=3)
    return cr_settings_script(model, values)


@pytest.mark.parametrize("model", MODELS)
def test_direct_context_binds_original_identity_readback_and_native_quantity_limits(model):
    instrument, port = meter(
        model, startup(model) + settings_script(model) + settings_script(model)
        + acquisition_script(model)
        + shutdown(model),
    )
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, instrument.read_settings())
    request = direct_request(frontend, options=dict(automatic=False))
    descriptor = json.loads(frontend.describe_json())
    intended = chart_context.intended(descriptor, request)
    assert intended["material"]["route"]["value"] == "DIRECT_SERIAL"
    assert intended["material"]["xyz_unit"]["certainty"] == "unknown"
    operation = frontend.prepare(json.dumps(request))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "completed", result.error
    accepted = chart_context.accepted(descriptor, request, result)
    material = accepted["material"]
    assert material["native_identity:serial"]["value"] == instrument.identity.serial
    assert material["driver"]["certainty"] == "acknowledged"
    assert material["actual_mode"]["certainty"] == "acknowledged"
    assert material["effective_option:automatic"]["value"] is False
    assert {"device_id", "driver", "native_identity:serial"} <= chart_context.source_identity_keys(
        result.patches[0].observation, accepted,
    )
    assert not any(key.startswith("native_connected:") for key in material)
    if model.startswith("CS-"):
        assert material["xyz_unit"]["value"] == "cd/m^2"
        assert material["spectral_unit"]["value"] == "W/(m^2 sr nm)"
        assert material["native_capture_setting:integration_time_microseconds"]["value"] == 1000000
    else:
        assert material["xyz_unit"]["certainty"] == "unknown"
        if model != "CR-100":
            assert material["spectral_unit"]["certainty"] == "unknown"
    with pytest.raises(ValueError, match="exact chart request"):
        chart_context.accepted(descriptor, dict(request, options=dict(automatic=True)), result)
    assert frontend.close() and not port.script


def test_direct_documentary_encoding_failure_still_finishes_and_releases_idle_ownership(
    monkeypatch,
):
    from color_measurement import serial_frontend

    instrument, port = meter("CR-100", startup("CR-100") + settings_script("CR-100"))
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, cr_report())
    operation = frontend.prepare(json.dumps(direct_request(frontend, workflow="mode_snapshot")))
    original = serial_frontend._json

    def fail_exchanges(value):
        if value is instrument.exchanges:
            raise ValueError("Injected documentary failure")
        return original(value)

    monkeypatch.setattr(serial_frontend, "_json", fail_exchanges)
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and "Injected documentary failure" in result.error
    assert result.software_stopped is True and not instrument._ownership.locked()
    assert frontend.close() and not port.script


def test_direct_framed_uncertain_measurement_error_closes_host_without_remote_cleanup():
    model = "CS-2000"
    instrument, port = meter(
        model, startup(model) + settings_script(model) + [exchange("MEAS,1", "ER17")],
    )
    instrument.connect()
    current = dict(values={}, speedLayout="modern")
    frontend = DirectSerialFrontend(instrument, current)
    operation = frontend.prepare(json.dumps(
        direct_request(frontend, options=dict(automatic=False)),
    ))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and result.software_stopped is True
    assert not port.is_open and instrument.measurement_active
    assert not any(command == b"RMTS,0\r\n" for command in port.commands)
    assert not json.loads(frontend.describe_json())["modes"][0]["available"]


def test_direct_reply_budget_never_retains_more_than_the_transaction_byte_bound():
    model = "CR-100"
    instrument, port = meter(model, startup(model) + [exchange("M", "x" * 100)])
    instrument.connect()
    instrument.exchanges = [dict(channel=dict(received_hex="00" * (4 * 1024 * 1024 - 20),
                                            sent_hex=""))]
    with pytest.raises(SerialProtocolError, match="byte cap"):
        instrument._query("M")
    retained = sum(len(item["channel"]["received_hex"]) + len(item["channel"]["sent_hex"])
                   for item in instrument.exchanges) // 2
    assert retained <= 4 * 1024 * 1024
    assert instrument.channel.limit_bytes == 1048576
    assert instrument.close() and not port.script


@pytest.mark.parametrize("code", ["ER00", "ER02"])
def test_direct_cs_busy_calibration_query_preserves_partial_settings_and_blocks_cleanup(code):
    model = "CS-2000"
    script = cs_settings_script(model, channel=1)
    script[-1] = exchange("UCPR,2,01", code)
    instrument, port = meter(model, startup(model) + script)
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, dict(values={}, speedLayout="modern"))
    request = direct_request(frontend, workflow="mode_snapshot", options=dict(automatic=True))
    descriptor = json.loads(frontend.describe_json())
    operation = frontend.prepare(json.dumps(request))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and not port.is_open
    receipt = json.loads(dict(result.configuration)["direct_settings_json"])
    assert receipt["after"] is None and receipt["partial"]["values"]["userCalibrationChannel"] == 1
    assert instrument.measurement_active and not any(command == b"RMTS,0\r\n"
                                                    for command in port.commands)
    context = chart_context.accepted(descriptor, request, result)
    assert context["material"]["effective_option:automatic"]["certainty"] == "unknown"


@pytest.mark.parametrize("speed,key", [("4,1,1", "speedMode"),
                                       ("2,16,1", "integrationTimeSeconds")])
def test_direct_cs_grouped_speed_target_mismatch_is_retained_and_refused(speed, key):
    model = "CS-2000"
    instrument, port = meter(
        model, startup(model) + cs_settings_script(model)
        + [exchange("SPMS,2,1,1", "OK00")]
        + cs_settings_script(model, speed=speed) + shutdown(model),
    )
    instrument.connect()
    with pytest.raises(SerialProtocolError, match=f"readback differs for {key}"):
        instrument.apply_settings(dict(internalNDMode=1))
    assert instrument.settings_receipt["status"] == "failed"
    assert instrument.settings_receipt["after"]["values"]["internalNDMode"] == 1
    assert {"speedMode", "integrationTimeSeconds", "internalNDMode"} <= (
        instrument._settings_uncertain
    )
    assert instrument.close() and not port.script


@pytest.mark.parametrize("group", ["speed", "sync"])
def test_direct_cs_grouped_matching_readback_marks_the_actual_command_acknowledgement(group):
    model = "CS-2000"
    before = cs_settings_script(model, sync="1,06000" if group == "sync" else "0")
    after = cs_settings_script(model, speed="2,1,1" if group == "speed" else "2,1,2",
                               sync="1,07000" if group == "sync" else "0")
    command = "SPMS,2,1,1" if group == "speed" else "SCMS,1,07000"
    instrument, port = meter(model, startup(model) + before
                             + [exchange(command, "OK00")] + after + shutdown(model))
    instrument.connect()
    changes = dict(internalNDMode=1) if group == "speed" else dict(frequencyHz=70)
    receipt = instrument.apply_settings(changes)
    assert receipt["status"] == "applied"
    assert receipt["acknowledgements"][0]["id"] == group
    assert receipt["acknowledgements"][0]["readback_verified"] is True
    assert not instrument._settings_uncertain
    assert instrument.close() and not port.script


@pytest.mark.parametrize("failure", ["cancel", "pending"])
def test_direct_unsent_exchange_preserves_prior_originals_once_and_enforces_journal_bound(failure):
    model = "CR-100"
    instrument, port = meter(model, startup(model))
    instrument.connect()
    original = instrument.exchanges[-1]["channel"].copy()
    original_size = sum(len(row["channel"]["received_hex"]) + len(row["channel"]["sent_hex"])
                        for row in instrument.exchanges) // 2
    # A controlled retained-journal boundary, separate from actual native input bytes.
    instrument.exchanges.insert(0, dict(channel=dict(
        received_hex="00" * (4 * 1024 * 1024 - original_size - 20), sent_hex="",
    )))
    if failure == "cancel":
        event = Event()
        event.set()
        instrument.channel.cancel = event
        error = SerialCancelled
    else:
        port.buffer.extend(b"late\r\n")
        error = SerialProtocolError
    with pytest.raises(error):
        instrument._query("M")
    assert instrument.exchanges[-2]["channel"] == original
    attempted = instrument.exchanges[-1]
    assert attempted["requested_command"] == "M" and attempted["channel"]["command"] == "M"
    assert attempted["channel"]["sent_hex"] == "" and not attempted["channel"]["write_complete"]
    expected = b"late\r\n" if failure == "pending" else b""
    assert bytes.fromhex(attempted["channel"]["received_hex"]) == expected
    retained = sum(len(row["channel"]["received_hex"]) + len(row["channel"]["sent_hex"])
                   for row in instrument.exchanges) // 2
    assert retained <= 4 * 1024 * 1024
    instrument.channel.cancel = None
    assert instrument.close()


@pytest.mark.parametrize("group", ["sync", "normal", "fast"])
def test_direct_cs_null_coupled_targets_recover_only_through_complete_verified_command(group):
    model = "CS-2000"
    before = cs_settings_script(model, sync="1,06000" if group == "sync" else "0")
    mode = 0 if group == "normal" else 1
    changes = dict(syncMode=0) if group == "sync" else dict(speedMode=mode)
    command = "SCMS,0" if group == "sync" else f"SPMS,{mode},2"
    after = cs_settings_script(model, speed="2,1,2" if group == "sync" else f"{mode},2")
    instrument, port = meter(
        model, startup(model) + before + [exchange(command, "ER05")]
        + before + before + [exchange(command, "OK00")] + after + shutdown(model),
    )
    instrument.connect()
    with pytest.raises(SerialInstrumentError, match="ER05"):
        instrument.apply_settings(changes)
    assert instrument._settings_uncertain
    with pytest.raises(ValueError, match="Reapply every uncertain"):
        instrument.apply_settings(dict(closeupLens=1))
    receipt = instrument.apply_settings(changes)
    assert receipt["status"] == "applied" and not instrument._settings_uncertain
    assert receipt["acknowledgements"][0]["readback_verified"] is True
    key = "frequencyHz" if group == "sync" else "integrationTimeSeconds"
    assert receipt["expected_readback"][key] is None and key not in receipt["requested"]
    assert instrument.close() and not port.script


@pytest.mark.parametrize("group", ["sync", "normal", "fast"])
def test_direct_cs_uncertain_group_can_recover_from_already_matching_fresh_readback(group):
    model = "CS-2000"
    mode = 0 if group == "normal" else 1
    changes = dict(syncMode=0) if group == "sync" else dict(speedMode=mode)
    command = "SCMS,0" if group == "sync" else f"SPMS,{mode},2"
    before = cs_settings_script(model, sync="1,06000" if group == "sync" else "0")
    already_applied = cs_settings_script(model, speed="2,1,2" if group == "sync" else f"{mode},2")
    instrument, port = meter(
        model, startup(model) + before + [exchange(command, "ER05")]
        + already_applied + shutdown(model),
    )
    instrument.connect()
    with pytest.raises(SerialInstrumentError):
        instrument.apply_settings(changes)
    receipt = instrument.apply_settings(changes)
    assert receipt["status"] == "unchanged" and not receipt["commands"]
    assert not instrument._settings_uncertain
    key = "frequencyHz" if group == "sync" else "integrationTimeSeconds"
    assert receipt["expected_readback"][key] is None
    assert instrument.close() and not port.script


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_direct_final_documentary_control_has_failed_terminal_and_preserves_originals(
    monkeypatch, failure_type,
):
    from color_measurement import serial_frontend

    model = "CR-100"
    instrument, port = meter(model, startup(model) + settings_script(model))
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, cr_report())
    operation = frontend.prepare(json.dumps(direct_request(frontend, workflow="mode_snapshot")))
    original = serial_frontend._json

    def fail(value):
        if value is instrument.exchanges:
            raise failure_type("Final documentary control")
        return original(value)

    monkeypatch.setattr(serial_frontend, "_json", fail)
    operation.dispatch()
    result, events = drain(operation)
    assert result.status == "failed" and failure_type.__name__ in result.error
    assert events[-1]["kind"] == "terminal" and events[-1]["payload"]["status"] == "failed"
    assert result.software_stopped and not instrument._ownership.locked()
    assert json.loads(dict(result.configuration)["direct_exchanges_json"]) == instrument.exchanges
    assert frontend.close() and not port.script


@pytest.mark.parametrize("stage", ["configuration", "raw"])
def test_direct_completed_capture_survives_a_post_read_documentary_hook_failure(monkeypatch, stage):
    from color_measurement import serial_frontend

    model = "CR-100"
    instrument, port = meter(model, startup(model) + settings_script(model) * 2
                             + acquisition_script(model) + shutdown(model))
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, instrument.read_settings())
    operation = frontend.prepare(json.dumps(
        direct_request(frontend, options=dict(automatic=False)),
    ))
    original = serial_frontend._json
    injected = []

    def fail(value):
        match = value is instrument.settings_receipt if stage == "configuration" else \
            isinstance(value, dict) and "identity_exchanges" in value and "exchanges" in value
        if match and not injected:
            injected.append(True)
            raise ValueError("Post-read documentary hook failed")
        return original(value)

    monkeypatch.setattr(serial_frontend, "_json", fail)
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and "Post-read documentary hook failed" in result.error
    assert len(result.patches) == 1
    observation = result.patches[0].observation
    assert observation.backend_reported.xyz.values == (1, 2, 3)
    raw = json.loads(observation.raw_backend_output)
    xyz = next(row for row in raw["exchanges"] if row["requested_command"] == "RM XYZ")
    assert b"1,2,3" in bytes.fromhex(xyz["channel"]["received_hex"])
    assert frontend.close() and not port.script


@pytest.mark.parametrize("failure_type", [ValueError, KeyboardInterrupt, SystemExit])
def test_direct_channel_construction_failure_closes_owned_transport_and_preserves_control(
    monkeypatch, failure_type,
):
    from color_measurement import serial_instruments

    instrument, port = meter("CR-100", [])
    failure = failure_type("Channel initialization failed")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(serial_instruments, "SerialChannel", fail)
    with pytest.raises(failure_type) as caught:
        instrument.connect()
    assert caught.value is failure
    assert not port.is_open and instrument.cleanup["port_closed"] is True
    assert instrument.channel is None and not instrument._ownership.locked()


def test_direct_close_refuses_active_worker_without_releasing_its_io_lease():
    model = "CR-100"
    instrument, port = meter(model, startup(model))
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, cr_report())
    operation = frontend.prepare(json.dumps(direct_request(frontend)))
    operation.dispatch()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        raw = operation.poll_event_json()
        if raw and json.loads(raw)["kind"] == "instruction_required":
            break
        time.sleep(0.001)
    else:
        raise AssertionError("Placement prompt not reached")
    with pytest.raises(OperationBusyError):
        frontend.close()
    assert instrument._ownership.locked() and port.is_open
    operation.cancel(operation.operation_id)
    assert drain(operation)[0].status == "cancelled"
    assert frontend.close() and not port.is_open


@pytest.mark.parametrize("command,reply,key", [
    ("RS Aperture", "ER:-300:RS Aperture:invalid operation", "aperture"),
    ("RS Aperture", "OK:0:RS Aperture:", "aperture"),
    ("RS Exposure", "ER:-300:RS Exposure:invalid operation", "exposureMs"),
    ("RS Exposure", "OK:0:RS Exposure:unparsed", "exposureMs"),
    ("RC MinExposure", "ER:-300:RC MinExposure:invalid operation", "exposureMs"),
])
def test_cr_optional_setting_errors_keep_framing_and_disable_unavailable_targets(
    command, reply, key,
):
    model = "CR-100"
    settings = cr_settings_script(model, cr_report(model)["values"])
    index = next(i for i, (sent, _) in enumerate(settings) if sent == (command + "\r\n").encode())
    settings[index] = exchange(command, reply)
    if command == "RC MinExposure":
        settings = [item for item in settings if item[0] != b"RS Exposure\r\n"]
    instrument, port = meter(model, startup(model) + settings + acquisition_script(model)
                             + shutdown(model))
    instrument.connect()
    current = instrument.read_settings()
    assert current["values"].get(key) is None
    assert current["unavailable_metadata"][command]["exchange"]["channel"]["received_hex"] == \
        (reply + "\r\n").encode().hex()
    if key != "aperture":
        assert current["controls"][key]["available"] is False
    assert instrument.channel.synchronized
    native, _ = instrument._acquire()
    assert native.xyz.values == (1, 2, 3)
    assert instrument.close() and not port.script


def test_cs_cancel_before_measurement_write_preserves_normal_cleanup():
    model = "CS-2000"
    instrument, port = meter(model, startup(model) + shutdown(model))
    instrument.connect()
    cancelled = Event()
    cancelled.set()
    instrument.channel.cancel = cancelled
    with pytest.raises(SerialCancelled):
        instrument._acquire()
    assert not instrument.measurement_active and instrument.channel.synchronized
    assert instrument.exchanges[-1]["channel"]["sent_hex"] == ""
    instrument.channel.cancel = None
    assert instrument.close() and instrument.cleanup["backlight_restored"]
    assert instrument.cleanup["remote_released"] and not port.script


def test_direct_descriptor_stays_stable_during_exposure_and_owned_overlap_is_refused():
    instrument, port = meter("CS-2000", startup("CS-2000") + shutdown("CS-2000"))
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, dict(values={}, speedLayout="modern"))
    original = frontend.describe_json()
    instrument._ownership.acquire()
    instrument.measurement_active = True
    try:
        assert frontend.describe_json() == original
        with pytest.raises(OperationBusyError):
            instrument.read_settings()
    finally:
        instrument.measurement_active = False
        instrument._ownership.release()
    assert frontend.close() and not port.script


def test_cr_complete_unparsed_inventory_is_optional_but_unknown_body_count_is_fatal():
    model = "CR-100"
    settings = cr_settings_script(model, cr_report(model)["values"])
    index = next(i for i, (sent, _) in enumerate(settings) if sent == b"RC Mode\r\n")
    settings[index] = exchange("RC Mode", "OK:0:RC Mode:1\r\nmalformed")
    settings = [item for item in settings if item[0] != b"RS Mode\r\n"]
    instrument, port = meter(model, startup(model) + settings + shutdown(model))
    instrument.connect()
    report = instrument.read_settings()
    assert report["controls"]["mode"]["choices"] == []
    assert report["unavailable_metadata"]["RC Mode"]["code"] is None
    assert instrument.channel.synchronized and instrument.close() and not port.script
    instrument, port = meter(model, startup(model) + settings[:index]
                             + [exchange("RC Mode", "OK:0:RC Mode:unknown")])
    instrument.connect()
    with pytest.raises(SerialProtocolError, match="declared integer"):
        instrument.read_settings()
    assert not instrument.channel.synchronized
    assert instrument.close() and not port.script


def test_family_response_defaults_and_explicit_override_match_inert_catalog_floor():
    rows = {row["model_enum"]: row for row in direct_serial_catalog()["models"]}
    for model in MODELS:
        instrument = DirectSerialInstrument(model, "unopened-selected-port")
        assert instrument.response_timeout == (10 if model.startswith("CS-") else 5)
        policy = rows[model]["binding_policy"]
        assert policy["response_timeout_default_seconds"] == instrument.response_timeout
        assert policy["measurement_floor_seconds"] == (287 if model.startswith("CS-") else None)
        assert instrument.channel is None
    overridden = DirectSerialInstrument("CS-2000", "unopened-selected-port", response_timeout=5,
                                        measurement_timeout=282)
    assert overridden.response_timeout == 5 and overridden.channel is None
    row = next(row for row in direct_serial_catalog(response_timeout=5)["models"]
               if row["model_enum"] == "CS-2000")
    assert row["binding_policy"]["measurement_floor_seconds"] == 282


def _cs_meter(script, owed, *, model="CS-2000", expected_serial=None):
    port = ScriptedPort(script)
    instrument = DirectSerialInstrument(
        model, "software-selected-port", transport=port, clock=Clock(),
        unrestored_backlight=owed, expected_serial=expected_serial,
    )
    return instrument, port


def test_cs_uncertain_stop_backlight_is_restored_by_the_next_connection():
    model, owed = "CS-2000", {}
    failed = [exchange("MEAS,1", "ER02")]
    instrument, port = _cs_meter(startup(model) + settings_script(model) * 2 + failed, owed)
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, instrument.read_settings())
    operation = frontend.prepare(json.dumps(
        direct_request(frontend, options=dict(automatic=False)),
    ))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and instrument.measurement_active and not port.is_open
    assert not any(command.startswith(b"BALS,1,1") for command in port.commands)
    assert owed == {(model, "1234567"): (1, 1)}
    script = startup(model)
    script[2] = exchange("BALR", "OK00,1,0")
    del script[3]
    following, port = _cs_meter(script + shutdown(model), owed)
    following.connect()
    assert not any(command.startswith(b"BALS,") for command in port.commands)
    assert owed == {(model, "1234567"): (1, 1)}
    assert following.close() and not port.script
    assert port.commands[-2:] == [b"BALS,1,1\r\n", b"RMTS,0\r\n"]
    assert following.cleanup["backlight_restored"] is True and owed == {}


def test_cs_owed_backlight_waits_for_its_own_model_and_yields_to_manual_restoration():
    model = "CS-2000"
    owed = {("CS-2000A", "1234567"): (0, 1), (model, "7654321"): (1, 1)}
    original = dict(owed)
    instrument, port = _cs_meter(startup(model) + shutdown(model), owed)
    instrument.connect()
    assert instrument.close() and not port.script and owed == original
    owed[(model, "1234567")] = (1, 1)
    instrument, port = _cs_meter(startup(model) + shutdown(model), owed)
    instrument.connect()
    assert instrument.close() and not port.script
    assert port.commands.count(b"BALS,1,0\r\n") == 1 and owed == original


@pytest.mark.parametrize("command", ["BALS,1,0", "MSWE,0"])
def test_cs_uncertain_startup_retains_original_debt_and_closes_host_before_identity_exists(command):
    model, owed = "CS-2000", {}
    script = startup(model)
    index = next(i for i, (sent, _) in enumerate(script) if sent == (command + "\r\n").encode())
    script[index] = exchange(command, "unframed")
    instrument, port = _cs_meter(script[:index + 1], owed)
    with pytest.raises(SerialProtocolError):
        instrument.connect()
    assert instrument.identity is None and instrument._cs_serial == "1234567"
    assert instrument.cleanup["port_closed"] is True and not port.is_open and not port.script
    assert owed == {(model, "1234567"): (1, 1)}
    assert not any(sent == b"BALS,1,1\r\n" for sent in port.commands)


def test_cs_repeated_failed_recovery_keeps_debt_and_current_first_flag_until_confirmed_restore():
    model = "CS-2000"
    owed = {(model, "1234567"): (1, 1)}
    script = startup(model)
    script[2] = exchange("BALR", "OK00,0,0")
    del script[3]
    for _ in range(2):
        failed = script[:-1] + [exchange("MSWE,0", "unframed")]
        instrument, port = _cs_meter(failed, owed)
        with pytest.raises(SerialProtocolError):
            instrument.connect()
        assert not port.is_open and instrument.cleanup["port_closed"] is True
        assert owed[(model, "1234567")][1] == 1
    cleanup = [exchange("BALS,0,1", "OK00"), exchange("RMTS,0", "OK00")]
    instrument, port = _cs_meter(script + cleanup, owed)
    instrument.connect()
    assert owed and instrument._backlight_before == (0, 1)
    assert instrument.close() and not port.script and owed == {}


def test_cs_serial_assertion_precedes_recovery_queries_and_keeps_other_instrument_debt():
    model = "CS-2000"
    owed = {(model, "1234567"): (1, 1)}
    script = startup(model)[:2] + [exchange("RMTS,0", "OK00")]
    instrument, port = _cs_meter(script, owed, expected_serial="7654321")
    with pytest.raises(SerialProtocolError, match="different serial"):
        instrument.connect()
    assert not port.is_open and not port.script
    assert owed == {(model, "1234567"): (1, 1)}
    assert not any(sent.startswith((b"BALR", b"BALS", b"MSWE")) for sent in port.commands)


@pytest.mark.parametrize("ledger", [
    [], {"1234567": (1, 1)}, {("CS-2000", "1234567"): (1, "0\r\nMSWE,1")},
    {("CS-2000", "1234567"): (1, 2)}, {("CS-2000", "1234567"): (True, 1)},
    {("CS-2000", "1234567"): (1, float("nan"))}, {("CS-2000", "1234567"): [1, 1]},
    {("CR-250", "1234567"): (1, 1)}, {("CS-2000", "short"): (1, 1)},
])
def test_cs_malformed_recovery_input_is_refused_before_any_open_or_command(ledger):
    port = ScriptedPort([])
    with pytest.raises(ValueError, match="backlight ledger"):
        DirectSerialInstrument("CS-2000", "unopened-selected-port", transport=port,
                               clock=Clock(), unrestored_backlight=ledger)
    assert not port.commands


def test_cs_mutated_recovery_ledger_is_rechecked_before_connect():
    ledger = {}
    instrument, port = _cs_meter(startup("CS-2000"), ledger)
    ledger[("CS-2000", "1234567")] = (1, 100)
    with pytest.raises(ValueError, match="backlight ledger"):
        instrument.connect()
    assert instrument.channel is None and not port.commands


def test_initialization_cancel_refuses_before_opening_a_physical_selected_port(monkeypatch):
    from color_measurement import serial_instruments

    cancelled = Event()
    cancelled.set()
    monkeypatch.setattr(serial_instruments, "open_serial_port",
                        lambda *args, **kwargs: pytest.fail("Cancelled setup opened a port"))
    instrument = DirectSerialInstrument("CS-2000", "must-not-open", initialization_cancel=cancelled)
    with pytest.raises(SerialCancelled, match="before opening"):
        instrument.connect()
    assert instrument.channel is None and instrument.cleanup["port_closed"]


def test_cs_cancel_before_the_first_remote_write_sends_no_remote_release():
    class CancelAfterOpen:
        checks = 0

        def is_set(self):
            # Clear for the pre-open check, then set before RMTS,1 can be written.
            self.checks += 1
            return self.checks > 1

    port = ScriptedPort([])
    instrument = DirectSerialInstrument(
        "CS-2000", "software-selected-port", transport=port, clock=Clock(),
        initialization_cancel=CancelAfterOpen(),
    )
    with pytest.raises(SerialCancelled, match="instrument state is unknown"):
        instrument.connect()
    # RMTS,1 was never sent, so close owes no RMTS,0 to a remote mode never entered.
    assert bytes(port.command) == b"" and port.commands == []
    assert instrument._remote_requested is False
    assert instrument.cleanup["remote_released"] is None and instrument.cleanup["errors"] == []
    assert instrument.cleanup["port_closed"] is True and not port.is_open


def test_initialization_cancel_after_completed_cs_effects_preserves_debt_and_closes_host():
    model, cancelled, ledger = "CS-2000", Event(), {}
    script = startup(model)
    script[-1] = exchange("MSWE,0", "")
    port = ScriptedPort(script)
    write = port.write

    def cancel_after_mswe(data):
        count = write(data)
        if port.commands and port.commands[-1] == b"MSWE,0\r\n":
            cancelled.set()
        return count

    port.write = cancel_after_mswe
    instrument = DirectSerialInstrument(
        model, "software-selected-port", transport=port, clock=Clock(),
        unrestored_backlight=ledger, initialization_cancel=cancelled,
    )
    with pytest.raises(SerialCancelled):
        instrument.connect()
    assert not port.is_open and instrument.cleanup["port_closed"]
    assert ledger == {(model, "1234567"): (1, 1)}
    assert instrument.channel.cancel is None and instrument._initialization_cancel is None
    assert not any(command == b"BALS,1,1\r\n" for command in port.commands)


def test_initialization_readback_uses_same_event_then_detaches_before_normal_cleanup():
    model, cancelled = "CS-2000", Event()
    instrument, port = meter(model, startup(model) + shutdown(model))
    instrument._initialization_cancel = cancelled
    instrument.connect()
    cancelled.set()
    with pytest.raises(SerialCancelled):
        instrument.read_settings()
    assert instrument.channel.synchronized and instrument.channel.cancel is None
    assert instrument._initialization_cancel is None
    assert b"SPMR\r\n" not in port.commands
    assert instrument.close() and instrument.cleanup["backlight_restored"]
    assert instrument.cleanup["remote_released"] and not port.script


def test_cr_optional_metadata_cannot_swallow_initialization_cancellation():
    model, cancelled = "CR-100", Event()
    instrument, port = meter(model, startup(model))
    instrument._initialization_cancel = cancelled
    instrument.connect()
    cancelled.set()
    with pytest.raises(SerialCancelled):
        instrument.read_settings()
    assert instrument.channel.synchronized and instrument.channel.cancel is None
    assert instrument._initialization_cancel is None
    assert len(port.commands) == len(startup(model))
    assert instrument.close() and not port.script


def test_direct_catalog_discloses_the_serial_format_the_opener_uses(monkeypatch):
    from color_measurement import serial_instruments

    catalog = serial_instruments.direct_serial_catalog()["models"]
    assert len(catalog) == len(MODELS)
    for row in catalog:
        policy = row["binding_policy"]
        assert {key: policy[key] for key in ("baudrate", "data_bits", "parity", "stop_bits")} == \
            dict(baudrate=115200, data_bits=8, parity="none", stop_bits=1)
    opened = []

    def recording_open(port, **kwargs):
        opened.append(kwargs)
        raise RuntimeError("recording opener: no port is opened")

    monkeypatch.setattr(serial_instruments, "open_serial_port", recording_open)
    instrument = DirectSerialInstrument("CR-100", "software-selected-port", clock=Clock())
    with pytest.raises(Exception, match="recording opener"):
        instrument.connect()
    assert opened and opened[0]["baudrate"] == 115200


def test_terminal_error_with_surplus_records_the_surplus_failure():
    model = "CS-2000"
    acquisition = acquisition_script(model)
    at = next(index for index, (sent, _) in enumerate(acquisition)
              if sent == exchange("MEAS,1", "")[0])
    failed = acquisition[:at] + [exchange("MEAS,1", b"ER10\r\nlate")]
    instrument, port = meter(model, startup(model) + settings_script(model) * 2 + failed)
    instrument.connect()
    frontend = DirectSerialFrontend(instrument, instrument.read_settings())
    operation = frontend.prepare(json.dumps(
        direct_request(frontend, options=dict(automatic=False)),
    ))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed"
    measured = next(entry for entry in reversed(instrument.exchanges)
                    if entry["requested_command"] == "MEAS,1")
    assert "surplus" in measured["error"], measured["error"]


def test_cs_backlight_ledger_validation_tolerates_a_debt_recorded_by_another_worker(monkeypatch):
    import re

    from color_measurement import serial_instruments

    # The desktop setup shares one ledger. Another CS source's operation worker records
    # its debt at terminal close while this connection validates the ledger.
    model, other = "CS-2000", ("CS-2000A", "7654321")
    owed = {(model, "7654320"): (1, 1)}

    class AnotherWorkerRecordsMidValidation:
        def __getattr__(self, name):
            return getattr(re, name)

        def fullmatch(self, pattern, text, *args):
            owed.setdefault(other, (1, 1))
            return re.fullmatch(pattern, text, *args)

    monkeypatch.setattr(serial_instruments, "re", AnotherWorkerRecordsMidValidation())
    instrument, port = _cs_meter(startup(model) + shutdown(model), owed)
    instrument.connect()
    assert instrument.close() and not port.script
    assert owed == {(model, "7654320"): (1, 1), other: (1, 1)}
