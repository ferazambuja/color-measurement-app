"""One direct frontend over the shared polling, native record and serial owners."""

import json
import threading
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from .frontend import _fallback_json, _json, _load_request, _PolledOperation
from .instrument_frontend import _validate_descriptor_request, capability_identity
from .models import CalibrationContext, ComputedValues, Observation
from .operation_records import PatchResult
from .serial_instruments import DirectSerialInstrument
from .serial_settings import direct_settings_options
from .serial_transport import SerialCancelled, positive_seconds


class DirectSerialFrontend:
    """Cached truthful descriptor; only dispatched operations perform native transactions."""

    def __init__(self, instrument, settings):
        if not isinstance(instrument, DirectSerialInstrument) or instrument.identity is None:
            raise ValueError("Bind one explicitly connected direct instrument")
        self._meter = instrument
        self._identity = instrument.identity
        self._provenance = instrument.provenance
        self._ownership = instrument._ownership
        self._description_lock = threading.RLock()
        self._settings = self._settings_facts(settings)
        self._settings_revision = 0
        self._closed = False
        direct_settings_options(self._identity.model, self._settings)

    @staticmethod
    def _settings_facts(settings):
        return {
            key: deepcopy(settings[key])
            for key in (
                "values",
                "controls",
                "speedLayout",
                "identity",
            )
            if key in settings
        }

    def describe_json(self, device_id=None):
        if device_id is not None and device_id != self._identity.device_id:
            raise ValueError("Unknown direct device")
        with self._description_lock:
            available = (
                not self._closed
                and self._meter.channel is not None
                and not self._meter.channel.closed
                and not self._meter.channel._failed
            )
            options = direct_settings_options(self._identity.model, self._settings)
            workflows = [
                dict(
                    workflow="spot",
                    label="Placed sample",
                    min_patches=1,
                    max_patches=1,
                    available=available,
                    unavailable_reason=None
                    if available
                    else "Direct connection requires explicit reconnect",
                    layout_fields=[],
                ),
                dict(
                    workflow="mode_snapshot",
                    label="Read current instrument settings",
                    min_patches=0,
                    max_patches=0,
                    available=available,
                    unavailable_reason=None
                    if available
                    else "Direct connection requires explicit reconnect",
                    layout_fields=[],
                ),
            ]
            snapshot = dict(
                schema_version=1,
                contract="direct_serial_instrument_v1",
                device_id=self._identity.device_id,
                device=asdict(self._identity),
                provenance=self._provenance,
                synthetic=self._provenance == "simulated",
                live=self._provenance == "physical",
                default_event_contract="retained_patch_v1",
                event_contracts=["retained_patch_v1"],
                options=options,
                settings_revision=self._settings_revision,
                settings=deepcopy(self._settings),
                identity_details=deepcopy(self._meter.identity_details),
                accepts_correction=False,
                modes=[
                    dict(
                        mode="emissive_spot",
                        label="Ordinary direct light measurement",
                        workflow="spot",
                        workflows=workflows,
                        available=available,
                        unavailable_reason=None
                        if available
                        else "Direct connection requires explicit reconnect",
                        expected_quantities=dict(
                            xyz=True,
                            spectrum=self._identity.model != "CR-100",
                        ),
                    )
                ],
                calibration=dict(
                    actions=[],
                    conditions=[],
                    available=False,
                    reason="This direct protocol port selects existing settings/profiles; "
                    "it does not author or perform a calibration",
                ),
                limits=dict(
                    max_event_bytes=16384,
                    max_request_bytes=16384,
                    max_patches=1,
                    binding_timeout_seconds=self._meter.measurement_timeout,
                    native_diagnostics=dict(
                        available=False,
                        unavailable_reason="Direct serial originals use their native record",
                    ),
                ),
            )
            snapshot["capability_id"] = capability_identity(snapshot)
            return _json(snapshot)

    def _validate_request(self, text):
        request = _load_request(text)
        fields = {
            "schema_version",
            "device_id",
            "capability_id",
            "mode",
            "workflow",
            "patch_ids",
            "options",
            "layout",
            "event_contract",
        }
        if not isinstance(request, dict) or request.keys() != fields:
            raise ValueError("Direct request requires the exact current shared envelope")
        if request["device_id"] != self._identity.device_id:
            raise ValueError("Unknown direct device")
        if request["event_contract"] != "retained_patch_v1" or not isinstance(
            request["options"], dict
        ):
            raise ValueError("Direct requests require retained_patch_v1 and an options object")
        snapshot = json.loads(self.describe_json())
        _validate_descriptor_request(request, snapshot)
        offered = {row["id"]: row for row in snapshot["options"]}
        if request["options"].keys() - offered.keys():
            raise ValueError("Unknown direct setting option")
        for key, value in request["options"].items():
            if value is None and key != "automatic":
                continue
            offer = offered[key]
            if not offer["available"]:
                raise ValueError(offer["unavailable_reason"])
            kind = offer["type"]
            if kind == "boolean":
                valid = type(value) is bool
            elif kind == "choice":
                valid = type(value) in (str, int, float) and value in offer["choices"]
            else:
                valid = type(value) in (int, float)
                if valid:
                    try:
                        valid = offer["minimum"] <= value <= offer["maximum"]
                    except (TypeError, OverflowError):
                        valid = False
                if valid and kind == "integer":
                    valid = value == int(value)
            if not valid:
                raise ValueError(f"Invalid value for direct setting {key}")
        if request["workflow"] == "mode_snapshot" and any(
            value is not None and key != "automatic" for key, value in request["options"].items()
        ):
            raise ValueError("Current settings queries do not apply overrides")
        return request, snapshot

    def prepare(
        self,
        request_json,
        *,
        response_timeout=120,
        timeout=None,
        native_diagnostic_target=None,
        native_debug_level=0,
        native_diagnostic_limit_bytes=65536,
    ):
        if native_debug_level != 0 or native_diagnostic_target is not None:
            raise ValueError("Native helper diagnostics do not apply to direct serial instruments")
        return DirectSerialOperation(
            self, request_json, response_timeout=response_timeout, timeout=timeout
        )

    def close(self):
        closed = self._meter.close()
        if closed:
            with self._description_lock:
                self._closed = True
        return closed


class DirectSerialOperation(_PolledOperation):
    """Shared operation lifetime; this producer owns only direct transactions and facts."""

    def __init__(self, frontend, request_json, *, response_timeout, timeout):
        request, self._snapshot = frontend._validate_request(request_json)
        response_timeout = positive_seconds(response_timeout)
        if response_timeout > 600:
            raise ValueError("Operator response timeout must not exceed 600 seconds")
        selected = frontend._meter.measurement_timeout
        if timeout is not None and positive_seconds(timeout) != selected:
            raise ValueError("Direct operation timeout must match the explicitly bound allowance")
        self._frontend = frontend
        self._initialize_operation(
            request,
            identity=frontend._identity,
            provenance=frontend._provenance,
            ownership=frontend._ownership,
            instrument_state="not_applicable" if frontend._provenance == "simulated" else "unknown",
        )
        self._response_timeout = response_timeout
        self._calibration = CalibrationContext(
            "unknown", "Native calibration validity is unverified"
        )
        self._configuration_data = (("event_contract", "retained_patch_v1"),)
        self._documentary_failures = []

    def _document(self, value):
        """Retain validated primitives through the shared fallback before publishing failure."""
        try:
            return _json(value)
        except BaseException as failure:
            self._documentary_failures.append(failure)
            return _fallback_json(value)

    @property
    def request_json(self):
        return self._initial_result["request_json"]

    def _start_worker(self):
        with self._frontend._description_lock:
            if (
                json.loads(self._frontend.describe_json())["capability_id"]
                != (self._snapshot["capability_id"])
            ):
                raise ValueError("Direct descriptor changed before dispatch")
            super()._start_worker()

    def _run(self):
        meter = self._frontend._meter
        status, error = "failed", None
        meter.exchanges = []
        meter.settings_receipt = None
        meter.channel.cancel = self._cancel
        try:
            self._emit_started()
            if self._request["workflow"] == "mode_snapshot":
                meter.settings_receipt = dict(
                    requested={}, automatic=None, before=None, after=None,
                    commands=[], acknowledgements=[], status="failed", error=None,
                )
                try:
                    settings = meter._read_settings()
                except BaseException as failure:
                    meter.settings_receipt.update(
                        partial=deepcopy(meter.last_settings), error=str(failure),
                    )
                    raise
                meter.settings_receipt.update(after=settings, status="read")
            else:
                response = self._ask(
                    "sample_placement",
                    "Place the instrument on the selected sample",
                    ("accept", "cancel"),
                )
                if response == "cancel":
                    raise SerialCancelled("Direct sample placement cancelled")
                options = self._request["options"]
                changes = {
                    key: value
                    for key, value in options.items()
                    if key != "automatic" and value is not None
                }
                meter._apply_settings(changes, automatic=options.get("automatic", True))
                post_capture_failure = None
                try:
                    native, metadata = meter._acquire()
                except BaseException as failure:
                    if meter._retained_capture is None:
                        raise
                    native, metadata = meter._retained_capture
                    post_capture_failure = failure
                settings = meter.settings_receipt["after"]
                configuration = (
                    ("event_contract", "retained_patch_v1"),
                    ("direct_identity_json", self._document(meter.identity_details)),
                    ("direct_settings_json", self._document(meter.settings_receipt)),
                    ("direct_capture_json", self._document(metadata)),
                )
                observation = Observation(
                    1,
                    str(uuid4()),
                    self.operation_id,
                    self._request["patch_ids"][0],
                    datetime.now(timezone.utc).isoformat(),
                    self._provenance,
                    self._identity_record,
                    self._request["mode"],
                    self._request["mode"],
                    configuration,
                    self._calibration,
                    native,
                    ComputedValues(),
                    self._document(
                        dict(
                            identity_exchanges=meter.connection_exchanges,
                            exchanges=meter.exchanges,
                        )
                    ),
                    warnings=(
                        "Direct instrument calibration and physical operation are unverified",
                    ),
                )
                self._publish_patch(PatchResult(0, observation.sample_id, observation))
                if post_capture_failure is not None:
                    raise post_capture_failure
            with self._frontend._description_lock:
                facts = self._frontend._settings_facts(settings)
                if (
                    self._request["workflow"] == "mode_snapshot"
                    and facts != self._frontend._settings
                ):
                    self._frontend._settings = facts
                    self._frontend._settings_revision += 1
            status = "completed"
        except SerialCancelled as failure:
            status, error = "cancelled", str(failure)
        except BaseException as failure:
            error = f"{type(failure).__name__}: {failure}"
        finally:
            meter.channel.cancel = None
            self._software_stopped = True
            if not meter.channel.synchronized or meter.measurement_active:
                try:
                    meter._close()
                except BaseException as cleanup_error:
                    # This operation has no further native work. A quarantined
                    # persistent host port stays owned by its instrument/registry;
                    # its failed close does not keep the stopped worker's lease.
                    error = (error or "Direct cleanup failed") + (
                        f"; {type(cleanup_error).__name__}: {cleanup_error}"
                    )
            if (
                self._provenance == "physical"
                and meter.channel.synchronized
                and not meter.measurement_active
            ):
                self._instrument_state = "confirmed_ready" if status == "completed" else "unknown"
            # Retain proven lifetime facts even if documentary JSON encoding fails.
            self._initial_result.update(
                software_stopped=self._software_stopped, instrument_state=self._instrument_state,
            )
            try:
                self._configuration_data = (
                    ("event_contract", "retained_patch_v1"),
                    ("direct_identity_json", self._document(meter.identity_details)),
                    ("direct_exchanges_json", self._document(meter.exchanges)),
                    ("direct_settings_json", self._document(meter.settings_receipt)),
                    ("direct_cleanup_json", self._document(meter.cleanup)),
                )
            except BaseException as failure:
                status = "failed"
                error = (error or "Direct documentary encoding failed") + (
                    f"; {type(failure).__name__}: {failure}"
                )
            finally:
                if self._documentary_failures:
                    status = "failed"
                    detail = "; ".join(f"{type(failure).__name__}: {failure}"
                                       for failure in self._documentary_failures)
                    error = (error or "Direct documentary encoding failed") + "; " + detail
                self._finish(status, error)
