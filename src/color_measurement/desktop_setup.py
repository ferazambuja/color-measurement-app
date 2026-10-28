"""Deliberate desktop equipment setup over the existing InstrumentBuild owner."""

import hashlib
import json
import math
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, get_ident
from uuid import uuid4

from .application import _uuid
from .dataset_binding import content_digest
from .discovery import InstrumentBuild
from .native_controls import NativeControlState
from .serial_frontend import DirectSerialFrontend
from .serial_instruments import DirectSerialInstrument, direct_serial_catalog


class DesktopInstrumentSetup:
    """Session-owned report/candidate fences; the host serializes these blocking calls.

    Report selection reads authenticated build metadata. Only explicit refresh
    enumerates; only explicit connect initializes the chosen unit. InstrumentBuild
    remains the sole owner of native policy, identification, binding and timeouts.
    """

    def __init__(self, mutex, validate_idle, executor=None):
        self._mutex = mutex
        self._validate_idle = validate_idle
        self._executor = executor
        self._active = None
        self._future = None
        self.generation = 0
        self.listing_generation = 0
        self._build = None
        self._report = None
        self._catalog = None
        self._candidates = {}
        self._consumed = set()
        self._commands = {}
        self._registered_paths = set()
        self._registered_serials = set()
        self._bindings = {}
        self._unrestored_backlight = {}
        self._native_control_state = NativeControlState()
        self._state = "unselected"
        self._reason = None

    def snapshot(self):
        with self._mutex:
            return self._snapshot()

    def _snapshot(self):
        for record in self._bindings.values():
            view = record["view"]
            if view.get("backend") == "direct_serial":
                meter = record["instrument"]
                descriptor = (json.loads(record["frontend"].describe_json())
                              if record["frontend"] is not None else dict(modes=[]))
                view["connection_available"] = any(
                    mode["available"] for mode in descriptor["modes"]
                )
                view["connection_reason"] = None if view["connection_available"] else \
                    "Close this unavailable connection before explicitly reopening it"
                if (view["state"] == "active" and not meter.cleanup["port_closed"]
                        and meter.channel is not None
                        and meter.channel._failed):
                    view.update(state="cleanup_required", cleanup=deepcopy(meter.cleanup))
        rows = [dict(
            candidate_id=identity, **asdict(found), transports=list(found.transports),
            identified=found.identified, attachable=found.attachable,
            opening_requires=found.opening_requires, consumed=identity in self._consumed,
        ) for identity, found in self._candidates.items()]
        return deepcopy(dict(
            generation=self.generation, listing_generation=self.listing_generation,
            state=self._state, reason=self._reason, report=self._report,
            catalog=self._catalog, candidates=rows,
            direct_serial=direct_serial_catalog(),
            active_setup=dict(
                command_id=self._active["command_id"],
                action=self._active["action"],
                backend=self._active["backend"],
                stage=self._commands[self._active["command_id"]]["stage"],
                cancel_requested=self._active["cancel"].is_set(),
                cancellation_mode=("cooperative" if self._active["backend"] == "direct_serial"
                                   else "after_initialization"),
            ) if self._active is not None else None,
            bindings=[record["view"] for record in self._bindings.values()],
        ))

    def _fence(self, generation):
        if type(generation) is not int or generation != self.generation:
            raise ValueError("Equipment setup generation changed")

    def select_report(self, path, *, expected_generation, simulated=False,
                      source_root=None, scenario="success", simulated_session="generic"):
        """Native chooser only: authenticate both workers without starting either."""
        self._fence(expected_generation)
        if type(simulated) is not bool:
            raise ValueError("simulated must be an explicit boolean")
        path = Path(path).resolve()
        original = path.read_bytes()
        build = (InstrumentBuild.from_report(path, simulated=simulated, source_root=source_root,
                                             scenario=scenario, simulated_session=simulated_session)
                 if (simulated or source_root is not None or scenario != "success"
                     or simulated_session != "generic")
                 else InstrumentBuild.from_report(path))
        catalog = json.loads(build.catalog_json())
        for row in catalog["instrument_types"]:
            row.update(build.model_policy(row["enum"]))
        if path.read_bytes() != original:
            raise ValueError("Build report changed during selection")
        self._build = build
        self._report = dict(path=str(path), sha256=hashlib.sha256(original).hexdigest(),
                            context=build.binding_context())
        self._catalog = catalog
        self.generation += 1
        self.listing_generation = 0
        self._candidates = {}
        self._consumed = set()
        self._state, self._reason = "ready", None
        return self.snapshot()

    def _command(self, command_id, content, operation, *, requires_build=True, executor=None):
        _uuid(command_id)
        digest = content_digest(content)
        direct = content["action"] in {"connect_direct_serial", "reopen_direct_serial"}
        supervised = direct or executor is not None
        with self._mutex:
            previous = self._commands.get(command_id)
            if previous is not None:
                if previous["content_sha256"] != digest:
                    raise ValueError("Equipment command ID has different content")
                return deepcopy(previous)
            self._fence(content["setup_generation"])
            self._validate_idle()
            if requires_build and self._build is None:
                raise ValueError("Select an authenticated build report first")
            # Reserve before submission or any client call. Replay cannot initialize twice.
            result = dict(command_id=command_id, content_sha256=digest,
                          setup_generation=self.generation,
                          state="pending" if supervised else "failed", reason=None, result=None,
                          stage="admission")
            self._commands[command_id] = result
            active = dict(command_id=command_id, cancel=Event(), thread_id=None,
                          action=content["action"],
                          backend="direct_serial" if direct else "argyll",
                          registration_committed=False) if supervised else None
            if supervised:
                self._active = active

        def execute():
            if active is not None:
                with self._mutex:
                    if self._active is not active:
                        return deepcopy(result)
                    active["thread_id"] = get_ident()
            try:
                if active is not None and active["cancel"].is_set():
                    raise ValueError(
                        "Setup cancelled before native access; no instrument was opened")
                value = operation(result)
                with self._mutex:
                    result.update(result=value, state="completed")
                    self._reason = None
            except (ValueError, TypeError, OSError, RuntimeError) as error:
                with self._mutex:
                    result.update(state="failed", reason=str(error))
                    self._state, self._reason = "failed", str(error)
            except BaseException:
                with self._mutex:
                    result.update(state="interrupted", reason=(
                        f"Equipment setup interrupted during {result['stage']}; "
                        "native cleanup and instrument state are unconfirmed"
                    ))
                    self._state, self._reason = "interrupted", result["reason"]
                raise
            finally:
                if active is not None:
                    with self._mutex:
                        self._active = None
            return deepcopy(result)

        if executor is None:
            return execute()
        with self._mutex:
            self._future = None
            try:
                self._future = executor.submit(execute)
                def cancelled_before_start(future):
                    if not future.cancelled():
                        return
                    with self._mutex:
                        if self._active is active and active["thread_id"] is None:
                            result.update(state="failed", reason=(
                                "Initialization cancelled before worker start; "
                                "no instrument was opened"
                            ))
                            self._active = None
                self._future.add_done_callback(cancelled_before_start)
            except BaseException as error:
                self._active = None
                result.update(state="failed" if isinstance(error, Exception) else "interrupted",
                              reason=f"Equipment worker submission failed: {type(error).__name__}: "
                              f"{error}")
                if not isinstance(error, Exception):
                    raise
            return deepcopy(result)

    def outcome(self, command_id):
        """Path-free reconciliation; never repeat a chooser, initialization or close."""
        _uuid(command_id)
        with self._mutex:
            if command_id not in self._commands:
                raise ValueError("Unknown equipment command ID")
            return deepcopy(self._commands[command_id])

    def cancel(self, command_id):
        """Request cancellation of this exact admitted initialization, without cleanup claims."""
        _uuid(command_id)
        with self._mutex:
            if self._active is None or self._active["command_id"] != command_id:
                raise ValueError("This device setup is no longer active")
            if self._active["registration_committed"]:
                raise ValueError(
                    "The device listing has been published; cancellation is too late"
                    if self._active["action"] == "refresh_devices" else
                    "Source registration has completed; cancellation is too late")
            self._active["cancel"].set()
            self._commands[command_id]["cancel_requested"] = True
            if self._future is not None and self._active["thread_id"] is None:
                self._future.cancel()
            return self.outcome(command_id)

    def _check_cancel(self):
        if self._active is not None and self._active["cancel"].is_set():
            raise ValueError(
                "Device setup cancellation requested; no source was registered. "
                "Instrument state remains unconfirmed"
            )

    def _metadata(self, outcome, operation):
        """Keep validated original helper events on both success and refusal."""
        try:
            return operation()
        finally:
            with self._mutex:
                outcome["native_events"] = deepcopy(list(self._build._client.last_events))

    def connect_direct(
        self, register, port, *, command_id, setup_generation, model, expected_serial,
        accept_disclosed_initialization, response_timeout, measurement_timeout,
        registration_transaction, validate_replacement, binding_id=None,
        binding_generation=None, transport=None, clock=time, executor=None,
    ):
        """Native selected path only; share command, binding and retirement ownership.

        Reopen requires a confirmed closed host port and the retained model/serial.
        Failed registration closes its candidate; unconfirmed release stays owned
        by a cleanup-required binding and prevents another opening on that path.
        """
        content = dict(
            action="reopen_direct_serial" if binding_id is not None else "connect_direct_serial",
            setup_generation=setup_generation, port=port, model=model,
            expected_serial=expected_serial,
            accept_disclosed_initialization=accept_disclosed_initialization,
            response_timeout=response_timeout, measurement_timeout=measurement_timeout,
            binding_id=binding_id, binding_generation=binding_generation,
            provenance="simulated" if transport is not None else "physical",
        )

        def run(outcome):
            outcome["stage"] = "selection_validation"
            if accept_disclosed_initialization is not True:
                raise ValueError("Explicitly accept the direct model's disclosed initialization")
            previous = self._binding(binding_id, binding_generation) if binding_id else None
            if previous is not None:
                view = previous["view"]
                if view.get("backend") != "direct_serial" or view["state"] != "closed":
                    raise ValueError("Confirm direct host port close before reopening")
                if not view["serial"] or (model, expected_serial) != (
                    view["model"], view["serial"],
                ):
                    raise ValueError("Reopen must assert the retained direct model and serial")
                validate_replacement(previous["frontend"])
            if port in self._registered_paths:
                raise ValueError("This path already has a registered or cleanup-required source")
            meter = DirectSerialInstrument(
                model, port, expected_serial=expected_serial, response_timeout=response_timeout,
                measurement_timeout=measurement_timeout, transport=transport, clock=clock,
                unrestored_backlight=self._unrestored_backlight,
                initialization_cancel=self._active["cancel"],
            )
            identity = binding_id or str(uuid4())
            generation = previous["view"]["generation"] + 1 if previous else 1
            policy = next(row for row in direct_serial_catalog(response_timeout=response_timeout)[
                "models"] if row["model_enum"] == model)
            connection = dict(path=port, index=None, identity_basis=None)
            record = dict(frontend=None, instrument=meter, view=dict(
                **policy, binding_id=identity, generation=generation, state="initializing",
                device_id=previous["view"]["device_id"] if previous else None,
                model=model, serial=expected_serial, connection=connection,
            ))
            connected = False
            try:
                outcome["stage"] = "initialization"
                meter.connect()
                connected = True
                serial_key = (model, meter.identity.serial)
                if serial_key in self._registered_serials:
                    raise ValueError(
                        "This reported model and serial already has a registered source",
                    )
                connection["identity_basis"] = "reported-model-and-serial"
                device_id = "direct:" + hashlib.sha256(json.dumps(
                    [model, meter.identity.serial], ensure_ascii=True, separators=(",", ":"),
                ).encode()).hexdigest()
                meter.identity = replace(meter.identity, device_id=device_id)
                record["view"].update(device_id=device_id, serial=meter.identity.serial)
                outcome["stage"] = "settings_readback"
                frontend = DirectSerialFrontend(meter, meter.read_settings())
                record["frontend"] = frontend
                descriptor = json.loads(frontend.describe_json())
                outcome["stage"] = "registration"
                with registration_transaction():
                    self._fence(setup_generation)
                    if binding_id is not None:
                        self._binding(binding_id, binding_generation)
                    if self._active["cancel"].is_set():
                        raise ValueError("Direct initialization cancellation requested")
                    token = register(frontend)
                    record["view"]["state"] = "active"
                    self._bindings[identity] = record
                    self._registered_paths.add(port)
                    self._registered_serials.add(serial_key)
                    self._state = "registered"
                    self._active["registration_committed"] = True
                return dict(
                    selection_id=token, descriptor=descriptor, binding_id=identity,
                    binding_generation=generation, connection=connection,
                    native_receipt=dict(identity=deepcopy(meter.identity_details),
                                        identity_exchanges=deepcopy(meter.connection_exchanges),
                                        exchanges=deepcopy(meter.exchanges),
                                        settings=deepcopy(meter.last_settings)),
                )
            except BaseException as error:
                if connected:
                    try:
                        meter.close()
                    except BaseException as cleanup_error:
                        error.add_note(f"Direct candidate cleanup failed: {cleanup_error}")
                outcome["result"] = dict(native_receipt=dict(
                    identity=deepcopy(meter.identity_details), exchanges=deepcopy(meter.exchanges),
                    identity_exchanges=deepcopy(meter.connection_exchanges),
                    settings=deepcopy(meter.last_settings), cleanup=deepcopy(meter.cleanup),
                ))
                if not meter.cleanup["port_closed"]:
                    with self._mutex:
                        record["view"].update(state="cleanup_required",
                                             cleanup=deepcopy(meter.cleanup))
                        self._bindings[identity] = record
                        self._registered_paths.add(port)
                        if meter.identity is not None:
                            self._registered_serials.add((model, meter.identity.serial))
                        outcome["result"]["binding"] = deepcopy(record["view"])
                raise

        return self._command(command_id, content, run, requires_build=False, executor=executor)

    def refresh(self, *, command_id, setup_generation):
        """One explicit enumeration; retire previous candidate tokens even on failure."""
        def run(outcome):
            self.listing_generation += 1
            self._candidates = {}
            self._consumed = set()
            outcome["stage"] = "enumeration"
            self._check_cancel()
            found = self._metadata(outcome, self._build.attached)
            with self._mutex:
                self._check_cancel()
                self._candidates = {str(uuid4()): candidate for candidate in found}
                self._state = "listed"
                # Published: from here cancellation could no longer withdraw the
                # listing, so it is refused rather than reported with a completion.
                if self._active is not None:
                    self._active["registration_committed"] = True
            completed = self.snapshot()
            # The result is published after the shared command retires its active lease.
            completed["active_setup"] = None
            return completed

        return self._command(command_id, dict(action="refresh_devices",
                                             setup_generation=setup_generation), run,
                             executor=self._executor)

    def connect(self, register, *, registered_ids, command_id, setup_generation, listing_generation,
                candidate_id, expect, expected_serial, allow_serial_identification,
                accept_disclosed_initialization, accept_unassessed_initialization, timeout,
                binding_id=None, binding_generation=None, validate_replacement=None,
                reopening=False, registration_transaction):
        """Initialize only the selected candidate, then register its cached frontend.

        A candidate is consumed before description. After any attempted opening,
        another attempt requires a new explicit listing and candidate selection.
        Failure and command reconciliation do not retry native work.
        """
        content = dict(
            action="reopen_device" if reopening else "connect_device",
            setup_generation=setup_generation,
            listing_generation=listing_generation, candidate_id=candidate_id,
            expect=expect, expected_serial=expected_serial,
            allow_serial_identification=allow_serial_identification,
            accept_disclosed_initialization=accept_disclosed_initialization,
            accept_unassessed_initialization=accept_unassessed_initialization, timeout=timeout,
            binding_id=binding_id, binding_generation=binding_generation,
        )

        def run(outcome):
            outcome["stage"] = "selection_validation"
            if type(listing_generation) is not int or listing_generation != self.listing_generation:
                raise ValueError("Equipment listing generation changed")
            if not isinstance(candidate_id, str) or candidate_id not in self._candidates:
                raise ValueError("Select a current listed candidate")
            if candidate_id in self._consumed:
                raise ValueError(
                    "Candidate was consumed; explicitly refresh before another attempt"
                )
            for flag in (allow_serial_identification, accept_disclosed_initialization,
                         accept_unassessed_initialization):
                if type(flag) is not bool:
                    raise ValueError(
                        "Initialization and identification acknowledgements are boolean"
                    )
            if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                    or not 0 < timeout <= 600):
                raise ValueError("Binding timeout must be finite seconds in (0, 600]")
            found = self._candidates[candidate_id]
            previous = self._binding(binding_id, binding_generation) if reopening else None
            previous_view = previous["view"] if previous else None
            if previous_view and previous_view.get("backend") == "direct_serial":
                raise ValueError("Use the native direct serial reopen method")
            old_active = previous is not None and previous_view["state"] == "active"
            if previous is not None:
                if not previous_view["serial"]:
                    raise ValueError(
                        "Reopening requires an original reported serial; select a new source"
                    )
                validate_replacement(previous["frontend"])
            if ((found.path in self._registered_paths and not (
                    old_active and found.path == previous_view["connection"]["path"]))
                    or found.path in registered_ids):
                raise ValueError("This path already has a registered source")
            # Read the existing driver's floor before initialization, rather than
            # opening the unit only to discover its requested binding cannot fit.
            model = previous_view["model"] if previous else (
                expect if expect is not None else found.symbol)
            if model is not None:
                self._build.validate_binding_timeout(model, timeout)
            self._build.validate_selection(
                found, expect=model if previous else expect,
                expected_serial=previous_view["serial"] if previous else expected_serial,
                allow_serial_identification=allow_serial_identification,
                accept_disclosed_initialization=accept_disclosed_initialization,
                accept_unassessed_initialization=accept_unassessed_initialization,
            )
            self._consumed.add(candidate_id)
            outcome["stage"] = "description"
            self._check_cancel()
            description = self._metadata(outcome, lambda: self._build.describe(
                found, expect=model if previous else expect,
                expected_serial=previous_view["serial"] if previous else expected_serial,
                allow_serial_identification=allow_serial_identification,
                accept_disclosed_initialization=accept_disclosed_initialization,
                accept_unassessed_initialization=accept_unassessed_initialization,
            ))
            self._check_cancel()
            serial_key = (description.name, description.serial)
            old_key = (previous_view["model"], previous_view["serial"]) if previous else None
            if previous and serial_key != old_key:
                raise ValueError("Reopened source reported a different model or serial")
            if description.serial and serial_key in self._registered_serials and not (
                    old_active and serial_key == old_key):
                raise ValueError("This reported model and serial already has a registered source")
            device_id = "instrument:" + hashlib.sha256(json.dumps(
                serial_key, ensure_ascii=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest() if description.serial else "session:" + str(uuid4())
            outcome["stage"] = "binding"
            frontend = self._build.bind(
                description, allow_serial_identification=allow_serial_identification,
                timeout=timeout, device_id=device_id,
                native_control_state=self._native_control_state,
            )
            outcome["stage"] = "registration"
            descriptor = json.loads(frontend.describe_json())
            model_policy = self._build.model_policy(description.name)
            if model_policy["native_type"] != description.initialized_type:
                raise ValueError("Bound model policy differs from initialized instrument type")
            identity_basis = ("reported-model-and-serial" if description.serial
                              else "session-only-no-reported-serial")
            connection = dict(path=found.path, index=found.index, identity_basis=identity_basis)
            identity = binding_id or str(uuid4())
            generation = previous_view["generation"] + 1 if previous else 1
            with registration_transaction():
                self._check_cancel()
                token = (register(frontend, replacing=previous["frontend"])
                         if old_active else register(frontend))
                if old_active:
                    self._registered_paths.discard(previous_view["connection"]["path"])
                    self._registered_serials.discard(old_key)
                self._registered_paths.add(found.path)
                if description.serial:
                    self._registered_serials.add(serial_key)
                self._state = "registered"
                self._bindings[identity] = dict(frontend=frontend, view=dict(
                    binding_id=identity, generation=generation, state="active", device_id=device_id,
                    model=description.name, serial=description.serial, connection=connection,
                    **model_policy,
                ))
                if self._active is not None:
                    self._active["registration_committed"] = True
                return dict(selection_id=token, descriptor=descriptor,
                            listing_generation=self.listing_generation, candidate_id=candidate_id,
                            binding_id=identity, binding_generation=generation,
                            connection=connection)

        return self._command(command_id, content, run, executor=self._executor)

    def _binding(self, identity, generation):
        _uuid(identity)
        record = self._bindings.get(identity)
        if (record is None or type(generation) is not int
                or generation != record["view"]["generation"]):
            raise ValueError("Equipment binding changed; refresh its current snapshot")
        return record

    @contextmanager
    def registration_transaction(self):
        """Rollback only logical registry changes; keep attempted candidates consumed."""
        previous = dict(
            bindings={identity: dict(record, view=deepcopy(record["view"]))
                      for identity, record in self._bindings.items()},
            paths=set(self._registered_paths), serials=set(self._registered_serials),
            candidates=dict(self._candidates), consumed=set(self._consumed),
            listing_generation=self.listing_generation, state=self._state,
        )
        try:
            yield
        except BaseException:
            self._bindings = previous["bindings"]
            self._registered_paths = previous["paths"]
            self._registered_serials = previous["serials"]
            self._candidates, self._consumed = previous["candidates"], previous["consumed"]
            self.listing_generation, self._state = previous["listing_generation"], previous["state"]
            raise

    def close(self, retire, *, command_id, setup_generation, binding_id, binding_generation,
              registration_transaction, validate_retirement):
        """Release a direct host port before retiring; Argyll holds no persistent worker."""
        def run(outcome):
            outcome["stage"] = "selection_validation"
            record = self._binding(binding_id, binding_generation)
            view = record["view"]
            direct = view.get("backend") == "direct_serial"
            if view["state"] not in ({"active", "cleanup_required"} if direct else {"active"}):
                raise ValueError("Equipment binding is already retired")
            validate_retirement(record["frontend"])
            cleanup = None
            if direct:
                outcome["stage"] = "host_cleanup"
                try:
                    record["instrument"].close()
                except BaseException:
                    view.update(state="cleanup_required",
                                cleanup=deepcopy(record["instrument"].cleanup))
                    outcome["result"] = dict(binding=deepcopy(view))
                    raise
                cleanup = deepcopy(record["instrument"].cleanup)
            outcome["stage"] = "registration"
            with registration_transaction():
                retired = retire(record["frontend"])
                self._registered_paths.discard(view["connection"]["path"])
                if view["serial"]:
                    self._registered_serials.discard((view["model"], view["serial"]))
                view.update(state="closed", generation=view["generation"] + 1)
                self.listing_generation += 1
                self._candidates, self._consumed = {}, set()
                self._state = "retired"
                return dict(binding=deepcopy(view), retired_selection_ids=retired,
                            software_selection_retired=True, instrument_state="unknown",
                            native_cleanup=cleanup if direct else
                            "not_performed_no_persistent_worker")

        return self._command(command_id, dict(action="close_device",
                                             setup_generation=setup_generation,
                                             binding_id=binding_id,
                                             binding_generation=binding_generation), run,
                             requires_build=False)
