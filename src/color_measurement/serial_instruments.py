"""Direct CR/CS transactions over one shared owned serial channel.

The native protocol and policy ports are attributed in docs/direct-serial.md.
Construction is inert. Only explicit connect opens the selected path. This owner
does not discover equipment, supervise a frontend, compute XYZ or publish files.
"""

from __future__ import annotations

import re
import threading
import time
from copy import deepcopy
from uuid import uuid4

from .decimal_numbers import decimal_integer
from .models import XYZ, DeviceIdentity, ReportedValues
from .operations import OperationBusyError
from .serial_protocols import (
    SerialInstrumentError,
    cr_capture_value,
    cr_grid,
    cr_reply,
    cr_spectrum,
    cs_conditions,
    cs_duration,
    cs_identity,
    cs_reply,
    cs_spectrum,
    numbers,
)
from .serial_settings import (
    cr_setting_limits,
    cr_setting_list,
    cr_setting_value,
    cr_settings_plan,
    cr_settings_specs,
    cs_settings_plan,
    cs_speed_values,
    cs_sync_values,
)
from .serial_transport import (
    SerialCancelled,
    SerialChannel,
    SerialProtocolError,
    close_owned_transport,
    open_serial_port,
    positive_seconds,
)

MODELS = ("CR-100", "CR-250", "CR-300", "CS-2000", "CS-2000A")
# Every current direct route uses one fixed serial format; the catalog discloses it
# and the port opener uses it, so the two cannot drift apart.
SERIAL_FORMAT = dict(baudrate=115200, data_bits=8, parity="none", stop_bits=1)

# Remote Communication 1.36 sections 4.3.1.3-20, .34, .41; last-capture RM,
# independently of current RS settings. Attributed MATLAB metadataQueries owner.
_CR_CAPTURE_FIELDS = (
    ("deviceTime", "Time", "text", "", "always"),
    ("accessory", "Accessory", "text", "", "always"),
    ("filters", "Filter", "text", "", "always"),
    ("aperture", "Aperture", "text", "", "always"),
    ("instrumentMode", "Mode", "text", "", "always"),
    ("exposureMode", "ExposureMode", "text", "", "always"),
    ("exposureMs", "Exposure", "milliseconds", "ms", "always"),
    ("maxAutoExposureMs", "MaxAutoExposure", "milliseconds", "ms", "always"),
    ("rangeMode", "RangeMode", "text", "", "always"),
    ("range", "Range", "text", "", "always"),
    ("syncMode", "SyncMode", "syncMode", "", "always"),
    ("syncFrequencyHz", "SyncFreq", "hertz", "Hz", "sync"),
    ("exposureMultiplier", "ExposureX", "positiveInteger", "", "always"),
    ("userCalibrationMode", "UserCalibMode", "calibrationMode", "", "always"),
    ("legacyMatrixMode", "MatrixMode", "legacyMatrixMode", "", "legacy"),
    ("matrixId", "Matrix", "text", "", "matrix"),
    ("matchId", "Match", "text", "", "match"),
    ("speed", "Speed", "text", "", "spectral"),
    ("warnings", "Warnings", "text", "", "always"),
    ("cmfIndex", "CMF", "integer", "", "spectral"),
)


def direct_serial_catalog(*, response_timeout=None):
    """Source-backed setup policy, without enumeration, port access or native calls.

    CS budgets its initial 35-second reply and maximum 242-second announced
    exposure separately. Other families have no established worst-case floor;
    their default host allowance is a policy, not a physical completion promise.
    """
    response = positive_seconds(response_timeout) if response_timeout is not None else None
    if response is not None and response > 600:
        raise ValueError("Direct response timeout must not exceed 600 seconds")
    rows = []
    for model in MODELS:
        family = "cr" if model.startswith("CR-") else "cs"
        default_response = 10 if family == "cs" else 5
        selected_response = default_response if response is None else response
        effects = {
            "cr": ["Read identity through the selected serial port"],
            "cs": ["Enter remote mode", "Read identity and backlight state",
                   "Turn backlight off", "Disable instrument measurement switch"],
        }[family]
        floor = max(35, selected_response) + 242 + selected_response if family == "cs" else None
        rows.append(dict(
            model_enum=model, display_label=model, backend="direct_serial",
            initialization_assessed=True, initialization_refusal=None,
            initialization_effects=effects, opening_requires="accept_disclosed_initialization",
            serial_assertion=dict(available=True, continuity_requires_reported_serial=True),
            binding_policy=dict(
                **SERIAL_FORMAT,
                response_timeout_default_seconds=default_response,
                measurement_timeout_default_seconds=300,
                maximum_timeout_seconds=600, measurement_floor_seconds=floor,
                measurement_floor_basis="CS initial reply plus maximum announced exposure"
                if family == "cs" else "No established worst-case exposure bound",
            ),
            physical_validation="unverified",
        ))
    return dict(contract="direct_serial_setup_v1", models=rows)


class DirectSerialInstrument:
    """One explicitly selected connection; all models share its I/O and ownership.

    Injected transports and clocks exercise the real transaction path in software.
    They carry simulated provenance; an OS port requires separate hardware scope.
    A timeout or malformed reply forbids another command until a new connection.
    """

    def __init__(
        self,
        model,
        port,
        *,
        response_timeout=None,
        measurement_timeout=300,
        expected_serial=None,
        transport=None,
        clock=time,
        unrestored_backlight=None,
        initialization_cancel=None,
    ):
        if (
            not isinstance(model, str)
            or model not in MODELS
            or not isinstance(port, str)
            or not port.strip()
            or "\x00" in port
        ):
            raise ValueError("Choose a supported direct model and one explicit serial port")
        default_response = 10 if model.startswith("CS-") else 5
        self.response_timeout = positive_seconds(
            default_response if response_timeout is None else response_timeout)
        self.measurement_timeout = positive_seconds(measurement_timeout)
        if max(self.response_timeout, self.measurement_timeout) > 600:
            raise ValueError("Direct timeouts must not exceed 600 seconds")
        policy = next(row["binding_policy"] for row in direct_serial_catalog(
            response_timeout=self.response_timeout,
        )["models"] if row["model_enum"] == model)
        floor = policy["measurement_floor_seconds"]
        if floor is not None and self.measurement_timeout < floor:
            raise ValueError(f"Direct measurement timeout requires at least {floor:g} seconds")
        if expected_serial is not None and (
            not isinstance(expected_serial, str) or not expected_serial or "\x00" in expected_serial
        ):
            raise ValueError("Expected serial must be nonempty text or null")
        self.model, self.port, self.expected_serial = model, port, expected_serial
        self.family = "cr" if model.startswith("CR-") else "cs"
        self.clock, self._transport = clock, transport
        self._owned_transport = None
        self.provenance = "simulated" if transport is not None else "physical"
        self._ownership = threading.Lock()
        self.channel = None
        self.identity = None
        self.identity_details = {}
        self.connection_exchanges = []
        self.exchanges = []
        self.settings_receipt = None
        self.last_settings = None
        self._settings_uncertain = set()
        self.measurement_active = False
        self._retained_capture = None
        self._remote_requested = False
        self._backlight_before = None
        self._backlight_changed = False
        # One caller-owned session ledger survives replacement of a host connection.
        # The model and reported serial bind each original to the same CS instrument.
        self.unrestored_backlight = {} if unrestored_backlight is None else unrestored_backlight
        self._validate_backlight_ledger()
        self._cs_serial = None
        self._validate_cancel_event(initialization_cancel)
        self._initialization_cancel = initialization_cancel
        self.cleanup = dict(port_closed=True, remote_released=None, backlight_restored=None)

    def _validate_backlight_ledger(self):
        ledger = self.unrestored_backlight
        # Operation workers record debts at terminal close on their own threads;
        # validate one copy, which the interpreter takes atomically.
        if type(ledger) is not dict or any(
            type(key) is not tuple or len(key) != 2
            or type(key[0]) is not str or key[0] not in {"CS-2000", "CS-2000A"}
            or type(key[1]) is not str or re.fullmatch(r"[0-9]{7}", key[1]) is None
            or type(value) is not tuple or len(value) != 2
            or any(type(flag) is not int or flag not in (0, 1) for flag in value)
            for key, value in ledger.copy().items()
        ):
            raise ValueError(
                "CS backlight ledger requires model/serial keys and two integer 0/1 flags",
            )

    @staticmethod
    def _validate_cancel_event(cancel):
        if cancel is not None and not callable(getattr(cancel, "is_set", None)):
            raise ValueError("Direct cancellation requires a caller-owned event")

    def connect(self):
        """Explicit native operation: open, identify and retain this model's startup effects."""
        cancel = self._initialization_cancel
        if not self._ownership.acquire(blocking=False):
            raise OperationBusyError("Direct connection is owned by an active operation")
        try:
            if self.channel is not None or self._owned_transport is not None:
                raise ValueError("Construct a new direct connection before reconnecting")
            self._validate_backlight_ledger()
            if cancel is not None and cancel.is_set():
                raise SerialCancelled(
                    "Direct initialization cancelled before opening the selected port",
                )
            transport = self._transport
            try:
                if transport is None:
                    transport = open_serial_port(
                        self.port, baudrate=SERIAL_FORMAT["baudrate"],
                        write_timeout=self.response_timeout,
                    )
                # Retain the acquired host resource before fallible channel setup.
                self._owned_transport = transport
                self.cleanup["port_closed"] = False
                self.channel = SerialChannel(
                    transport, clock=self.clock, cancel=cancel,
                )
                details = self._identify()
                serial = details.get("serial")
                if self.expected_serial is not None and serial != self.expected_serial:
                    raise SerialProtocolError("Direct connection reported a different serial")
                self.identity_details = dict(details, port=self.port)
                self.identity = DeviceIdentity(
                    "direct:" + str(uuid4()),
                    self.model,
                    "direct_serial",
                    "1",
                    serial=serial,
                    serial_encoding="utf8",
                )
                self.connection_exchanges = deepcopy(self.exchanges)
                return self.identity
            except BaseException as error:
                if transport is not None and self.channel is None:
                    self._owned_transport = transport
                    self.cleanup["port_closed"] = False
                if self.channel is not None:
                    # The cancelled initialization's cleanup owns a fresh bounded
                    # wait; it may restore only a synchronized, idle connection.
                    self.channel.cancel = None
                try:
                    self._close()
                except BaseException as cleanup_error:
                    error.add_note(f"Direct connection cleanup failed: {cleanup_error}")
                self._initialization_cancel = None
                raise
        finally:
            if self.channel is not None:
                self.channel.cancel = None
            self._ownership.release()

    def _exchange(self, command, reader, *, timeout=None):
        channel = self.channel
        if channel is None:
            raise SerialProtocolError("Direct connection is not open")
        retained = sum(
            len(item["channel"]["received_hex"]) + len(item["channel"]["sent_hex"])
            for item in self.exchanges
        ) // 2
        remaining = 4 * 1024 * 1024 - retained - len(command.encode("ascii")) - 2
        if len(self.exchanges) >= 256 or remaining < 1:
            raise SerialProtocolError("Direct transaction exceeded its exchange bound")
        budget = self.response_timeout if timeout is None else positive_seconds(timeout)
        original_limit = channel.limit_bytes
        channel.limit_bytes = min(original_limit, remaining)
        started, error = self.clock.monotonic(), None
        try:
            channel.begin(
                command,
                timeout=min(self.response_timeout, budget),
            )
            # As in the original owners, response allowance starts after the
            # completed write; each write itself has its separate finite bound.
            result = reader(self.clock.monotonic() + budget)
            channel.complete()
            return result
        except SerialInstrumentError as failure:
            error = str(failure)
            uncertain = self.family == "cs" and (
                failure.code in {"ER00", "ER02"}
                or failure.code == "ER17" and command == "MEAS,1"
            )
            if uncertain:
                self.measurement_active = True
            try:
                channel.complete()  # A complete framed error is not a late reply.
            except BaseException as surplus:
                # The retained exchange names the failure actually raised.
                error = str(surplus)
                raise
            if not uncertain and (self.family, command) in {
                ("cr", "M"), ("cs", "MEAS,1"),
            }:
                self.measurement_active = False
            raise
        except SerialCancelled as failure:
            error = str(failure)
            if channel.synchronized and not channel.facts()["sent_hex"]:
                if (self.family, command) in {("cr", "M"), ("cs", "MEAS,1")}:
                    self.measurement_active = False
                if self.family == "cs" and command == "RMTS,1":
                    self._remote_requested = False
            else:
                channel.quarantine()
            raise
        except BaseException as failure:
            error = str(failure)
            channel.quarantine()
            raise
        finally:
            channel.limit_bytes = original_limit
            self.exchanges.append(
                dict(
                    requested_command=command,
                    channel=channel.facts(),
                    started_monotonic=started,
                    finished_monotonic=self.clock.monotonic(),
                    error=error,
                )
            )

    def _query(self, command, *, parser=lambda value: value, timeout=None, acknowledgement=False):
        def read(deadline):
            line = self.channel.line(deadline)
            if self.family == "cr":
                label = command.split()[1] if command.startswith("SM ") else command
                if command.startswith("SM Mode "):
                    label = "SM Mode"
                payload, _ = cr_reply(line, command, expected_label=label)
            else:
                payload = cs_reply(line, command, acknowledgement=acknowledgement)
            return parser(payload)

        return self._exchange(command, read, timeout=timeout)

    def _identify(self):
        if self.family == "cr":
            if self._query("RC Model") != self.model:
                raise SerialProtocolError("Selected CR port reported a different model")
            serial, firmware = self._query("RC ID"), self._query("RC Firmware")
            if not serial.strip() or not firmware.strip():
                raise SerialProtocolError("CR identity reports must contain nonempty values")
            return dict(
                model=self.model, serial=serial, firmware=firmware, protocol="CR Remote 1.36"
            )
        self._remote_requested = True
        self._query("RMTS,1", acknowledgement=True)
        identity = self._query("IDDR", parser=lambda payload: cs_identity(payload, self.model))
        self._cs_serial = identity["serial"]
        if self.expected_serial is not None and self._cs_serial != self.expected_serial:
            raise SerialProtocolError("Direct connection reported a different serial")
        before = self._query("BALR", parser=lambda payload: self._codes(payload, (0, 1), count=2))
        self._backlight_before = before
        self._validate_backlight_ledger()
        key = (self.model, self._cs_serial)
        owed = self.unrestored_backlight.get(key)
        if owed is not None and before[1] == 0 and owed[1] != 0:
            # Keep current idle behavior; restore the earlier measurement flag at
            # cleanup. Do not consume the debt while startup can still fail.
            self._backlight_before = (before[0], owed[1])
            self._backlight_changed = True
        else:
            # BALR confirmed the original flag, or a manual switch-on superseded
            # the old change. Any new off request below creates its own debt.
            self.unrestored_backlight.pop(key, None)
        if before[1] != 0:
            self._backlight_changed = True
            self.unrestored_backlight[key] = self._backlight_before
            self._query(f"BALS,{before[0]},0", acknowledgement=True)
        self._query("MSWE,0", acknowledgement=True)
        return dict(identity, firmware=None, protocol="CS Rev.7")

    @staticmethod
    def _codes(payload, allowed, *, count=1):
        numbers(payload, count)
        values = tuple(decimal_integer(field.strip()) for field in payload.split(","))
        if any(value not in allowed for value in values):
            raise SerialProtocolError("Direct reply contains an undocumented integer code")
        return values if count != 1 else values[0]

    def read_settings(self):
        cancel = self._initialization_cancel
        if not self._ownership.acquire(blocking=False):
            raise OperationBusyError("Direct connection is owned by an active operation")
        try:
            if self.channel is not None:
                self.channel.cancel = cancel
            self.exchanges = []
            return deepcopy(self._read_settings())
        finally:
            if self.channel is not None:
                self.channel.cancel = None
            self._initialization_cancel = None
            self._ownership.release()

    def _read_settings(self):
        if self.identity is None:
            raise ValueError("Identify the selected direct connection first")
        report = dict(values={}, identity=deepcopy(self.identity_details))
        self.last_settings = report  # Partial completed observations survive a later error.
        if self.family == "cs":
            speed = self._query(
                "SPMR",
                parser=lambda value: cs_speed_values(
                    value, variation=self.identity_details["variation"]
                ),
            )
            report["speedLayout"] = speed.pop("speedLayout")
            report["restoreCommand"] = speed.pop("restoreCommand")
            report["values"].update(speed)
            report["values"].update(self._query("SCMR", parser=cs_sync_values))
            for key, command, allowed in (
                ("measurementAngleCode", "STSR", range(3)),
                ("closeupLens", "LNSR", range(2)),
                ("externalNDFilter", "NDFR", range(3)),
                ("userCalibrationChannel", "UCCR", range(11)),
            ):
                report["values"][key] = self._query(
                    command, parser=lambda value: self._codes(value, allowed)
                )
            channel = report["values"]["userCalibrationChannel"]
            report["userCalibration"] = dict(channel=channel, name=None, status="factory-standard")
            if channel:
                try:
                    name = self._query(f"UCPR,2,{channel:02d}", parser=self._cs_calibration_name)
                except SerialInstrumentError as error:
                    if error.code not in {"ER05", "ER17", "ER30", "ER32", "ER34"}:
                        raise
                    report["userCalibration"].update(status="unavailable", error=error.code)
                else:
                    report["userCalibration"].update(status="reported-name", name=name)
        else:
            self._cr_settings(report)
        report["exchanges"] = deepcopy(self.exchanges)
        return report

    def _cr_settings(self, report):
        controls, cache = {}, {}
        report["controls"] = controls
        match = re.match(r"([0-9]+)\.([0-9]+)", self.identity_details["firmware"])
        version = tuple(map(int, match.groups())) if match else None
        for key, spec in cr_settings_specs(self.model).items():
            control = dict(available=False, choices=[], minimum=None, maximum=None)
            controls[key] = control
            if version is None or version < spec["since"]:
                continue
            token, kind = spec["token"], spec["kind"]
            try:
                if kind in {"choice", "index", "filter"}:
                    token = "Filter" if kind == "filter" else token
                    command = "RC " + token
                    if command in report.get("unavailable_metadata", {}):
                        continue
                    if command not in cache:

                        def rows(deadline):
                            payload, _ = cr_reply(self.channel.line(deadline), command)
                            try:
                                count = 0 if payload == "None" else decimal_integer(payload.strip())
                            except ValueError as error:
                                raise SerialProtocolError(
                                    "CR row count requires a declared integer",
                                ) from error
                            if not 0 <= count <= 256:
                                raise SerialProtocolError("CR row count exceeds its declared bound")
                            lines = [self.channel.line(deadline) for _ in range(count)]
                            columns = 3 if token in {"Accessory", "Filter"} else 2
                            return payload, lines, columns

                        payload, lines, columns = self._exchange(command, rows)
                        cache[command] = cr_setting_list(payload, lines, columns=columns)
                    control["choices"] = deepcopy(cache[command])
                    if key == "userCalibrationMode" and self.model != "CR-100":
                        control["choices"] = [
                            item for item in control["choices"] if item["label"] != "Matrix"
                        ]
                    control["available"] = bool(control["choices"]) and key != "mode"
                else:
                    token = "Exposure" if key == "maxAutoExposureMs" else token
                    for prefix in ("Min", "Max"):
                        command = "RC " + prefix + token
                        if command not in cache:
                            # Frame completion owns synchronization. An invalid
                            # optional capability payload cannot quarantine it.
                            try:
                                cache[command] = self._query(command)
                            except SerialInstrumentError as error:
                                self._optional_setting_error(report, command, error)
                                cache[command] = None
                    control.update(cr_setting_limits(
                        key, cache["RC Min" + token], cache["RC Max" + token],
                    ))
            except SerialInstrumentError as error:
                self._optional_setting_error(report, command, error)
            except SerialCancelled:
                raise
            except SerialProtocolError as error:
                if not self.channel.synchronized:
                    raise
                self._optional_setting_error(report, command, error)
            if key == "maxAutoExposureMs" or not (control["available"] or control["choices"]):
                continue
            token = "Filter" if kind == "filter" else spec["token"]
            command = "RS " + token
            if command in report.get("unavailable_metadata", {}):
                control.update(available=False, unavailable_reason="Current setting is unavailable")
                continue
            try:
                if command not in cache:
                    cache[command] = self._query(command)
                report["values"][key] = cr_setting_value(cache[command], key, control)
            except SerialInstrumentError as error:
                self._optional_setting_error(report, command, error)
                control.update(available=False, unavailable_reason=str(error))
            except SerialCancelled:
                raise
            except SerialProtocolError as error:
                if not self.channel.synchronized:
                    raise
                self._optional_setting_error(report, command, error)
                control.update(available=False, unavailable_reason=str(error))
        if version is not None and version >= (1, 4):
            try:
                payload = self._query("RS Aperture")
                report["values"]["aperture"] = cr_setting_value(payload, "aperture", {})
            except SerialInstrumentError as error:
                self._optional_setting_error(report, "RS Aperture", error)
            except SerialCancelled:
                raise
            except SerialProtocolError as error:
                if not self.channel.synchronized:
                    raise
                self._optional_setting_error(report, "RS Aperture", error)

    def _optional_setting_error(self, report, command, error):
        """Retain a framed optional refusal/unparsed payload without inventing a value."""
        report.setdefault("unavailable_metadata", {})[command] = dict(
            error=str(error), code=getattr(error, "code", None),
            exchange=deepcopy(self.exchanges[-1]),
        )

    def _apply_settings(self, changes, *, automatic):
        receipt = dict(
            requested=deepcopy(changes),
            automatic=automatic,
            before=None,
            after=None,
            commands=[],
            acknowledgements=[],
            status="failed",
            error=None,
        )
        self.settings_receipt = receipt
        try:
            receipt["before"] = self._read_settings()
            if self.family == "cr":
                plan = cr_settings_plan(self.model, changes, receipt["before"], automatic=automatic)
            else:
                plan = cs_settings_plan(changes, receipt["before"], automatic=automatic)
            receipt["requested"] = plan["requested"]
            expected = dict(plan["readback_target"] if self.family == "cs" else plan["requested"])
            for item in plan["commands"]:
                if isinstance(item["value"], dict):
                    expected.update(item["value"])
                else:
                    expected[item["id"]] = item["value"]
            receipt["expected_readback"] = deepcopy(expected)
            if not self._settings_uncertain <= expected.keys():
                raise ValueError("Reapply every uncertain direct setting before acquiring")
            for preflight in plan.get("preflight", []):
                name = self._query(preflight["command"], parser=self._cs_calibration_name)
                receipt.setdefault("preflight", []).append(dict(preflight, name=name))
            for item in plan["commands"]:
                self._settings_uncertain.update(expected)
                receipt["commands"].append(deepcopy(item))
                reply = self._query(item["command"], acknowledgement=True)
                receipt["acknowledgements"].append(
                    dict(item, response=reply, readback_verified=False)
                )
            receipt["after"] = self._read_settings() \
                if plan["commands"] else receipt["before"]
            after = receipt["after"]["values"]
            verified = {}
            for key, value in expected.items():
                actual = after.get(key)
                acknowledged_only = self.family == "cr" and key == "maxAutoExposureMs"
                if not acknowledged_only and actual != value:
                    raise SerialProtocolError(f"Direct setting readback differs for {key}")
                verified[key] = not acknowledged_only
            for acknowledgement in receipt["acknowledgements"]:
                keys = (acknowledgement["value"] if isinstance(acknowledgement["value"], dict)
                        else [acknowledgement["id"]])
                acknowledgement["readback_verified"] = all(verified[key] for key in keys)
            receipt["status"] = "applied" if plan["commands"] else "unchanged"
            if plan["requested"]:
                self._settings_uncertain.clear()
            return receipt
        except BaseException as error:
            receipt["error"] = str(error)
            if receipt["before"] is None:
                receipt["before"] = deepcopy(self.last_settings)
            receipt["exchanges"] = deepcopy(self.exchanges)
            raise

    def apply_settings(self, changes, *, automatic=False):
        """Explicit setting application with retained partial receipt and independent readback."""
        if not self._ownership.acquire(blocking=False):
            raise OperationBusyError("Direct connection is owned by an active operation")
        try:
            self.exchanges = []
            return deepcopy(self._apply_settings(changes, automatic=automatic))
        finally:
            self._ownership.release()

    @staticmethod
    def _cs_calibration_name(payload):
        if len(payload) != 10 or any(not 32 <= ord(value) <= 126 for value in payload):
            raise SerialProtocolError("CS correction name requires ten printable native characters")
        return payload.strip()

    def _acquire(self):
        self._retained_capture = None
        if self.family == "cr":
            self.measurement_active = True
            self._query("M", timeout=self.measurement_timeout)
            self.measurement_active = False
            xyz = self._query("RM XYZ", parser=lambda value: numbers(value, 3))
            metadata = dict(xyz_source="instrument RM XYZ; CIE 1931 2 degree")
            spectral = None
            if self.model != "CR-100":
                radiometric = self._query("RM Radiometric")

                def spectrum(deadline):
                    header, _ = cr_reply(self.channel.line(deadline), "RM Spectrum")
                    grid = cr_grid(header)
                    lines = [self.channel.line(deadline) for _ in grid]
                    # Remote Communication 1.36 p.35: spectrum processing needs
                    # 200 ms before another command. complete() then checks any
                    # trailing samples that arrived during this guard.
                    self.clock.sleep(0.2)
                    return cr_spectrum(header, lines, radiometric)

                spectral, radiometry = self._exchange("RM Spectrum", spectrum)
                metadata.update(radiometry)
            native = ReportedValues(
                XYZ(
                    xyz,
                    "unverified instrument XYZ unit",
                    "unverified",
                    "CIE1931_2",
                    "self_luminous",
                ),
                spectral,
            )
            self._retained_capture = (native, metadata)
            self._cr_capture_metadata(metadata)
        else:

            def measurement(deadline):
                initial_deadline = min(
                    deadline, self.clock.monotonic() + max(35, self.response_timeout)
                )
                duration = cs_duration(cs_reply(self.channel.line(initial_deadline), "MEAS,1"))
                completion_deadline = self.clock.monotonic() + duration + self.response_timeout
                if completion_deadline > deadline:
                    raise SerialProtocolError(
                        "CS announced exposure exceeds selected host allowance"
                    )
                cs_reply(self.channel.line(completion_deadline), "MEAS,1", acknowledgement=True)
                return duration

            self.measurement_active = True
            duration = self._exchange("MEAS,1", measurement, timeout=self.measurement_timeout)
            self.measurement_active = False
            blocks = []
            for block, count in enumerate((100, 100, 100, 101), 1):

                def read(payload):
                    numbers(payload, count, cs2000=True)
                    return payload

                blocks.append(self._query(f"MEDR,1,0,{block}", parser=read))
            spectral = cs_spectrum(blocks)
            xyz = self._query("MEDR,2,0,01", parser=lambda value: numbers(value, 3, cs2000=True))
            metadata = self._query("MEDR,0,0,1", parser=cs_conditions)
            metadata.update(
                announced_duration_seconds=duration,
                xyz_source="instrument MEDR,2,0,01; CIE 1931 2 degree",
            )
            native = ReportedValues(
                XYZ(xyz, "cd/m^2", "absolute; Y is luminance", "CIE1931_2", "self_luminous"),
                spectral,
            )
        self.measurement_active = False
        return native, metadata

    def _cr_capture_metadata(self, metadata):
        values, entries = {}, []
        metadata.update(capture_settings=values, capture_responses=entries)
        for field, token, kind, unit, condition in _CR_CAPTURE_FIELDS:
            values[field] = None
            entry = dict(field=field, command="RM " + token, value=None, unit=unit,
                         status="notApplicable", code=None, response=None)
            entries.append(entry)
            calibration = next((row for row in entries
                                if row["field"] == "userCalibrationMode"), None)
            dependencies = {
                "sync": values.get("syncMode"),
                "matrix": values.get("userCalibrationMode") or values.get("legacyMatrixMode"),
                "match": values.get("userCalibrationMode") or values.get("legacyMatrixMode"),
            }
            read = (condition == "always"
                    or condition == "spectral" and self.model != "CR-100"
                    or condition == "sync" and values.get("syncMode") not in {None, "None"}
                    or condition == "legacy" and calibration["status"] == "unsupported"
                    or condition == "matrix" and (values.get("userCalibrationMode") == "Matrix"
                                                  or values.get("legacyMatrixMode") == "Enabled")
                    or condition == "match" and values.get("userCalibrationMode") == "Match")
            if not read:
                if condition in dependencies and dependencies[condition] is None:
                    entry["status"] = "dependencyUnavailable"
                continue
            try:
                command = entry["command"]
                entry["status"] = "incomplete"
                payload, code = self._exchange(
                    command, lambda deadline: cr_reply(self.channel.line(deadline), command),
                )
            except SerialInstrumentError as error:
                entry.update(code=error.code, response=error.raw,
                             status="unsupported" if error.code == -500 else "instrumentError")
                continue
            entry.update(response=payload, code=code)
            try:
                value = cr_capture_value(payload, kind)
            except SerialProtocolError:
                entry["status"] = "unparsed"
                continue
            values[field] = value
            entry.update(value=value, status="unavailable" if value is None else "available")

    def _close(self):
        channel = self.channel
        if channel is None:
            if self._owned_transport is not None and not self.cleanup["port_closed"]:
                self.cleanup["port_closed"] = close_owned_transport(self._owned_transport)
            return self.cleanup["port_closed"]
        if channel.closed:
            return True
        errors = []
        control = None
        if channel.synchronized and not self.measurement_active:
            try:
                if self.family == "cs":
                    if self._backlight_changed:
                        self._query(
                            f"BALS,{self._backlight_before[0]},{self._backlight_before[1]}",
                            acknowledgement=True,
                        )
                        self.cleanup["backlight_restored"] = True
                        self.unrestored_backlight.pop((self.model, self._cs_serial), None)
                    if self._remote_requested:
                        self._query("RMTS,0", acknowledgement=True)
                        self.cleanup["remote_released"] = True
            except BaseException as error:
                errors.append(str(error))
                if not isinstance(error, Exception):
                    control = error
        self.cleanup["errors"] = errors
        if self._backlight_changed and self.cleanup["backlight_restored"] is not True:
            self.unrestored_backlight[(self.model, self._cs_serial)] = self._backlight_before
        try:
            self.cleanup["port_closed"] = channel.close()
        except BaseException as error:
            if control is None:
                raise
            control.add_note(f"Host port close also failed: {error}")
        if control is not None:
            raise control
        return self.cleanup["port_closed"]

    def close(self):
        """Close only an idle owned connection; unknown measurement state forbids cleanup writes."""
        if not self._ownership.acquire(blocking=False):
            raise OperationBusyError("Direct connection is owned by an active operation")
        try:
            return self._close()
        finally:
            self._ownership.release()
