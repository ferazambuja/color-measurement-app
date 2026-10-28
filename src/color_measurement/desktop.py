"""Offline portable desktop controller. No HTTP acquisition service."""

import hashlib
import json
import time
from concurrent.futures import wait
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from . import desktop_assets_manifest
from .desktop_session import _science_material_role


@dataclass
class Dialog:
    """A native choice; destructive and physical confirmations have no default."""

    title: str = ""
    text: str = ""
    buttons: list[str] = field(default_factory=list)
    default: int | None = None
    cancel: int = 0


def _failure_text(error):
    """Keep the original reason, with a useful fallback for empty exceptions."""
    try:
        return str(error) or type(error).__name__
    except Exception:
        return type(error).__name__


def _configure_instrument_software(session, resource_root, expected_report_sha256):
    """Authenticate bundled software without listing or opening an instrument.

    The compiled package identity binds the portable report, which binds both workers
    and the retained native notices. Source and wheel launches retain explicit
    development setup. A packaged failure remains visible and never selects a
    simulated measurement source.
    """
    if expected_report_sha256 is None:
        return dict(packaged=False, ready=False, error=None)
    try:
        if (
            not isinstance(expected_report_sha256, str)
            or len(expected_report_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_report_sha256)
        ):
            raise ValueError("The application's instrument software identity is invalid")
        report = Path(resource_root) / "instrument-software" / "report.json"
        encoded = report.read_bytes()
        if hashlib.sha256(encoded).hexdigest() != expected_report_sha256:
            raise ValueError("The application's instrument software has changed")
        for relative, digest in json.loads(encoded)["bundled_license_sha256"].items():
            if hashlib.sha256((report.parent / relative).read_bytes()).hexdigest() != digest:
                raise ValueError("The application's instrument software notices have changed")
        session.select_build_report(report, expected_setup_generation=0)
        if hashlib.sha256(report.read_bytes()).hexdigest() != expected_report_sha256:
            raise ValueError("The application's instrument software changed during startup")
    except Exception as error:
        return dict(
            packaged=True,
            ready=False,
            error=(
                "Instrument software could not be loaded. Reinstall this application "
                "from a complete build before connecting an instrument. " + _failure_text(error)
            ),
        )
    return dict(packaged=True, ready=True, error=None)


def _close_summary(session):
    """Take a discard identity only after the current publication leases settle."""
    deadline = time.monotonic() + 30
    while session.batch:
        current = session.batch.snapshot()
        if current["phase"] != "saving" and not current["companions"]["saving"]:
            break
        if time.monotonic() >= deadline:
            raise ValueError("Automatic or batch saving has not settled within 30 seconds.")
        Event().wait(0.02)
    state = session.batch.snapshot() if session.batch else None
    needs_close = state is not None and not (
        session.batch._closed or state["process_exit_acknowledged"]
    )
    return dict(
        contents=session.batch.discard_summary() if needs_close else None,
        state=state,
        pending_exports=session.export_recovery(),
    )


def _choose_chart_input(ui, *, target):
    """Keep TI2 paths and continuation source folders at the native chooser."""
    return ui.file_path(
        "Choose a TI2 target" if target else "Choose a saved chart batch",
        extensions=["ti2"] if target else None,
        directory=not target,
    )


def _choose_serial_port(ui):
    """Native human port selection only; listing opens no instrument or serial channel."""
    try:
        from serial.tools import list_ports
    except ImportError as error:
        raise ImportError("Direct serial instruments require pySerial") from error

    ports = sorted(list_ports.comports(), key=lambda item: item.device)
    if not ports:
        raise ValueError("No serial ports are available. Connect the device, then choose its port.")
    return ui.choose_port(ports)


def _choose_record_input(ui, format):
    """Select one explicitly typed original record; paths never enter renderer requests."""
    from .source_records import FORMATS

    if format not in FORMATS:
        raise ValueError("Choose a current record format before opening a file")
    extension = format.rsplit("_", 1)[1]
    kind = "operation" if format.startswith("operation_") else "color observation"
    return ui.file_path(f"Open an original {kind} {extension.upper()} file", extensions=[extension])


def _choose_science_material(ui, role):
    """Select a current typed Spectrum JSON document through the native dialog."""
    role = _science_material_role(role)
    return ui.file_path(f"Choose a declared {role} spectrum JSON file", extensions=["json"])


def _choose_record_outputs(host, format):
    """Select both SPECT destinations before publication through the existing owner."""
    path = host.choose_export(format)
    if path is None:
        return None
    canonical = host.choose_export("json") if format == "sp" else None
    if format == "sp" and canonical is None:
        return None
    return path, canonical


def _choose_build_report(ui):
    """Select an existing report locally; authentication starts no device worker."""
    return ui.file_path("Choose an instrument software report", extensions=["json"])


def _choose_correction(ui):
    """Keep correction paths at the native chooser; Python validates actual file kind."""
    return ui.file_path("Choose a colorimeter correction", extensions=["ccmx", "ccss"])


def _copy_summary(ui, text):
    """Copy explicit operator text locally through the native pasteboard."""
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 131072:
        raise ValueError("Choose a nonempty summary within the clipboard size limit")
    ui.copy_text(text)
    return dict(copied=True)


def _failure_alert(title, reason):
    """One standard native acknowledgement for an informational failure."""
    return Dialog(title, _failure_text(reason), ["Dismiss"], default=0)


def _bridge_document(*, file_url, document_path, workspace):
    """Authenticate the sending document: a file URL at the bundled workspace path.

    Frames are not inspected here; the message handler admits main-frame
    messages only, before calling this.
    """
    if not file_url or Path(document_path).resolve() != workspace.resolve():
        raise ValueError("Only the bundled workspace document can use the owner bridge")


def _bridge_request(body):
    """Validate the closed bridge envelope before any native or owner action."""
    if (
        not isinstance(body, dict)
        or set(body) != {"id", "method", "params"}
        or not isinstance(body["id"], str)
        or not body["id"]
        or not isinstance(body["method"], str)
        or not body["method"]
        or not isinstance(body["params"], dict)
    ):
        raise ValueError("Use the declared bridge envelope")
    try:
        wire = json.dumps(body, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError("Bridge envelope requires finite JSON values") from error
    if len(body["id"]) > 64 or len(wire) > 132096:
        raise ValueError("Bridge envelope exceeds its size limit")
    return body["id"], body["method"], body["params"]


def _reply_script(request_id, value=None, error=None):
    """Encode a finite reply for a validated request identity, with an error fallback."""
    reply = dict(
        id=request_id,
        ok=error is None,
        value=value if error is None else None,
        error=_failure_text(error) if error is not None else None,
    )
    failure = None
    try:
        encoded = json.dumps(reply, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        failure = "Application reply could not be serialized: " + _failure_text(error)
        encoded = json.dumps(dict(id=request_id, ok=False, value=None, error=failure))
    return "window.ownerReply(" + encoded + "); null", failure


def _is_session_snapshot(value):
    """Only a session packet can supply acquisition instructions to the native host.

    Format availability also has a ``batch`` key, describing archive formats.
    Dataset binding metadata distinguishes an actual session snapshot from that
    unrelated reply before any native instruction callback is scheduled.
    """
    return (
        isinstance(value, dict)
        and isinstance(value.get("dataset_binding"), dict)
        and "batch" in value
    )


def _instruction_command(state, key, response):
    """Select a response only for the exact instruction still owned by the batch."""
    instruction = state.get("instructions") if state else None
    if (
        not instruction
        or state.get("operation_id") != key[0]
        or (instruction.get("operation_id"), instruction.get("request_id")) != key
    ):
        raise ValueError("This instruction is no longer current; refresh the workspace")
    if response is not None:
        if response not in instruction["allowed_responses"]:
            raise ValueError("This response is not offered by the current instruction")
        return "respond", dict(operation_id=key[0], request_id=key[1], response=response)
    active = next((item for item in state["operations"] if item["operation_id"] == key[0]), None)
    if active is None:
        raise ValueError("The current operation has no retained command; refresh the workspace")
    return "cancel", dict(command_id=active["command_id"])


def _stop_demo_preload(stop, active_id, requested_id):
    """Stop only the identified native load, immediately from the UI thread."""
    if stop is None or active_id is None or requested_id != active_id:
        raise ValueError("This Demo load is no longer active")
    stop.set()


def _require_completed_close(outcome):
    """Admission alone cannot confirm a completed owner-close operation."""
    if outcome["admission"] != "admitted" or outcome["state"] != "completed":
        raise ValueError(outcome["reason"] or "The batch did not complete closing")


def _close_owner(session, action, *, batch_id, generation, discard_token):
    """Complete the exact native confirmation before allowing process termination."""
    if action not in {"close", "abandon", "confirm_process_exit"}:
        raise ValueError("Unknown native close action")
    payload = {"discard_token": discard_token}
    if action == "abandon":
        payload["reason"] = "Operator chose native abandonment after terminal unconfirmed cleanup"
    if action == "confirm_process_exit":
        outcome = session.confirm_process_exit(
            batch_id=batch_id,
            generation=generation,
            discard_token=discard_token,
        )
    else:
        outcome = session.batch.command(
            str(uuid4()),
            generation,
            action,
            payload,
            batch_id=batch_id,
        )
    _require_completed_close(outcome)
    if action == "confirm_process_exit" and outcome.get("effect") != "process_exit_acknowledged":
        raise ValueError("The batch did not acknowledge this process exit")
    return outcome


def _unconfirmed_shutdown_text(outcome):
    """Disclose unconfirmed saving without guessing which bytes reached disk."""
    if outcome.get("shutdown_saved") is not False:
        return None
    return (
        "Saving the abandonment record is unconfirmed: "
        f"{outcome.get('reason') or 'no reason was reported'}. "
        "Some files may already contain the abandonment and cleanup facts. "
        "The unresolved in-memory work covered by your confirmation has been discarded. "
        "Saved records remain available for review. The application will quit."
    )


def _instruction_text(instruction, *, synthetic=False, guidance=None):
    """Show the current decoded setup and the backend's actual response budget."""
    lines = [
        (
            "Simulated instruction. No physical instrument is connected."
            if synthetic
            else "Complete the instrument preparation below before continuing."
        ),
    ]
    calibration = instruction.get("calibration")
    position = instruction.get("position")
    if instruction["message"] != instruction.get("kind"):
        lines.append(instruction["message"])
    elif not calibration and not position:
        lines.append(guidance or instruction["message"].replace("_", " ").capitalize())
    if calibration:
        lines.append(calibration["instruction"])
        setup = calibration.get("setup")
        if setup:
            lines.append(setup["text"])
        if calibration.get("reference_id"):
            lines.append(f"{calibration['reference_id_holds']}: {calibration['reference_id']}")
        if calibration.get("display_level") is not None:
            lines.append(f"Requested display level: {calibration['display_level']} of full white")
        lines.extend(calibration.get("warnings", []))
    if position:
        purpose = {"calibrate": "calibration", "measure": "measurement"}.get(
            position["stage"],
            position["stage"].replace("_", " "),
        )
        lines.append(f"Required position for {purpose}: {position['needed'].replace('_', ' ')}")
        if position.get("driver_text"):
            lines.append(position["driver_text"])
    lines.append(
        f"Time limit: {instruction['response_timeout_seconds']:g} seconds from the request. "
        "Some time may have elapsed before this prompt appeared."
    )
    return "\n".join(lines)


def _instruction_alert(instruction, *, synthetic=False, guidance=None):
    """Show actual provenance and only offered responses, without a Return default."""
    alert = Dialog()
    title = (
        "Prepare calibration"
        if instruction.get("calibration")
        else "Position instrument"
        if instruction.get("position") or instruction["kind"] == "sample_position"
        else "Instrument instruction"
    )
    alert.title = ("Demo: " if synthetic else "") + title
    alert.text = _instruction_text(
        instruction,
        synthetic=synthetic,
        guidance=guidance,
    )
    if instruction.get("calibration"):
        label = "Confirm simulated calibration"
    elif instruction.get("position") or instruction["kind"] == "sample_position":
        label = "Confirm simulated position"
    else:
        label = "Confirm simulated instruction"
    if not synthetic:
        label = (
            "Preparation complete"
            if instruction.get("calibration")
            else "Position ready"
            if instruction.get("position") or instruction["kind"] == "sample_position"
            else "Continue"
        )
    for response in instruction["allowed_responses"]:
        title = (
            label
            if response == "accept"
            else "Cancel operation"
            if response == "cancel"
            else response.replace("_", " ").capitalize()
        )
        alert.buttons.append(title)
    if "cancel" not in instruction["allowed_responses"]:
        alert.buttons.append("Cancel operation")
    alert.cancel = alert.buttons.index("Cancel operation")
    return alert


def _discard_text(summary, uncertain, save_reason=None):
    """Disclose every unsaved category covered by the exact discard token.

    A failed save is unresolved even when every listed category is empty: a
    synchronization failure after the manifest was replaced leaves no listed
    difference, yet Close still discards what the save did not confirm.
    """
    dirty_count = sum(draft["dirty"] for draft in summary["annotation_drafts"])
    companions = summary["companions"]
    retained_files = (
        ""
        if not companions
        else "\nRetained automatic files:\n"
        + "\n".join(
            f"{item['format'].upper()}: {item['path']} "
            + (
                f"({item['payload_bytes']} bytes)"
                if item["payload_bytes"] is not None
                else "(no prepared bytes)"
            )
            + "\n"
            f"Source SHA-256: {item['source_manifest_sha256']}\n"
            + (
                f"Prepared file SHA-256: {item['payload_sha256']}"
                if item["payload_sha256"]
                else "Preparation or saving is unresolved"
            )
            + (f"\nReason: {item['reason']}" if item.get("reason") else "")
            for item in companions
        )
        + "\n"
    )
    chart = summary.get("chart")
    diagnostics = summary.get("native_diagnostics")
    auxiliary = ""
    if chart is not None:
        auxiliary += "\nUnsaved chart state and selections will be discarded.\n"
    if diagnostics is not None:
        auxiliary += "\nUnresolved native diagnostic logs/settings:\n"
        if diagnostics["settings"] is not None:
            auxiliary += "Unsaved diagnostic settings.\n"
        for item in diagnostics["captures"]:
            auxiliary += f"Retained diagnostic: {item['bytes']} bytes, SHA-256 {item['sha256']}\n"
        for category in ("retention_reasons", "publication_reasons"):
            for reason in diagnostics.get(category, {}).values():
                auxiliary += reason + "\n"
    return (
        f"{len(summary['operations'])} unsaved outcomes, "
        f"{len(summary['annotations'])} unsaved annotations, "
        f"{dirty_count} unapplied annotation drafts, "
        f"{int(summary['drafts'] is not None)} unsaved sample drafts, "
        f"{int(summary['comparison'] is not None)} unsaved comparison context "
        "(reference and white choices), "
        f"{len(summary['unknown_outcomes'])} unknown outcomes. "
        f"{len(companions)} automatic file{'s' if len(companions) != 1 else ''} awaiting save. "
        "Saved files remain available for read-only review. "
        + (
            f"The last batch save did not complete: {save_reason}. Close discards what "
            "that save did not confirm; Retry save keeps it. "
            if save_reason
            else ""
        )
        + (
            "Software cleanup is unconfirmed. Abandonment requires process shutdown; "
            "it does not prove a device stop. "
            if uncertain
            else ""
        )
        + retained_files
        + auxiliary
        + "Close verifies this exact summary again; unresolved work is discarded only if accepted."
    )


def _startup_failure(reason):
    """Explain a launch refusal without constructing an acquisition owner."""
    try:
        from .desktop_native import startup_failure

        startup_failure(reason)
    except ImportError:
        pass
    raise SystemExit(reason)


# Nothing updates the window title after startup, and one host serves Demo,
# Argyll and direct-serial sources: it names the application, never a source.
_WINDOW_TITLE = "Color Measurement"


class DesktopHost:
    """One session controller for the portable native presentation.

    Requests serialize on the owner executor. Demo and direct initialization use
    their separate executor, keeping snapshots and cancellation responsive.
    Every callback and native dialog returns to the UI thread through dispatch.
    """

    def __init__(
        self,
        *,
        ui,
        session,
        assets,
        executor,
        demo_executor,
        dispatch_main,
        instrument_software=None,
    ):
        self.ui, self.session, self.assets = ui, session, assets
        self.executor, self.demo_executor = executor, demo_executor
        self.dispatch_main, self.instrument_software = dispatch_main, instrument_software
        self.close_phase = "idle"
        self.direct_ports = {}
        self.renderer_crashes = []
        self.confirmed = self.stopping = self.presenting_instruction = False
        self.instruction_key = self.demo_stop = self.demo_future = self.demo_load_id = None

    def reply_value(self, request_id, value=None, error=None):
        script, failure = _reply_script(request_id, value, error)

        def delivered(result, delivery_error):
            if delivery_error:
                self.show_failure(
                    "A workspace reply was not delivered",
                    _failure_text(delivery_error)
                    + "\nThe batch may have completed the command. Keep the application open "
                    "and reload the workspace to reconcile its retained state.",
                )
            elif failure is None and error is None and _is_session_snapshot(value):
                # Let the bridge return resolve before presenting a modal instruction.
                self.dispatch_main(lambda: self.show_instruction(value["batch"]))

        try:
            self.ui.evaluate(script, delivered)
        except Exception as delivery_error:
            delivered(None, delivery_error)
        if failure is not None:
            self.show_failure("Application reply could not be serialized", failure)

    def show_failure(self, title, reason):
        self.ui.ask(_failure_alert(title, reason))

    def show_instruction(self, state):
        if self.demo_stop is not None:
            # A Demo load acknowledges its own instructions; a poll reply that
            # lands while one is pending must not present it natively, or the
            # confirmation answers an instruction the load has consumed.
            return
        instruction = state["instructions"] if state else None
        if instruction is None:
            self.instruction_key = None
            return
        key = instruction["operation_id"], instruction["request_id"]
        if self.presenting_instruction or self.instruction_key == key:
            return
        self.instruction_key = key
        self.presenting_instruction = True
        guidance = next(
            (
                item["detail"]
                for item in state["descriptor"].get("instructions", [])
                if item["instruction"] == instruction["kind"]
            ),
            None,
        )
        alert = _instruction_alert(
            instruction, synthetic=state["descriptor"]["synthetic"], guidance=guidance
        )
        try:
            index = self.ui.ask(alert)
            responses = instruction["allowed_responses"]
            response = responses[index] if 0 <= index < len(responses) else None
        finally:
            self.presenting_instruction = False

        def respond():
            current = self.session.batch.snapshot()
            action, payload = _instruction_command(current, key, response)
            outcome = self.session.request(
                "command",
                dict(
                    command_id=str(uuid4()),
                    generation=current["generation"],
                    batch_id=current["batch_id"],
                    action=action,
                    payload=payload,
                ),
            )
            if outcome["admission"] != "admitted":
                raise ValueError(outcome["reason"])

        def completed(future):
            try:
                future.result()
            except BaseException as error:

                def report(failure=error):
                    self.instruction_key = None
                    self.show_failure("Instruction response was not accepted", failure)

                self.dispatch_main(report)

        self.executor.submit(respond).add_done_callback(completed)

    def run_request(self, request_id, callback, pool=None):
        def completed(future):
            try:
                value, error = future.result(), None
            except BaseException as failure:
                value, error = None, failure
            self.dispatch_main(lambda: self.reply_value(request_id, value, error))

        future = (pool or self.executor).submit(callback)
        future.add_done_callback(completed)
        return future

    def choose_folder(self, creating, saved_data=False):
        return self.ui.choose_folder(creating, saved_data=saved_data)

    def choose_export(self, format):
        if format not in {"json", "csv", "mat", "sp"}:
            raise ValueError("Choose JSON, CSV, MAT or SPECT")
        return self.ui.file_path(
            "Save native SPECT to a new file; choose its JSON companion next"
            if format == "sp"
            else "Save export to a new file",
            extensions=[format],
            save=True,
            name="Measurement export." + format,
        )

    def request_close(self):
        if self.close_phase == "idle":
            self.close_phase = "renderer"
            self.ui.evaluate(
                "window.requestOwnerClose(); null",
                lambda value, error: self.close_failed(error, renderer=True),
            )

    def close_failed(self, error, renderer=False):
        if error:
            self.close_phase = "idle"
            if renderer:
                choice = self.ui.ask(
                    Dialog(
                        "The workspace could not finish its pending changes",
                        _failure_text(error) + "\nReload to recover the workspace. If it remains "
                        "unavailable, native close can inspect the retained batch. Text that never "
                        "reached the application cannot be recovered. Closing still requires "
                        "confirmation of the exact retained work and cleanup state.",
                        ["Keep application open", "Reload workspace", "Inspect batch and close…"],
                    )
                )
                if choice == 1:
                    self.ui.reload()
                elif choice == 2:
                    self.close_phase = "native"
                    self.close_after_flush()
            else:
                self.show_failure(
                    "The application could not close",
                    _failure_text(error)
                    + "\nKeep the application open. Try Close again after the problem is resolved.",
                )

    def close_after_flush(self):
        def inspect():
            return _close_summary(self.session)

        def inspected(future):
            try:
                summary = future.result()
            except BaseException as error:
                self.dispatch_main(lambda failure=error: self.close_failed(failure))
                return
            self.dispatch_main(lambda: self.confirm_close(summary))

        self.executor.submit(inspect).add_done_callback(inspected)

    def confirm_close(self, summary):
        pending = summary.get("pending_exports", []) if summary else []
        if pending:
            item = pending[0]
            alert = Dialog()
            alert.title = "Export saving needs attention"
            alert.text = (
                f"{len(pending)} prepared exports remain in this application. "
                "Their exact data can be saved to a new destination.\n"
                f"This export: {item['format'].upper()} · {item['reason']}\n"
                + (
                    f"An earlier file was published at {item['attempted_path']}; "
                    "its final synchronization is unconfirmed. Retry saves identical "
                    "retained data to a new file.\n"
                    if item["published"]
                    else "Publication was not confirmed. Retry saves the retained data "
                    "to a new file.\n"
                )
                + "Discard removes only this prepared export; saved source files remain."
            )
            alert.buttons.append("Keep working")
            alert.buttons.append("Save this export to a new file…")
            alert.buttons.append("Discard this export and continue closing")
            choice = self.ui.ask(alert)
            if choice not in {1, 2}:
                self.close_phase = "idle"
                return
            outputs = (
                _choose_record_outputs(self, item["native_format"] or item["format"])
                if choice == 1
                else None
            )
            if choice == 1 and outputs is None:
                self.close_phase = "idle"
                return

            def discard():
                if choice == 1:
                    path, canonical = outputs
                    if item["retry_method"] == "retry_record_export":
                        self.session.retry_record_export(
                            path,
                            recovery_id=item["recovery_id"],
                            canonical_path=canonical,
                        )
                    else:
                        self.session.retry_analysis_export(path, recovery_id=item["recovery_id"])
                else:
                    self.session.discard_analysis_export(
                        recovery_id=item["recovery_id"],
                        payload_sha256=item["payload_sha256"],
                    )

            def discarded(future):
                try:
                    future.result()
                except BaseException as error:
                    self.dispatch_main(lambda failure=error: self.close_failed(failure))
                else:
                    self.dispatch_main(self.close_after_flush)

            self.executor.submit(discard).add_done_callback(discarded)
            return
        state = summary["state"] if summary is not None else None
        summary = summary["contents"] if summary is not None else None
        uncertain = bool(state and state["cleanup_reason"])
        process_exit = bool(state and state["process_exit_ready"])
        if summary is not None and summary["active_command_id"] and not state["operation_terminal"]:
            alert = Dialog()
            alert.title = "Wait for the current operation before closing"
            alert.text = "Cancel in the workspace, then wait for retained stop and saving facts."
            self.ui.ask(alert)
            self.close_phase = "idle"
            return
        if summary is not None:
            alert = Dialog()
            alert.title = (
                "Quit without confirming folder cleanup?"
                if process_exit
                else "Quit with instrument cleanup unconfirmed?"
                if uncertain
                else "Close this batch?"
            )
            unresolved = state["save_reason"] is not None or any(
                (
                    summary["operations"],
                    summary["annotations"],
                    summary["drafts"] is not None,
                    summary["comparison"] is not None,
                    summary.get("chart") is not None,
                    summary.get("native_diagnostics") is not None,
                    summary["companions"],
                    summary["unknown_outcomes"],
                    any(draft["dirty"] for draft in summary["annotation_drafts"]),
                )
            )
            alert.text = (
                "All readings and edits are saved. Saved files remain available for review."
                if not (
                    unresolved or uncertain or process_exit or state["software_stopped"] is not True
                )
                else _discard_text(
                    summary,
                    state["software_stopped"] is not True,
                    state["save_reason"],
                )
                + (
                    "\nFolder publication cleanup remains unconfirmed. Quit discards the "
                    "unresolved in-memory work listed above. It does not save a shutdown "
                    "record, release the unresolved folder lock or confirm cleanup."
                    if process_exit
                    else ""
                )
            )
            alert.buttons.append("Keep working")
            alert.buttons.append(
                "Discard unresolved work and quit"
                if process_exit
                else "Abandon and quit"
                if uncertain
                else "Close and discard unresolved work"
                if unresolved
                else "Close"
            )
            if self.ui.ask(alert) != 1:
                self.close_phase = "idle"
                return

            def close():
                action = (
                    "confirm_process_exit" if process_exit else "abandon" if uncertain else "close"
                )
                return _close_owner(
                    self.session,
                    action,
                    batch_id=state["batch_id"],
                    generation=state["generation"],
                    discard_token=summary["token"],
                )

            def closed(future):
                try:
                    unconfirmed = _unconfirmed_shutdown_text(future.result())
                except BaseException as error:
                    self.dispatch_main(lambda failure=error: self.close_failed(failure))
                    return
                self.dispatch_main(lambda: self.quit_after_disclosure(unconfirmed))

            self.executor.submit(close).add_done_callback(closed)
        else:
            self.stop()

    def quit_after_disclosure(self, unconfirmed):
        # The owner is already closed; what remains is to say when the
        # abandonment saving is unconfirmed before the process ends.
        if unconfirmed is not None:
            self.ui.ask(_failure_alert("Abandonment saving is unconfirmed", unconfirmed))
        self.stop()

    def stop(self):
        # This follows the owner executor's admitted close (or empty-owner
        # inspection). A Demo load still holding its worker is told to stop
        # and waited for, then the owner executor drains. The UI remains responsive until
        # admitted setup and cleanup finish; event-loop teardown cannot supply
        # this shutdown.
        if self.stopping:
            return
        self.stopping = True
        stop_load = self.demo_stop
        if stop_load is not None:
            stop_load.set()
        demo_future = self.demo_future

        def pending_sources():
            # Queue behind admitted setup commands so their shared Future is
            # visible before shutdown captures it. Do not wait on this executor:
            # snapshots and outcome reconciliation must remain responsive.
            with self.session._mutex:
                equipment_future = self.session._equipment_setup._future
                active = self.session._equipment_setup._active
                if active is not None and not active["registration_committed"]:
                    self.session._equipment_setup.cancel(active["command_id"])
            return demo_future, equipment_future

        def released(future):
            try:
                report = future.result()
            except BaseException as error:
                report = dict(
                    failures=[
                        dict(
                            model="Direct connection",
                            reason=_failure_text(error),
                        )
                    ]
                )
            self.dispatch_main(lambda: self.finish_stop(report))

        def inspected(future):
            try:
                demo_job, equipment_job = future.result()
            except BaseException:
                released(future)
                return

            def await_sources():
                try:
                    jobs = tuple(job for job in (demo_job, equipment_job) if job is not None)
                    _, pending = wait(jobs, timeout=30)
                    if pending:
                        report = dict(
                            demo_pending=demo_job in pending,
                            equipment_pending=equipment_job in pending,
                            failures=[],
                        )
                        self.dispatch_main(lambda: self.finish_stop(report))
                    else:
                        self.executor.submit(self.session.close_direct_sources).add_done_callback(
                            released
                        )
                except BaseException as error:
                    report = dict(
                        failures=[
                            dict(
                                model="Connection setup",
                                reason=_failure_text(error),
                            )
                        ]
                    )
                    self.dispatch_main(lambda: self.finish_stop(report))

            # One bounded shutdown waiter owns no instrument I/O. The existing
            # setup worker retains initialization; cleanup returns to its owner
            # executor only after both setup and Demo work have ended.
            Thread(target=await_sources, name="desktop-shutdown-wait", daemon=True).start()

        self.executor.submit(pending_sources).add_done_callback(inspected)

    def finish_stop(self, report):
        if report.get("demo_pending") or report.get("equipment_pending"):
            alert = Dialog()
            equipment = report.get("equipment_pending")
            alert.title = (
                "Instrument initialization has not stopped"
                if equipment
                else "Demo loading has not stopped"
            )
            alert.text = (
                "Initialization has not settled within 30 seconds. "
                if equipment
                else "The Demo worker has not stopped within 30 seconds. "
            ) + "Keep the application open or retry after the worker settles."
            alert.buttons.append("Keep application open")
            alert.buttons.append(
                "Retry stopping initialization" if equipment else "Retry stopping Demo"
            )
            choice = self.ui.ask(alert)
            self.stopping = False
            if choice == 1:
                self.stop()
            else:
                self.close_phase = "idle"
            return
        if report["failures"]:
            alert = Dialog()
            alert.title = "Direct connection cleanup is unconfirmed"
            alert.text = (
                "Batch shutdown was accepted. Connection cleanup remains unconfirmed. "
                "Quitting can release the host port without restoring the instrument.\n"
                + "\n".join(item["model"] + ": " + item["reason"] for item in report["failures"])
            )
            alert.buttons.append("Keep application open")
            alert.buttons.append("Retry connection cleanup")
            alert.buttons.append("Quit with cleanup unconfirmed")
            choice = self.ui.ask(alert)
            if choice == 1:
                self.stopping = False
                self.stop()
                return
            if choice != 2:
                self.stopping = False
                self.close_phase = "idle"
                return
        self.demo_executor.shutdown(wait=True)
        self.executor.shutdown(wait=True)
        self.confirmed = True
        self.ui.quit()

    def receive(self, body):
        """Accept a decoded envelope from the authenticated top-level Qt bridge."""
        request_id = "invalid"
        try:
            if isinstance(body, dict) and isinstance(body.get("id"), str) and len(body["id"]) <= 64:
                request_id = body["id"]
            request_id, method, params = _bridge_request(body)
            if self.instrument_software and self.instrument_software["packaged"]:
                if method == "choose_build_report":
                    raise ValueError("This application includes its instrument software")
                if not self.instrument_software["ready"] and method in {
                    "refresh_devices",
                    "connect_device",
                    "reopen_device",
                    "connect_direct_serial",
                    "reopen_direct_serial",
                    "choose_direct_port",
                }:
                    raise ValueError(self.instrument_software["error"])
            if self.stopping and method not in {"snapshot", "equipment_setup", "equipment_outcome"}:
                raise ValueError(
                    "Application close is releasing its owned connections; wait for the result"
                )
            if method == "copy_summary":
                if set(params) != {"text"}:
                    raise ValueError("Copy requires the displayed summary text")
                self.reply_value(request_id, _copy_summary(self.ui, params["text"]))
            elif method == "create_batch":
                if set(params) != {"name", "notes", "selection_id", "mode", "options"}:
                    raise ValueError("Batch setup requires source, mode, options, name and notes")
                folder = self.choose_folder(True)
                if folder is None:
                    self.reply_value(request_id, None)
                else:
                    self.run_request(request_id, lambda: self.session.create(folder, **params))
            elif method == "choose_build_report":
                if set(params) != {"setup_generation"}:
                    raise ValueError("Report selection requires the current setup generation")
                path = _choose_build_report(self.ui)
                if path is None:
                    self.reply_value(request_id, None)
                else:
                    self.run_request(
                        request_id,
                        lambda: self.session.select_build_report(
                            path, expected_setup_generation=params["setup_generation"]
                        ),
                    )
            elif method == "choose_science_material":
                if set(params) != {"binding_id", "generation", "dataset_revision", "role"}:
                    raise ValueError(
                        "Scientific material selection requires the current dataset and role"
                    )
                path = _choose_science_material(self.ui, params["role"])
                if path is None:
                    self.reply_value(request_id, None)
                else:
                    self.run_request(
                        request_id, lambda: self.session.stage_science_material(path, **params)
                    )
            elif method in {"choose_correction", "choose_current_correction"}:
                current = method == "choose_current_correction"
                fields = (
                    {"batch_id", "generation", "context_id", "draft_id", "version", "options"}
                    if current
                    else {"selection_id", "mode", "capability_id", "settings_generation", "options"}
                )
                if set(params) != fields:
                    raise ValueError("Correction selection requires the exact current settings")
                path = _choose_correction(self.ui)
                if path is None:
                    self.reply_value(request_id, None)
                else:
                    stage = (
                        self.session.stage_current_correction
                        if current
                        else self.session.stage_correction
                    )
                    self.run_request(request_id, lambda: stage(path, **params))
            elif method in {"stage_chart_target", "choose_chart_source"}:
                fields = {"batch_id", "generation", "context_id"}
                if method == "stage_chart_target":
                    fields.add("strips_as")
                if set(params) != fields:
                    raise ValueError("Chart input selection requires the exact live binding")
                path = _choose_chart_input(self.ui, target=method == "stage_chart_target")
                if path is None:
                    self.reply_value(request_id, None)
                elif method == "stage_chart_target":
                    self.run_request(
                        request_id, lambda: self.session.stage_chart_target(path, **params)
                    )
                else:
                    self.run_request(
                        request_id, lambda: self.session.stage_chart_continuation(path, **params)
                    )
            elif method == "preload_demo":
                if set(params) != {"batch_id", "generation", "count", "load_id"}:
                    raise ValueError("Demo loading requires its binding, count and identity")
                load_id = params["load_id"]
                if not isinstance(load_id, str) or not 1 <= len(load_id) <= 64:
                    raise ValueError("Invalid Demo loading identity")
                if self.demo_stop is not None:
                    raise ValueError("A Demo load is already active")
                stop = self.demo_stop = Event()
                self.demo_load_id = params["load_id"]

                def preload():
                    try:
                        return self.session.preload_demo(
                            batch_id=params["batch_id"],
                            generation=params["generation"],
                            count=params["count"],
                            stop_event=stop,
                        )
                    finally:
                        self.demo_stop = None
                        self.demo_load_id = None

                # The load holds its own worker for the whole load; the owner
                # executor keeps answering the snapshot poll and every other
                # request meanwhile.
                self.demo_future = self.run_request(request_id, preload, self.demo_executor)
            elif method == "stop_demo_preload":
                if set(params) != {"load_id"}:
                    raise ValueError("Stopping Demo loading requires its identity")
                # Do not queue this behind the load on either executor.
                _stop_demo_preload(self.demo_stop, self.demo_load_id, params["load_id"])
                self.reply_value(request_id, None)
            elif method == "another_folder":
                if params:
                    raise ValueError("Another folder requires the native chooser")
                folder = self.choose_folder(True)
                if folder is None:
                    self.reply_value(request_id, None)
                else:
                    self.run_request(request_id, lambda: self.session.another_folder(folder))
            elif method == "choose_direct_port":
                if (
                    set(params) != {"setup_generation"}
                    or type(params["setup_generation"]) is not int
                ):
                    raise ValueError("Port selection requires the displayed equipment generation")
                if params["setup_generation"] != self.session._equipment_setup.generation:
                    raise ValueError("Equipment setup changed. Refresh before choosing a port.")
                equipment_future = self.session._equipment_setup._future
                if equipment_future is not None and not equipment_future.done():
                    raise ValueError(
                        "Initialization is running. Cancel it or wait before choosing."
                    )
                port = _choose_serial_port(self.ui)
                if port is None:
                    self.reply_value(request_id, None)
                else:
                    token = str(uuid4())
                    self.direct_ports.clear()
                    self.direct_ports[token] = dict(
                        port=port,
                        generation=params["setup_generation"],
                    )
                    self.reply_value(
                        request_id,
                        dict(
                            port_token=token,
                            label=port,
                            setup_generation=params["setup_generation"],
                        ),
                    )
            elif method in {"connect_direct_serial", "reopen_direct_serial"}:
                fields = {
                    "command_id",
                    "setup_generation",
                    "port_token",
                    "accept_disclosed_initialization",
                    "response_timeout",
                    "measurement_timeout",
                }
                fields |= (
                    {"model", "expected_serial"}
                    if method == "connect_direct_serial"
                    else {"binding_id", "binding_generation"}
                )
                if set(params) != fields:
                    raise ValueError(
                        "Direct initialization requires the native port and exact intent"
                    )
                held = self.direct_ports.get(params["port_token"])
                if held is None or held["generation"] != params["setup_generation"]:
                    raise ValueError("The native port selection changed. Choose its port again.")
                intent = {key: value for key, value in params.items() if key != "port_token"}
                if "intent" in held and held["intent"] != dict(method=method, **intent):
                    raise ValueError("This port choice already belongs to another initialization.")
                held["intent"] = dict(method=method, **intent)

                def initialize():
                    return getattr(self.session, method)(
                        held["port"],
                        **intent,
                        executor=self.demo_executor,
                    )

                self.run_request(request_id, initialize)
            elif method == "open_record":
                if set(params) != {"binding_id", "generation", "format"}:
                    raise ValueError("Opening a record requires its format and displayed binding")
                path = _choose_record_input(self.ui, params["format"])
                if path is None:
                    self.reply_value(request_id, None)
                else:
                    fence = dict(
                        expected_binding_id=params["binding_id"],
                        expected_generation=params["generation"],
                    )
                    self.run_request(
                        request_id,
                        lambda: self.session.open_record(
                            path,
                            format=params["format"],
                            **fence,
                        ),
                    )
            elif method in {"export_record", "retry_record_export"}:
                fields = (
                    {"recovery_id"}
                    if method == "retry_record_export"
                    else {"binding_id", "generation", "dataset_revision", "format", "reading_ref"}
                )
                if set(params) != fields:
                    raise ValueError("Record export requires its exact source or retained payload")
                if method == "retry_record_export":
                    pending = next(
                        (
                            item
                            for item in self.session.export_recovery()
                            if item["recovery_id"] == params["recovery_id"]
                        ),
                        None,
                    )
                    if pending is None or pending["retry_method"] != method:
                        raise ValueError("This retained native export is no longer pending")
                    format = pending["native_format"]
                else:
                    format = params["format"]
                outputs = _choose_record_outputs(self, format)
                if outputs is None:
                    self.reply_value(request_id, None)
                else:
                    path, canonical = outputs
                    self.run_request(
                        request_id,
                        lambda: getattr(self.session, method)(
                            path,
                            canonical_path=canonical,
                            **params,
                        ),
                    )
            elif method in {"export_analysis", "export_batch_mat", "retry_analysis_export"}:
                fields = (
                    {"recovery_id"}
                    if method == "retry_analysis_export"
                    else {"binding_id", "generation"}
                    if method == "export_batch_mat"
                    else {
                        "binding_id",
                        "generation",
                        "dataset_revision",
                        "comparison_version",
                        "scope",
                        "selected",
                        "scale",
                        "format",
                        "derivation",
                    }
                )
                if set(params) != fields:
                    raise ValueError("Export requires its exact source or retained payload")
                if method == "retry_analysis_export":
                    pending = next(
                        (
                            item
                            for item in self.session.export_recovery()
                            if item["recovery_id"] == params["recovery_id"]
                        ),
                        None,
                    )
                    if pending is None or pending["retry_method"] != method:
                        raise ValueError("This prepared export is no longer pending")
                    format = pending["format"]
                else:
                    format = "mat" if method == "export_batch_mat" else params["format"]
                path = self.choose_export(format)
                if path is None:
                    self.reply_value(request_id, None)
                else:
                    self.run_request(
                        request_id,
                        lambda: getattr(self.session, method)(path, **params),
                    )
            elif method in {"open_saved", "activate_live"}:
                if set(params) != {"binding_id", "generation"}:
                    raise ValueError("Source switching requires the displayed dataset binding")
                fence = dict(
                    expected_binding_id=params["binding_id"],
                    expected_generation=params["generation"],
                )
                if method == "activate_live":
                    self.run_request(request_id, lambda: self.session.activate_live(**fence))
                else:
                    folder = self.choose_folder(False, saved_data=True)
                    if folder is None:
                        self.reply_value(request_id, None)
                    else:
                        self.run_request(
                            request_id,
                            lambda: self.session.open_saved(folder, **fence),
                        )
            elif method == "review":
                if params:
                    raise ValueError("Review requires the native chooser")
                folder = self.choose_folder(False)
                if folder is None:
                    self.reply_value(request_id, None)
                else:
                    self.run_request(request_id, lambda: self.session.review(folder))
            elif method == "cancel_close":
                if params:
                    raise ValueError("Cancel close requires no parameters")
                if self.close_phase == "renderer":
                    self.close_phase = "idle"
                self.reply_value(request_id, None)
            elif method == "request_close":
                if params:
                    raise ValueError("Close requires a fresh batch summary")
                self.reply_value(request_id, None)
                if self.close_phase != "native":
                    self.close_phase = "native"
                    self.close_after_flush()
            elif method == "equipment_setup":

                def equipment_setup():
                    value = self.session.request(method, params)
                    value["instrument_software"] = self.instrument_software
                    return value

                self.run_request(request_id, equipment_setup)
            else:
                self.run_request(request_id, lambda: self.session.request(method, params))
        except BaseException as error:
            self.reply_value(request_id, error=error)

    def renderer_terminated(self):
        # Existing owner/drainer continues. A replacement page binds a new generation.
        if self.close_phase == "renderer":
            self.close_phase = "idle"
        now = time.monotonic()
        self.renderer_crashes = [stamp for stamp in self.renderer_crashes if now - stamp < 30]
        if len(self.renderer_crashes) >= 2:
            self.show_failure(
                "The workspace renderer keeps stopping",
                "Automatic reload stopped after repeated failures. The retained batch remains "
                "in this application. Use Reload workspace to retry, or Close for native "
                "inspection and confirmation of retained work. Unsent page text may be lost.",
            )
            return
        self.renderer_crashes.append(now)
        self.ui.reload()


def main():
    assets = Path(__file__).with_name("desktop_assets").resolve()
    try:
        desktop_assets_manifest.check_renderer(
            assets, desktop_assets_manifest.renderer_sources(assets)
        )
        desktop_assets_manifest.check_reference(assets)
    except (OSError, ValueError) as error:
        _startup_failure(f"Bundled desktop assets failed freshness checks: {error}")
    try:
        from .desktop_native import run
    except ImportError as error:
        raise SystemExit("Install color-measurement[app] to run the desktop host") from error
    return run(assets)


if __name__ == "__main__":
    main()
