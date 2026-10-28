"""Explicit, blocking native metadata operations; importing performs no discovery.

The real helper is bound to a retained build report. Initialization is active
device access and requires operator authorization outside this API. No acquisition
or calibration route is provided here. Acquisition uses InstrumentFrontend.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from uuid import uuid4

from .argyll_source import ARCHIVE_SHA256
from .display_types import catalogue, validate_row
from .json_values import unique_object
from .mode_acceptance import validate_baseline_mode_checks
from .native_protocol import (
    NATIVE_TEXT_MAX_BYTES,
    HelperSession,
    ProtocolError,
    finite,
    invalid_constant,
)
from .native_receipt import native_worker_name

# pr655_imp_read_grid's D120 model contract: samples, increment nm, detector pixels.
PR_GRID_CONTRACTS = MappingProxyType({"PR-655": (101, 4, 128), "PR-670": (201, 2, 256)})

#: Startup effects no acknowledgement authorizes, so nothing opens an instrument
#: that discloses one. spyd2_init_inst downloads PLD firmware to a Spyder1 or
#: Spyder2 (spyd2.c:2997-3001, hwver < 4, via :2673); loading firmware is not a
#: measurement decision. description.c:init_blocker refuses it natively, and
#: session.c refuses it again for a direct acquisition caller.
UNAUTHORIZED_INITIALIZATION = frozenset({"initialization_downloads_pld"})


def _json(text: str) -> dict:
    try:
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ValueError("Invalid metadata JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Metadata must be an object")
    return value


def _uint(value, maximum=0xffffffff) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _threshold(value) -> bool:
    """driver_policy.h's matrix threshold: null, or a finite positive number."""
    return value is None or (type(value) in (int, float) and math.isfinite(value) and value > 0)


def _text(value, maximum: int, *, nullable=False) -> bool:
    if value is None:
        return nullable
    if not isinstance(value, str) or not value or "\0" in value:
        return False
    try:
        return len(value.encode("utf-8")) <= maximum
    except UnicodeError:
        return False


def validate_candidate(value) -> dict:
    """Validate the native path, transport flags and initialization facts."""
    if (
        not isinstance(value, dict)
        or set(value) != {"path", "dtype", "transport", "initialization_assessed",
                          "initialization_refusal"}
        or not _text(value["path"], 1024)
        or not _uint(value["dtype"], 0x7fffffff)
        or not _uint(value["transport"], 0xffff)
        or not value["transport"] & 0xff
        or not _text(value["initialization_refusal"], 200, nullable=True)
        or type(value["initialization_assessed"]) is not bool
    ):
        raise ProtocolError("Invalid candidate metadata")
    return value


def validate_pr_grid(value, *, model):
    """Check the exact model/grid contract verified by pr655_imp_read_grid.

    D120 supplies 101 samples at 4 nm for PR-655 or 201 at 2 nm for PR-670,
    each over 380..780 nm. Detector pixel count/range and reported bandwidth
    remain separate native facts; they do not infer extra spectral samples.
    This checks bounded metadata, not physical spectral or optical accuracy.
    """
    if (not isinstance(value, dict) or set(value) != {
        "count", "start_nm", "end_nm", "step_nm", "pixels", "first_pixel",
        "last_pixel", "bandwidth_nm",
    } or type(model) is not str or model not in PR_GRID_CONTRACTS
            or any(type(value[key]) is not int for key in value if key != "bandwidth_nm")):
        raise ProtocolError("Invalid reported PR model/grid")
    count, step, pixels = PR_GRID_CONTRACTS[model]
    if (value["count"] != count or value["start_nm"] != 380 or value["end_nm"] != 780
            or value["step_nm"] != step or value["pixels"] != pixels
            or not 0 <= value["first_pixel"] <= value["last_pixel"] < pixels
            or finite(value["bandwidth_nm"]) < 0):
        raise ProtocolError("Reported PR grid differs from its exact model contract")
    return value


def validate_native_identity(value, *, serial):
    """Validate identity volunteered by the protected initialized-instance adapter.

    No adapter means null. Current CR facts report their own model, firmware and
    instrument kind; that kind is independent of the Argyll initialized enum.
    """
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) not in (
        {"driver", "identity"}, {"driver", "identity", "native_controls"},
    ) or "native_controls" in value and type(value["native_controls"]) is not bool):
        raise ProtocolError("Malformed initialized native identity")
    identity = value["identity"]
    if value["driver"] == "pr655":
        if (not isinstance(identity, dict) or set(identity) != {
            "verified", "model", "serial", "firmware", "grid",
        } or identity["verified"] is not True
                or not _text(identity["serial"], NATIVE_TEXT_MAX_BYTES)
                or identity["serial"] != serial
                or not _text(identity["firmware"], NATIVE_TEXT_MAX_BYTES)
                or type(identity["model"]) is not str):
            raise ProtocolError("Invalid initialized PR identity or reported serial")
        validate_pr_grid(identity["grid"], model=identity["model"])
        return value
    if (value["driver"] != "cr250" or not isinstance(identity, dict)
            or set(identity) != {"verified", "model", "serial", "firmware",
                                "firmware_components", "instrument_type"}
            or identity["verified"] is not True
            or not _text(identity["model"], 200)
            or not _text(identity["serial"], NATIVE_TEXT_MAX_BYTES) or identity["serial"] != serial
            or not _text(identity["firmware"], 200, nullable=True)):
        raise ProtocolError("Invalid initialized native identity or reported serial")
    components = identity["firmware_components"]
    if components is not None and (
        not isinstance(components, list) or not 1 <= len(components) <= 3
        or any(not _uint(component, 9999) for component in components)
    ):
        raise ProtocolError("Invalid reported firmware components")
    kind = identity["instrument_type"]
    if kind is not None and not _uint(kind, 2):
        raise ProtocolError("Invalid reported instrument kind")
    return value


def _identity_allowed(selected_type, initialized_type):
    # insttypes.h:51-52: a selected instI1Pro2 (22) may initialise as instI1Pro
    # (21), the refinement description.c's identity_allowed accepts as well.
    return initialized_type == selected_type or (selected_type, initialized_type) == (22, 21)


def _description(message: dict, selected: dict, expected_type, expected_serial) -> None:
    candidate = validate_candidate(message.get("candidate"))
    if any(candidate[key] != selected[key] for key in ("path", "dtype", "transport")):
        raise ProtocolError("Description refers to a different candidate")
    actual = message.get("initialized_dtype")
    permitted_identity = (
        _identity_allowed(selected["dtype"], actual)
        or (selected["dtype"] == 0 and expected_type is not None and actual == expected_type)
    )
    if (
        message.get("access_scope") != "selected_device_active_initialization"
        or not _uint(message.get("initialized_dtype"), 0x7fffffff)
        or message["initialized_dtype"] == 0
        or not permitted_identity
        or (expected_type is not None and message["initialized_dtype"] != expected_type)
        or not _text(message.get("serial"), NATIVE_TEXT_MAX_BYTES, nullable=True)
        or (expected_serial is not None and message.get("serial") != expected_serial)
        or message.get("application_measurement_routes") != []
        or type(message.get("ambient_ccmx", False)) is not bool
        or not _threshold(message.get("ambient_matrix_threshold"))
    ):
        raise ProtocolError("Invalid initialized identity or application routes")
    if "native_identity" not in message:
        raise ProtocolError("Initialized description omits native identity availability")
    validate_native_identity(message["native_identity"], serial=message["serial"])
    masks = message.get("capabilities")
    if (
        not isinstance(masks, dict) or set(masks) != {"primary", "secondary", "tertiary"}
        or not all(_uint(v) for v in masks.values())
    ):
        raise ProtocolError("Invalid raw capability masks")
    try:
        from .strip_layout import validate_policies
        validate_policies(message["strip_policies"])
        validate_baseline_mode_checks(message.get("baseline_mode_checks"),
                                      mode_capabilities=masks["primary"])
    except (ValueError, KeyError) as error:
        raise ProtocolError(str(error)) from error
    config = message.get("current_configuration")
    if not isinstance(config, dict) or set(config) != {
        "status", "native_code", "index", "modes", "calibration_conditions",
    } or not _uint(config["native_code"]):
        raise ProtocolError("Invalid current configuration")
    if config["status"] == "available":
        if (
            config["native_code"] != 0 or type(config["index"]) is not int
            or not -1 <= config["index"] <= 0x7fffffff
            or not _uint(config["modes"]) or not _uint(config["calibration_conditions"])
        ):
            raise ProtocolError("Invalid available configuration")
    elif config["status"] != "unavailable" or config["native_code"] == 0 or any(
        config[key] is not None for key in ("index", "modes", "calibration_conditions")
    ):
        raise ProtocolError("Unavailable configuration must preserve unknowns")


def description_arguments(
    candidate_json, *, expected_initialized_dtype=None, expected_serial=None,
    allow_serial_identification=False, accepted_initialization_reason=None,
    accept_unassessed_initialization=False,
):
    """One arguments-only validator shared by setup admission and active description."""
    if not isinstance(candidate_json, str) or len(candidate_json) > 16384:
        raise ValueError("Candidate JSON must be text of at most 16384 characters")
    selected = validate_candidate(_json(candidate_json))
    if expected_initialized_dtype is not None and (
        not _uint(expected_initialized_dtype, 0x7fffffff) or expected_initialized_dtype == 0
    ):
        raise ValueError("expected_initialized_dtype must be a positive native enum")
    if (selected["dtype"] != 0 and expected_initialized_dtype is not None
            and not _identity_allowed(selected["dtype"], expected_initialized_dtype)):
        raise ValueError("Expected initialized type disagrees with the known selected candidate")
    if not _text(expected_serial, NATIVE_TEXT_MAX_BYTES, nullable=True):
        raise ValueError("expected_serial must be nonempty UTF-8 text or None")
    if type(allow_serial_identification) is not bool:
        raise ValueError("allow_serial_identification must be bool")
    if type(accept_unassessed_initialization) is not bool:
        raise ValueError("accept_unassessed_initialization must be bool")
    if accepted_initialization_reason is not None and (
        not isinstance(accepted_initialization_reason, str)
        or not re.fullmatch(r"initialization_[a-z_]{1,80}", accepted_initialization_reason)
    ):
        raise ValueError("Acceptance must name the disclosed initialization effect")
    if accepted_initialization_reason in UNAUTHORIZED_INITIALIZATION:
        raise ValueError("Firmware downloads are not authorized by initialization acceptance")
    if allow_serial_identification and (
        selected["dtype"] != 0 or expected_initialized_dtype is None
    ):
        raise ValueError("Serial identification needs unknown candidate and expected type")
    arguments = [
        "--describe", "--path", selected["path"], "--expected-dtype", str(selected["dtype"]),
        "--expected-transport", str(selected["transport"]),
    ]
    if expected_initialized_dtype is not None:
        arguments += ["--expected-initialized-dtype", str(expected_initialized_dtype)]
    if expected_serial is not None:
        arguments += ["--expected-serial", expected_serial]
    if allow_serial_identification:
        arguments += ["--allow-serial-identification"]
    if accepted_initialization_reason is not None:
        arguments += ["--accepted-initialization-reason", accepted_initialization_reason]
    if accept_unassessed_initialization:
        arguments += ["--accept-unassessed-initialization"]
    return selected, arguments


class NativeDescriptionClient:
    """Read catalog/build metadata or explicitly discover/initialize one device.

    Methods block with a bounded timeout; call them outside frontend event loops.
    Failed native operations return their error/terminal events, without retry.
    Protocol failures raise ProtocolError and keep validated events in last_events.
    """

    @classmethod
    def from_build(cls, report_path: str | Path, *, timeout: float = 30):
        path = Path(report_path).resolve()
        report_bytes = path.read_bytes()
        report = _json(report_bytes)
        if report.get("check_scope") == "substituted_binding":
            raise ValueError("A scoped substitute receipt requires explicit simulation selection")
        worker = report.get("description_worker_build_link", {})
        manifest = report.get("driver_manifest", {})
        if (
            report.get("status") != "passed" or report.get("argyll_version") != "3.5.0"
            or report.get("archive_sha256") != ARCHIVE_SHA256
            or report.get("driver_scope") != "all_public"
            or report.get("build_scope") != "all_public"
            or not isinstance(worker, dict) or not isinstance(manifest, dict)
            or worker.get("status") != "passed" or worker.get("executed") is not False
            or manifest.get("archive_sha256") != ARCHIVE_SHA256
            or manifest.get("build_scope") != "all_public"
            or not isinstance(manifest.get("instrument_types"), list)
        ):
            raise ValueError("A passed broad-driver build report is required")
        helper = path.parent / native_worker_name("argyll-description-worker-never-run",
                                                 report=report)
        client = cls(helper, timeout=timeout)
        if client._helper_sha256 != worker.get("executable_sha256"):
            raise ValueError("Helper does not match the retained build report")
        client._catalog = manifest
        client._build_report = report
        client._build_report_path = path
        client._report_sha256 = hashlib.sha256(report_bytes).hexdigest()
        return client

    @classmethod
    def for_offline_tests(cls, helper: str | Path, scenario: str = "success", *, timeout=5):
        """Only the separately built substituted worker is accepted for fixtures."""
        if Path(helper).name != native_worker_name("description-stub-worker"):
            raise ValueError("Offline checks require description-stub-worker")
        if not isinstance(scenario, str) or not re.fullmatch(r"[a-z0-9_]{1,40}", scenario):
            raise ValueError("Invalid fixture scenario")
        client = cls(Path(helper), timeout=timeout)
        client._fixture = scenario
        return client

    @classmethod
    def from_simulated_build(cls, report_path, *, source_root, scenario="success", timeout=30):
        """Bind original substituted-worker receipt fields without running a worker.

        The source checkout is explicit because an installed package does not
        contain all native build inputs. The receipt is trusted local evidence,
        not an authentication of arbitrary executables or physical equipment.
        InstrumentBuild separately authenticates its selected acquisition worker.
        """
        from .native_receipt import authenticate_stub

        path = Path(report_path).resolve()
        if path.name != "report.json":
            raise ValueError("Simulation requires the original sibling report.json")
        report_bytes = path.read_bytes()
        helper = path.parent / native_worker_name("description-stub-worker",
                                                 report=_json(report_bytes))
        report = authenticate_stub(helper, source_root=source_root, report_bytes=report_bytes)
        manifest = report.get("driver_manifest")
        if (report.get("driver_scope") != "all_public"
                or report.get("build_scope") != "all_public"
                or not isinstance(manifest, dict)
                or manifest.get("archive_sha256") != ARCHIVE_SHA256
                or manifest.get("build_scope") != "all_public"
                or not isinstance(manifest.get("instrument_types"), list)):
            raise ValueError("Simulation requires the original broad-driver catalog")
        client = cls.for_offline_tests(helper, scenario=scenario, timeout=timeout)
        if client._helper_sha256 != report["description_workflow"]["executable_sha256"]:
            raise ValueError("Substituted description worker changed during binding")
        client._catalog = manifest
        client._build_report = report
        client._build_report_path = path
        client._report_sha256 = hashlib.sha256(report_bytes).hexdigest()
        client._simulation_source_root = Path(source_root).resolve()
        return client

    def __init__(self, helper: Path, *, timeout: float):
        if type(timeout) not in (int, float) or not 0 < timeout <= 600:
            raise ValueError("timeout must be finite seconds in (0, 600]")
        self._helper = Path(helper).resolve()
        self._helper_sha256 = hashlib.sha256(self._helper.read_bytes()).hexdigest()
        self._timeout = timeout
        self._fixture = None
        self._catalog = None
        self._report_sha256 = None
        self._build_report = None
        self._build_report_path = None
        self._simulation_source_root = None
        self._lock = Lock()
        self.last_events: tuple[dict, ...] = ()

    def catalog_json(self) -> str:
        """Return the build's static catalog. Never starts a process or lists devices."""
        if self._catalog is None:
            raise ValueError("A substituted worker has no linked upstream catalog")
        return json.dumps(self._catalog, allow_nan=False)

    def list_candidates_json(self) -> str:
        """Explicit OS enumeration, without driver initialization/protocol probes."""
        return self._request("list_passive", ["--list-passive"])

    def describe_json(
        self, candidate_json: str, *, expected_initialized_dtype: int | None = None,
        expected_serial: str | None = None, allow_serial_identification: bool = False,
        accepted_initialization_reason: str | None = None,
        accept_unassessed_initialization: bool = False,
    ) -> str:
        """Actively initialize exactly the rechecked candidate; never acquire/calibrate."""
        selected, arguments = description_arguments(
            candidate_json, expected_initialized_dtype=expected_initialized_dtype,
            expected_serial=expected_serial,
            allow_serial_identification=allow_serial_identification,
            accepted_initialization_reason=accepted_initialization_reason,
            accept_unassessed_initialization=accept_unassessed_initialization,
        )
        return self._request("describe", arguments, selected, expected_initialized_dtype,
                             expected_serial)

    def _request(
        self, operation, arguments, selected=None, expected_type=None, expected_serial=None,
    ):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A metadata operation already owns this client")
        events = []
        self.last_events = ()
        try:
            if self._fixture is None and self._report_sha256 is None:
                raise ValueError("Physical metadata operations require from_build report binding")
            if hashlib.sha256(self._helper.read_bytes()).hexdigest() != self._helper_sha256:
                raise ValueError("Helper changed since client construction")
            operation_id = str(uuid4())
            command = [str(self._helper)]
            if self._fixture is not None:
                command += ["--fixture", self._fixture]
            command += ["--operation-id", operation_id, *arguments]
            provenance = "simulated" if self._fixture is not None else "physical"
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ARGYLL_")}
            with HelperSession(command, timeout=self._timeout, env=env) as session:
                state, data, error = "idle", None, None
                display_rows = []
                while True:
                    message = session.receive_message()
                    if (
                        type(message.get("schema_version")) is not int
                        or message["schema_version"] != 1
                        or message.get("operation_id") != operation_id
                        or type(message.get("sequence")) is not int
                        or message["sequence"] != len(events)
                        or message.get("provenance") != provenance
                        or message.get("backend_version") != "3.5.0"
                    ):
                        raise ProtocolError("Wrong metadata event identity, version or sequence")
                    event = message.get("event")
                    if state == "idle" and event == "started":
                        if message.get("operation") != operation:
                            raise ProtocolError("Wrong metadata operation")
                    elif (
                        state == "started" and event == "candidates" and operation == "list_passive"
                    ):
                        if (
                            message.get("discovery_scope")
                            != "os_path_metadata_without_protocol_identification"
                            or message.get("initialization_performed") is not False
                            or not isinstance(message.get("candidates"), list)
                            or len(message["candidates"]) > 512
                        ):
                            raise ProtocolError("Invalid candidate list")
                        for candidate in message["candidates"]:
                            validate_candidate(candidate)
                        data = message
                    elif state == "started" and event == "display_type" and operation == "describe":
                        try:
                            display_rows.append(validate_row(
                                {key: value for key, value in message.items() if key not in
                                 {"event", "schema_version", "operation_id", "sequence",
                                  "provenance", "backend_version"}}, len(display_rows)))
                        except ValueError as fault:
                            raise ProtocolError(str(fault)) from fault
                        events.append(message)
                        self.last_events = tuple(events)
                        continue
                    elif state == "started" and event == "description" and operation == "describe":
                        _description(message, selected, expected_type, expected_serial)
                        try:
                            message["display_types"] = catalogue(
                            message.get("display_types"), display_rows)
                            if message["display_types"]["applied_choice_id"] is not None:
                                raise ValueError("Description applied a display choice")
                        except ValueError as fault:
                            raise ProtocolError(str(fault)) from fault
                        data = message
                    elif state == "started" and event == "error":
                        if (
                            not _text(message.get("stage"), 200)
                            or not _uint(message.get("native_code"))
                            or not _text(message.get("refusal_reason"), 200, nullable=True)
                        ):
                            raise ProtocolError("Invalid native metadata failure")
                        error = message
                    elif state in {"description", "candidates", "error"} and event == "terminal":
                        expected_status = "failed" if error else "completed"
                        if (
                            message.get("status") != expected_status
                            or type(message.get("initialization_performed")) is not bool
                            or type(message.get("initialization_completed")) is not bool
                            or type(message.get("active_access_attempted")) is not bool
                            or type(message.get("callback_refused")) is not bool
                            or not isinstance(message.get("cleanup"), str)
                            or message.get("cleanup") not in {
                                "destructor_returned", "not_constructed", "destructor_unavailable",
                            }
                            or message.get("software_stopped") is not True
                            or message.get("instrument_state") != "unknown"
                            or (data is not None and operation == "describe"
                                and (not message["initialization_completed"]
                                     or message["cleanup"] != "destructor_returned"
                                     or message["callback_refused"]))
                            or (message["initialization_completed"]
                                and not message["initialization_performed"])
                            or (message["initialization_performed"]
                                and not message["active_access_attempted"])
                            or (operation == "list_passive"
                                and message["active_access_attempted"])
                        ):
                            raise ProtocolError("Invalid native metadata terminal state")
                        events.append(message)
                        self.last_events = tuple(events)
                        session.finish_messages(expected_returncode=1 if error else 0)
                        break
                    else:
                        raise ProtocolError("Unexpected or repeated native metadata event")
                    events.append(message)
                    self.last_events = tuple(events)
                    state = event
            return json.dumps({
                "schema_version": 1, "operation_id": operation_id, "provenance": provenance,
                "backend": {"version": "3.5.0", "helper_sha256": self._helper_sha256,
                            "build_report_sha256": self._report_sha256},
                "status": events[-1]["status"], "data": data, "error": error,
                "events": events,
            }, allow_nan=False)
        finally:
            self._lock.release()
