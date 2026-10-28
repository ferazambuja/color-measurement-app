"""What is plugged in, what it is called, and what it can do.

This is the step that was missing between "an instrument exists" and "a UI can
offer it". Everything else in the generic path assumed the caller already knew
which instrument and which path index; nothing answered the first question a
user interface has to ask.

The chain, and every step of it is device-agnostic
--------------------------------------------------
1. **Enumerate.** ``NativeDescriptionClient.list_candidates_json()`` lists OS
   paths with the ``dtype`` the OS metadata already identifies. It does not
   initialize instruments; descriptor access depends on the platform.
2. **Name.** ``dtype`` is an ``instType`` ORDINAL, and this project deliberately
   keeps no ordinal table in Python -- ``insttypes.h:29-69`` assigns the enum no
   explicit values, so an ordinal is a position in a list upstream may reorder.
   The ordering therefore comes from the build's own catalog, which
   ``driver_manifest.parse_instrument_types`` derives from the pinned header, and
   ``instrument_display_name`` supplies the canonical ``inst_name()`` label.
   ``instrument_binding_name`` separately resolves the alias ``inst_enum()`` accepts.
3. **Describe.** ``NativeDescriptionClient.describe_json()`` initialises exactly
   the chosen candidate and reports its raw capability words and baseline
   ``check_mode`` results, with no acquisition or calibration.
4. **Offer.** These facts become an ``InstrumentFrontend`` descriptor. Advertised
   modes, initialized-state acceptance and implemented workflows remain distinct.

That is the same division ArgyllCMS uses. Its own tools enumerate with
``icompaths``, construct through ``new_inst()``, and ask ``capabilities()``; no
separate per-family application stack is required for that sequence.

Enumeration is explicit and platform-dependent
---------------------------------------------
``InstrumentBuild.attached()`` is the one call that enumerates, and a caller makes
it deliberately -- a "Refresh devices" button, not a background poll, and never
during a software check. On macOS all three transports use IORegistry metadata:

* USB: ``usbio_ox.c:34-98`` matches ``kIOUSBDeviceClassName`` and reads vendor,
  product, configuration and location with ``IORegistryEntryCreateCFProperty``.
  ``USBDeviceOpenSeize`` appears only in ``usb_open_port`` (``usbio_ox.c:379``).
* HID: ``hidio.c:288-297``, with upstream's own comment on the call --
  ``/* Enumerate the devices (doesn't seem to have to open) */``.
* Serial: ``icoms_ux.c`` matches ``kIOSerialBSDServiceValue``, the same way.

Linux instead opens USB device nodes to read descriptors (``usbio_lx.c:65-77``);
Windows libusb0 takes query handles (``usbio_w0.c:99``). Do not infer a portable
no-open promise from macOS enumeration. Instrument initialization is separate:
``usb_open_port`` SEIZES the selected device on macOS, and
on failure upstream will try to kill the process holding it (``usbio_ox.c:379-387``,
which this project's no-termination regression patches out). One unit, one owner.

Describing is a second decision, per unit, with a visible cost
--------------------------------------------------------------
``describe`` initialises the instrument, and initialisation is not free. Five of
the thirty-eight change their visible state doing it: Huey, ColorHug, ColorHug2,
i1Disp3 and Smile all flash their indicator LEDs (``huey.c:1130`` and the eight-step
sequence at ``:1145-1168``; ``colorhug.c:653-666``; ``i1d3.c:3079``). They carry
``initialization_changes_indicator_leds``, and this module refuses to describe
them until the caller says so in as many words. A device list must therefore be
built from ``attached()`` alone -- never by opening every unit to see what it is.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .argyll_capability import instrument_binding_name, instrument_display_name
from .argyll_description import (
    UNAUTHORIZED_INITIALIZATION,
    validate_candidate,
    validate_native_identity,
)
from .mode_acceptance import validate_baseline_mode_checks
from .native_receipt import native_worker_name

if TYPE_CHECKING:  # For the annotation only; `bind` imports it when it is called.
    from .instrument_frontend import InstrumentFrontend

__all__ = [
    "REPORT_VARIABLE",
    "DiscoveredInstrument",
    "NativeRefusal",
    "InstrumentBuild",
    "InstrumentDescription",
    "discovered_instruments",
    "instrument_catalog",
]

#: Names the ``report.json`` of a native check run, with its workers beside it.
#: A client that has no better way to locate a build reads this rather than
#: inventing its own convention, so several clients on one machine agree.
REPORT_VARIABLE = "CM_ARGYLL_BUILD_REPORT"
_SIMULATED_SESSIONS = {
    "generic": ("generic-session-stub-worker", "executable_sha256"),
    "chart": ("generic-chart-stub-worker", "chart_executable_sha256"),
}

# icoms.h:88-100. A candidate's transport bits, for telling a user what kind of
# connection they are looking at without decoding a raw integer in the UI.
_TRANSPORTS = ((0x01, "serial"), (0x02, "usb"), (0x04, "hid"), (0x08, "bluetooth"),
               (0x100, "fastserial"))


def _opening_requirement(native_type, identified, assessed, refusal):
    if identified and assessed and refusal is None:
        return None
    if native_type == 0:
        return "expect"
    if refusal in UNAUTHORIZED_INITIALIZATION:
        return "not_permitted"
    if refusal is not None:
        return "accept_disclosed_initialization"
    if not assessed:
        return "accept_unassessed_initialization"
    return "expect"


@dataclass(frozen=True)
class DiscoveredInstrument:
    """One enumerated path, named if the OS metadata identified it."""

    index: int
    """1-based position in the enumeration, which is what ``path_index`` takes.

    Valid only for the enumeration it came from. Indices shift when the set of
    attached devices changes, and HID paths are added before USB ones
    (``icoms.c:489-497``), so a stored index may name a different device later.
    """

    native_type: int
    symbol: str | None
    name: str | None
    path: str
    transport: int
    #: Whether this build has READ this instrument's `init_inst` at all. False
    #: means unassessed, which is not the same as harmless -- see `attachable`.
    initialization_assessed: bool
    initialization_refusal: str | None

    @property
    def identified(self) -> bool:
        """Whether the OS metadata named the instrument.

        False for every serial path: ``icompaths_add_serial`` never writes
        ``dtype`` (``icoms.c:269-299``) and this build does not probe, so a
        SpectroScan or a DTP41 arrives unidentified and needs an explicit
        identification permission before it can be attached.
        """
        return self.native_type != 0 and self.name is not None

    @property
    def transports(self) -> tuple[str, ...]:
        return tuple(label for bit, label in _TRANSPORTS if self.transport & bit)

    @property
    def attachable(self) -> bool:
        """Whether this can be attached as it stands, with no further permission.

        Requires the initialisation to have been ASSESSED, not merely to carry no
        refusal. Those were the same test until now, so an instrument nobody had
        read the driver for was reported attachable on the strength of a silence
        -- the one fail-open left in the per-instrument tables. An unassessed
        instrument is not refused, it is reported unassessed, and the caller
        decides.
        """
        return (self.identified and self.initialization_assessed
                and self.initialization_refusal is None)

    @property
    def opening_requires(self) -> str | None:
        """What ``describe`` needs before it will open this unit, or None.

        ``not_permitted`` means nothing opens it: its disclosed initialization
        is one no acknowledgement authorizes (``UNAUTHORIZED_INITIALIZATION``).
        Otherwise it names the argument to pass --
        ``accept_disclosed_initialization``, ``accept_unassessed_initialization``
        -- or ``expect`` for an unidentified path, which is named with
        ``expect=`` (and ``allow_serial_identification`` unless its type is
        selected by type alone). Only the FIRST requirement: the expected type of
        an unidentified path may carry a disclosure of its own.

        An unidentified path's own facts are `instUnknown`'s, which the listing
        reports unassessed (``description.c:250-254``) because they describe the
        port, not a model. ``describe`` does not gate on them, as the worker does
        not (``description.c:224``), so they do not decide the first requirement.
        """
        return _opening_requirement(
            self.native_type, self.identified, self.initialization_assessed,
            self.initialization_refusal,
        )


def instrument_catalog(catalog_json: str) -> dict[int, str]:
    """``native_type`` -> ``instType`` symbol, from the build's own catalog.

    The ordinal lives in the build report rather than in this package on purpose:
    it is derived from the pinned ``insttypes.h`` by
    ``driver_manifest.parse_instrument_types``, so it moves when the pinned source
    moves and cannot silently disagree with the helper that reports it.
    """
    catalog = json.loads(catalog_json)
    rows = catalog.get("instrument_types") if isinstance(catalog, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("Catalog carries no instrument_types")
    mapping: dict[int, str] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or type(row.get("native_type")) is not int
            or not isinstance(row.get("enum"), str)
            or not row["enum"]
        ):
            raise ValueError("Malformed instrument_types row")
        if row["native_type"] in mapping:
            raise ValueError(f"Duplicate native type {row['native_type']}")
        mapping[row["native_type"]] = row["enum"]
    return mapping


def discovered_instruments(
    candidates_json: str, catalog_json: str
) -> tuple[DiscoveredInstrument, ...]:
    """Turn one enumeration into a list a user interface can show.

    An unidentified path is KEPT rather than dropped, with ``name`` None and
    ``identified`` False. Dropping it would hide every serial instrument --
    twelve of the thirty-eight, the SpectroScan among them -- from a UI that has
    no other way to learn they are there.
    """
    message = json.loads(candidates_json)
    if not isinstance(message, dict) or not isinstance(message.get("candidates"), list):
        raise ValueError("Enumeration carries no candidates")
    catalog = instrument_catalog(catalog_json)
    found = []
    for index, candidate in enumerate(message["candidates"], start=1):
        candidate = validate_candidate(candidate)
        native_type = candidate["dtype"]
        symbol = catalog.get(native_type) if native_type else None
        name = None
        if symbol is not None:
            try:
                name = instrument_display_name(symbol)
            except KeyError:
                # The build knows a type this package's table does not. Report it
                # as unidentified rather than inventing a name for it.
                symbol = None
        found.append(DiscoveredInstrument(
            index=index,
            native_type=native_type,
            symbol=symbol,
            name=name,
            path=candidate["path"],
            transport=candidate["transport"],
            initialization_assessed=candidate["initialization_assessed"],
            initialization_refusal=candidate["initialization_refusal"],
        ))
    return tuple(found)


class NativeRefusal(RuntimeError):
    """The helper answered, and the answer was a refusal. Kept as it named it.

    ``stage`` and ``native_code`` are the worker's own words. Nothing is mapped to
    a friendlier vocabulary here: the stage is what a client shows an operator and
    what a bug report needs, and inventing a second naming for it would make the
    two disagree.
    """

    def __init__(self, operation: str, error: dict):
        self.stage = error.get("stage")
        self.native_code = error.get("native_code")
        self.refusal_reason = error.get("refusal_reason")
        detail = f"{operation} refused at {self.stage} (native code {self.native_code})"
        if self.refusal_reason:
            detail += f": {self.refusal_reason}"
        super().__init__(detail)


def _payload(raw: str, operation: str) -> dict:
    """The data an operation envelope carries, or the refusal it carries instead.

    Every ``NativeDescriptionClient`` method returns the whole operation envelope
    -- status, error, data, events -- and the answer is one level in. Reading
    ``data`` without checking ``status`` would turn a refused enumeration into an
    empty device list, which is the one thing a picker must never show.
    """
    message = json.loads(raw)
    if message.get("status") != "completed" or message.get("error") is not None:
        raise NativeRefusal(operation, message.get("error") or {})
    data = message.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{operation} completed without data")
    return data


@dataclass(frozen=True)
class InstrumentDescription:
    """Initialized identity, capability words and baseline exact-mode checks.

    ``mode``/``trigger``/``filter`` are upstream's ``capabilities()`` triple --
    ``primary``, ``secondary``, ``tertiary`` on the wire -- kept raw. Nothing is
    decoded here; ``InstrumentFrontend`` derives the descriptor and acceptance
    statuses. Baseline checks precede mode configuration and cannot prove a
    later request is ready to measure.
    """

    discovered: DiscoveredInstrument
    initialized_type: int
    serial: str | None
    mode_capabilities: int
    trigger_capabilities: int
    filter_capabilities: int
    baseline_mode_checks: dict
    display_types: dict | None = None
    strip_policies: dict | None = None
    ambient_ccmx: bool = False
    #: The diagonal mean above which the driver types a reading ambient by its
    #: correction matrix (driver_policy.h), or None for a driver that types by route.
    ambient_matrix_threshold: float | None = None
    provenance: str = "physical"
    native_identity: dict | None = None
    backend_receipt: dict | None = None
    _native_identity_json: str = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if type(self.initialized_type) is not int or self.initialized_type <= 0:
            raise ValueError("Initialized type must be the observed positive native enum")
        object.__setattr__(self, "native_identity", deepcopy(validate_native_identity(
            self.native_identity, serial=self.serial)))
        # Nested dictionaries remain inspectable, but cannot replace the original
        # initialized observation with different, merely shape-valid facts.
        object.__setattr__(self, "_native_identity_json", json.dumps(
            self.native_identity, sort_keys=True, allow_nan=False))
        object.__setattr__(self, "backend_receipt", deepcopy(self.backend_receipt))
        object.__setattr__(self, "baseline_mode_checks", validate_baseline_mode_checks(
            self.baseline_mode_checks, mode_capabilities=self.mode_capabilities))

    @property
    def name(self) -> str:
        """The spelling ``inst_enum()`` accepts, which is what selects the driver."""
        if self.discovered.name is None:
            raise ValueError("A described instrument always carries a name")
        return self.discovered.name


def _gate(label, assessed, refusal, accept_disclosed, accept_unassessed) -> None:
    """Refuse to open an instrument whose initialisation was not agreed to.

    The two acknowledgements are separate on purpose. "This driver is known to
    flash an LED" and "nobody has read this driver" are different things to agree
    to, and one flag covering both would let a caller accepting the known effect
    silently accept the unknown one -- the fail-open `attachable` used to have.
    """
    if refusal in UNAUTHORIZED_INITIALIZATION:
        # Checked first, so no message suggests an acknowledgement that
        # describe_json then refuses.
        raise ValueError(
            f"{label} discloses {refusal}; nothing opens it, because no "
            "acknowledgement authorizes that")
    if refusal is not None and not accept_disclosed:
        raise ValueError(
            f"{label} discloses {refusal}; opening it will do that. "
            "Pass accept_disclosed_initialization=True")
    if not assessed and not accept_unassessed:
        raise ValueError(
            f"{label} has an unassessed initialisation: this build has not read its "
            "init_inst, so nothing is known about what opening it does. "
            "Pass accept_unassessed_initialization=True")


class InstrumentBuild:
    """The two workers one native build produced, verified together.

    Enumeration and acquisition are different processes -- the description worker
    lists and opens one unit to read its words, the session worker drives the
    measurement -- and an index is only meaningful across them if BOTH enumerate
    the same set. They do not automatically: ``usb_get_paths`` skips any device
    ``inst_usb_match`` answers ``instUnknown`` for (``usbio_ox.c:99``), and that
    filter is compiled from the linked driver set, so a narrow build enumerates
    ``0765:6009`` alone where a broad one enumerates every supported instrument.

    Binding both workers to ONE build report is what removes that failure mode.
    The report names each executable's SHA-256 and this checks both, so the pair
    cannot be mixed across builds, and a matching basename authenticates nothing.
    """

    READ_ALLOWANCE_MARGIN_SECONDS = 120.0

    def __init__(self, client, session_helper: Path, *, expected_helper_sha256: str,
                 timeout: float = 30.0):
        if (not isinstance(expected_helper_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_helper_sha256) is None):
            raise ValueError("A session worker SHA-256 is required")
        helper = Path(session_helper).resolve()
        report = getattr(client, "_build_report", None)
        if report is not None:
            simulation_root = getattr(client, "_simulation_source_root", None)
            simulated = simulation_root is not None
            link = report.get("generic_session" if simulated else "generic_worker_build_link")
            if not isinstance(link, dict) or link.get("status") != "passed":
                raise ValueError("The report carries no passed generic worker build")
            if not simulated and link.get("executed") is not False:
                raise ValueError("The generic worker in this report was executed")
            key = "executable_sha256"
            if simulated:
                selected = next(((filename, field) for filename, field in
                                 _SIMULATED_SESSIONS.values()
                                 if helper == client._build_report_path.parent
                                 / native_worker_name(filename, report=report)), None)
                if selected is None:
                    raise ValueError("The session worker is not beside the retained build report")
                filename, key = selected
                from .native_receipt import authenticate_stub

                authenticate_stub(helper, source_root=simulation_root,
                                  report_bytes=json.dumps(report).encode("utf-8"))
            else:
                filename = "generic-worker-never-run"
            filename = native_worker_name(filename, report=report)
            if helper != client._build_report_path.parent / filename:
                raise ValueError("The session worker is not beside the retained build report")
            if not helper.is_file():
                raise ValueError(f"No generic worker beside the report at {helper}")
            if (expected_helper_sha256 != link.get(key)
                    or hashlib.sha256(helper.read_bytes()).hexdigest()
                       != expected_helper_sha256):
                raise ValueError("The generic worker does not match the retained build report")
        self._client = client
        self._helper = helper
        self._timeout = timeout
        self._catalog = None
        self._helper_sha256 = expected_helper_sha256

    @classmethod
    def from_report(cls, report_path=None, *, timeout: float = 30.0, simulated=False,
                    source_root=None, scenario="success", simulated_session="generic"
                    ) -> InstrumentBuild:
        """Bind both workers from one retained build report.

        ``NativeDescriptionClient.from_build`` does the report validation and
        checks the description worker; this adds the session worker beside it,
        against the same report, so the two enumerations are the same enumeration.

        Omit ``report_path`` to take it from ``CM_ARGYLL_BUILD_REPORT``. There is
        no search and no built-in location: a build report is produced by a run
        the operator chose to make, lives wherever they put it, and pairs with the
        workers beside it. Guessing at one would be the same mistake as searching
        for a helper -- the thing it authenticates is the thing it is next to.
        """
        import os

        from .argyll_description import NativeDescriptionClient

        if type(simulated) is not bool:
            raise ValueError("simulated must be an explicit boolean")
        if (not isinstance(simulated_session, str)
                or simulated_session not in _SIMULATED_SESSIONS):
            raise ValueError("Select the generic or chart simulated session worker")
        if not simulated and (source_root is not None or scenario != "success"
                              or simulated_session != "generic"):
            raise ValueError("Source root and fixture selection require simulated=True")
        if simulated and source_root is None:
            raise ValueError("Simulation requires an explicit source checkout")
        if report_path is None:
            report_path = os.environ.get(REPORT_VARIABLE)
            if not report_path:
                raise ValueError(
                    f"No build report given and {REPORT_VARIABLE} is unset. It names the "
                    "report.json of a native check run, with its workers beside it")
        report_path = Path(report_path).resolve()
        client = (NativeDescriptionClient.from_simulated_build(
            report_path, source_root=source_root, scenario=scenario, timeout=timeout,
        ) if simulated else NativeDescriptionClient.from_build(report_path, timeout=timeout))
        report = client._build_report
        link = report.get("generic_session" if simulated else "generic_worker_build_link")
        if not isinstance(link, dict) or link.get("status") != "passed":
            raise ValueError("The report carries no passed generic worker build")
        filename, key = (_SIMULATED_SESSIONS[simulated_session] if simulated else
                         ("generic-worker-never-run", "executable_sha256"))
        helper = report_path.parent / native_worker_name(filename, report=report)
        return cls(client, helper, expected_helper_sha256=link.get(key),
                   timeout=timeout)

    def binding_context(self) -> dict:
        """Disclose the selected worker context without enumeration or initialization."""
        source_root = getattr(self._client, "_simulation_source_root", None)
        if source_root is None:
            return dict(kind="production-workers", provenance="physical")
        return dict(
            kind="authenticated-substituted-workers", provenance="simulated",
            source_root=str(source_root), description_fixture=self._client._fixture,
            session_worker=self._helper.name,
            limitations=[
                "Substituted responses do not establish physical device operation",
                "The acquisition substitute parses selection without proving OS-path attachment",
                "Description and acquisition fixtures retain independent reported serials; "
                "a mismatched available serial must refuse",
            ],
        )

    # -- enumerate --------------------------------------------------------------

    def catalog_json(self) -> str:
        """Read this build's cached static catalogue without enumeration or initialization."""
        return self._client.catalog_json()

    def model_policy(self, expect: str, *, catalog_json: str | None = None) -> dict:
        """Static canonical model and opening/binding policy; no enumeration or initialization."""
        native_type = self._native_type(expect, catalog_json)
        symbol = self._catalog_map(catalog_json)[native_type]
        assessed, refusal = self._disclosure(native_type, catalog_json)
        return dict(
            model_enum=symbol, native_type=native_type,
            display_label=instrument_display_name(symbol),
            binding_policy=self.binding_policy(symbol, catalog_json=catalog_json),
            initialization_assessed=assessed, initialization_refusal=refusal,
            opening_requires=_opening_requirement(native_type, True, assessed, refusal),
        )

    def attached(self, *, catalog_json: str | None = None) -> tuple[DiscoveredInstrument, ...]:
        """List paths without instrument initialization; OS access is platform-dependent.

        This is the whole of a "Refresh devices" action. The result is a list to
        SHOW, including units that are unidentified, unassessed or refused, each
        saying which it is -- a UI that hides them leaves an operator with an
        instrument plugged in and no row for it.

        ``catalog_json`` overrides the build's own catalog, and exists because a
        substituted worker links no upstream catalog to report; a fixture supplies
        the recorded one. A real build never needs it.
        """
        payload = _payload(self._client.list_candidates_json(), "enumeration")
        if catalog_json is None:
            catalog_json = self.catalog_json()
        return discovered_instruments(json.dumps(payload), catalog_json)

    # -- describe ---------------------------------------------------------------

    def describe(
        self,
        found: DiscoveredInstrument,
        *,
        expect: str | None = None,
        expected_serial: str | None = None,
        allow_serial_identification: bool = False,
        accept_disclosed_initialization: bool = False,
        accept_unassessed_initialization: bool = False,
        catalog_json: str | None = None,
    ) -> InstrumentDescription:
        """Initialize exactly this unit and obtain its scoped capability facts.

        This INITIALISES the instrument. Some drivers have visible effects
        in the room -- see this module's header -- so a disclosed
        initialisation must be accepted explicitly rather than waived by a general
        "force" flag. An UNASSESSED one takes a different acknowledgement again,
        because "nobody read this driver" and "this driver flashes an LED" are not
        the same thing to agree to, and one flag covering both would let a caller
        accepting the known effect silently accept the unknown one.

        ``expect`` names what a serial path should turn out to be. A serial port
        carries no identity until it is opened (``icoms.c:269-299``), so
        identifying one means talking to the device, and that needs both the
        expected type and ``allow_serial_identification``.

        A type the catalog marks ``typed_serial_selection`` is the exception: it
        is selected by ``expect`` alone. Its driver is constructed for this one
        path without Argyll's generic identification probes, and initialisation
        opens only this port and requires the exact model the driver was built
        for (``serial_policy.h``). ``allow_serial_identification`` does not apply.
        """
        candidate, arguments = self.validate_selection(
            found, expect=expect, expected_serial=expected_serial,
            allow_serial_identification=allow_serial_identification,
            accept_disclosed_initialization=accept_disclosed_initialization,
            accept_unassessed_initialization=accept_unassessed_initialization,
            catalog_json=catalog_json,
        )
        result_json = self._client.describe_json(candidate, **arguments)
        message = _payload(result_json, "description")
        envelope = json.loads(result_json)
        receipt = self._checked_description_receipt(envelope.get("backend"))
        if "native_identity" not in message:
            raise ValueError("Initialized description omits native identity availability")
        native = self._checked_native_identity(
            message["native_identity"], message["initialized_dtype"], message.get("serial"),
            catalog_json,
        )
        masks = message["capabilities"]
        described = found
        if found.native_type == 0 or found.name is None:
            # A serial unit identified itself. Carry the answer, not the blank --
            # INCLUDING its disclosure. Carrying the unidentified path's assessment
            # forward would publish `instUnknown`'s facts under the identified
            # model's name, and `bind` initialises that model again on the session
            # worker.
            identified = message["initialized_dtype"]
            symbol = self._catalog_map(catalog_json).get(identified)
            assessed, refusal = self._disclosure(identified, catalog_json)
            described = DiscoveredInstrument(
                index=found.index, native_type=identified, symbol=symbol,
                name=instrument_display_name(symbol) if symbol else None, path=found.path,
                transport=found.transport,
                initialization_assessed=assessed, initialization_refusal=refusal,
            )
        return InstrumentDescription(
            discovered=described,
            initialized_type=message["initialized_dtype"],
            serial=message.get("serial"),
            mode_capabilities=masks["primary"],
            trigger_capabilities=masks["secondary"],
            filter_capabilities=masks["tertiary"],
            baseline_mode_checks=message["baseline_mode_checks"],
            display_types=message["display_types"],
            strip_policies=message["strip_policies"],
            ambient_ccmx=message.get("ambient_ccmx", False),
            ambient_matrix_threshold=message.get("ambient_matrix_threshold"),
            provenance=message.get("provenance", "physical"),
            native_identity=native,
            backend_receipt=receipt,
        )

    def _checked_description_receipt(self, receipt):
        if (not isinstance(receipt, dict)
                or set(receipt) != {"version", "helper_sha256", "build_report_sha256"}
                or receipt["version"] != "3.5.0"
                or receipt["helper_sha256"] != getattr(self._client, "_helper_sha256", None)
                or not isinstance(receipt["helper_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", receipt["helper_sha256"]) is None
                or receipt["build_report_sha256"] != getattr(self._client, "_report_sha256", None)
                or receipt["build_report_sha256"] is not None and (
                    not isinstance(receipt["build_report_sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", receipt["build_report_sha256"]) is None)):
            raise ValueError(
                "Initialized description is not bound to its authenticated backend receipt",
            )
        return deepcopy(receipt)

    def _checked_native_identity(self, value, initialized_type, serial, catalog_json):
        native = validate_native_identity(value, serial=serial)
        if native is not None:
            symbol = self._catalog_map(catalog_json).get(initialized_type)
            if not self._native_facts(initialized_type, catalog_json):
                raise ValueError(
                    "This initialized type has no authenticated native identity adapter",
                )
            try:
                # Reuse the binding resolver/catalogue as the model authority.
                # Each validated native driver reports a short model within its
                # manufacturer's namespace, not a second frontend alias.
                namespace = ("Photo Research " if native["driver"] == "pr655"
                             else "Colorimetry Research ")
                reported = instrument_binding_name(
                    namespace + native["identity"]["model"],
                )
                actual = instrument_binding_name(symbol)
            except KeyError as error:
                raise ValueError(
                    "Reported native model is absent from the binding catalogue",
                ) from error
            if reported != actual:
                raise ValueError("Reported native model differs from the observed initialized type")
        return deepcopy(native)

    def validate_selection(
        self, found, *, expect=None, expected_serial=None, allow_serial_identification=False,
        accept_disclosed_initialization=False, accept_unassessed_initialization=False,
        catalog_json=None,
    ):
        """Validate selection/form policy without enumeration or instrument access."""
        if not isinstance(found, DiscoveredInstrument):
            raise TypeError("describe takes a DiscoveredInstrument from attached()")
        # An unidentified path's facts are `instUnknown`'s: the port's, always
        # reported unassessed. Gating on them asked every serial selection to
        # accept an "unassessed" effect, even for a type this build has read. The
        # worker skips them too (description.c:224) and judges the expected type
        # (description.c:309-310), as the branch below does.
        if found.native_type != 0:
            _gate(found.name or found.path, found.initialization_assessed,
                  found.initialization_refusal,
                  accept_disclosed_initialization, accept_unassessed_initialization)
        expected_type = None
        accepted_reason = found.initialization_refusal
        if expect is not None:
            expected_type = self._native_type(expect, catalog_json)
            # An unidentified path carries the facts for `instUnknown`, which are
            # about the PORT and say nothing about the model it turns out to hold.
            # The instrument that will actually be initialised is the expected
            # one, so it is judged before anything is opened -- which is what
            # description.c:271 does with init_refusal(expected_initialized_dtype).
            if found.native_type == 0:
                assessed, refusal = self._disclosure(expected_type, catalog_json)
                accepted_reason = refusal
                _gate(instrument_display_name(expect), assessed, refusal,
                      accept_disclosed_initialization, accept_unassessed_initialization)
        typed = expected_type is not None and self._typed_serial(expected_type, catalog_json)
        if found.native_type == 0:
            if expected_type is None:
                raise ValueError(
                    f"{found.path} is an unidentified path and carries no type; name what it "
                    "should be with expect= and pass allow_serial_identification=True"
                )
            if typed:
                if allow_serial_identification:
                    raise ValueError(
                        f"{instrument_display_name(expect)} is selected by its type without "
                        "identification; allow_serial_identification does not apply"
                    )
            elif not allow_serial_identification:
                raise ValueError(
                    f"Identifying {found.path} means OPENING the port to ask the device what "
                    "it is; pass allow_serial_identification=True to permit that"
                )
            elif not self._generic_serial(expected_type, catalog_json):
                raise ValueError("Expected type is not eligible for generic serial identification")
            if not found.transport & (0x01 | 0x100 | 0x400):
                raise ValueError("Serial selection requires an unidentified serial transport")
        elif allow_serial_identification:
            raise ValueError("Serial identification applies only to an unidentified path")
        candidate = json.dumps({
            "path": found.path, "dtype": found.native_type, "transport": found.transport,
            "initialization_assessed": found.initialization_assessed,
            "initialization_refusal": found.initialization_refusal,
        })
        from .argyll_description import description_arguments

        kwargs = dict(
            expected_initialized_dtype=expected_type, expected_serial=expected_serial,
            allow_serial_identification=allow_serial_identification,
            accepted_initialization_reason=(accepted_reason
                                            if accept_disclosed_initialization else None),
            accept_unassessed_initialization=accept_unassessed_initialization,
        )
        description_arguments(candidate, **kwargs)
        return candidate, kwargs

    # -- offer ------------------------------------------------------------------

    def bind(
        self,
        description: InstrumentDescription,
        *,
        device_id: str | None = None,
        allow_serial_identification: bool = False,
        timeout: float | None = None,
        catalog_json: str | None = None,
        native_control_state=None,
    ) -> InstrumentFrontend:
        """One ``InstrumentFrontend`` over the unit this description came from.

        ``device_id`` defaults to the enumerated PATH rather than the model,
        because two units of one model share a model name and would otherwise
        write records nobody can tell apart. A path is unique within the
        enumeration it came from; it is not stable across a replug, so a client
        keeping records across sessions should pass a label of its own.
        """
        from .instrument import Instrument
        from .instrument_frontend import InstrumentFrontend

        if not isinstance(description, InstrumentDescription):
            raise TypeError("bind takes an InstrumentDescription from describe()")
        chosen = self.validate_binding_timeout(description.name, timeout,
                                              catalog_json=catalog_json)
        floor = self._read_budget_floor(description.initialized_type, catalog_json)
        meter = Instrument(
            helper=self._helper,
            instrument=description.name,
            expected_serial=description.serial or None,
            path_index=description.discovered.index,
            expected_path=description.discovered.path,
            expected_helper_sha256=self._helper_sha256,
            allow_serial_identification=allow_serial_identification,
            timeout=chosen,
        )
        binding_id = device_id or description.discovered.path
        receipt = self._checked_description_receipt(description.backend_receipt)
        native = self._checked_native_identity(
            description.native_identity, description.initialized_type, description.serial,
            catalog_json,
        )
        if json.dumps(native, sort_keys=True, allow_nan=False) != description._native_identity_json:
            raise ValueError("Native identity differs from the initialized observation")
        current_identity = dict(
            device_id=binding_id, initialized_type=description.initialized_type,
            native_identity=native, provenance=dict(
                backend_version=receipt["version"],
                description_helper_sha256=receipt["helper_sha256"],
                session_helper_sha256=self._helper_sha256,
                build_report_sha256=receipt["build_report_sha256"],
            ),
        )
        return InstrumentFrontend(
            meter,
            mode_capabilities=description.mode_capabilities,
            trigger_capabilities=description.trigger_capabilities,
            filter_capabilities=description.filter_capabilities,
            baseline_mode_checks=description.baseline_mode_checks,
            display_types=description.display_types,
            strip_policies=description.strip_policies,
            ambient_ccmx=description.ambient_ccmx,
            ambient_matrix_threshold=description.ambient_matrix_threshold,
            provenance=description.provenance,
            serial=description.serial or None,
            device_id=binding_id,
            current_binding_identity=current_identity,
            read_allowance_floor=floor,
            read_allowance_floors=self._read_floors_by_word(
                description.initialized_type, catalog_json),
            binding_policy=self.binding_policy(description.name, catalog_json=catalog_json),
            native_facts=self._native_facts(description.initialized_type, catalog_json),
            native_controls_available=self._native_controls(description.initialized_type,
                                                            catalog_json),
            native_control_state=native_control_state,
        )

    # -- per-type facts a client needs BEFORE opening or binding -----------------

    def validate_binding_timeout(self, expect, timeout=None, *, catalog_json=None):
        """One pre-binding rule for direct callers and setup, across every offered word."""
        policy = self.binding_policy(expect, catalog_json=catalog_json)
        chosen = policy["default_binding_timeout_seconds"] if timeout is None else timeout
        if type(chosen) not in (int, float) or not 0 < chosen <= 600:
            raise ValueError("Binding timeout must be finite seconds in (0, 600]")
        floor = policy["all_modes_read_allowance_floor_seconds"]
        if floor is not None and chosen <= floor:
            raise ValueError(
                f"{instrument_display_name(expect)} needs more than {floor:g} s for every offered "
                f"mode/output; this binding allows {chosen:g} s. Bind with a larger timeout"
            )
        return float(chosen)

    def typed_serial_selection(self, expect: str, *, catalog_json: str | None = None) -> bool:
        """Whether ``describe(found, expect=expect)`` selects this type on an
        unidentified serial path WITHOUT identification.

        True only for a driver the build's compiled ``serial_policy.h`` admits
        (today the CR-250, in a composed build). A client uses it to know, before
        opening anything, that ``allow_serial_identification`` does not apply --
        passing it is refused -- and to say so to the operator. Read from the same
        catalog ``describe`` and ``bind`` use.
        """
        return self._typed_serial(self._native_type(expect, catalog_json), catalog_json)

    def read_allowance_floor(self, expect: str, *,
                             catalog_json: str | None = None) -> float | None:
        """The smallest timeout, in seconds, this type's driver accepts for one read,
        or None when it declares none.

        This cheapest-mode floor is diagnostic. ``bind`` and
        ``InstrumentFrontend.prepare`` use ``binding_policy`` to refuse a timeout
        at or below the maximum published floor across all modes. This method
        returns the driver's own figure for its cheapest mode, asked with this
        application's allowance transform composed when the build was made (174 s
        for the CR-250, 165 s for the CR-100). Exceeding it is
        necessary, not sufficient: a spectral read, preflight reads and writes, an
        operator-trigger wait and a longer exposure setting also consume the read's
        allowance, and the driver then refuses before measuring. This returns no
        recommended margin; how much more to allow is the client's decision.
        """
        return self._read_budget_floor(self._native_type(expect, catalog_json), catalog_json)

    def binding_policy(self, expect: str, *, catalog_json: str | None = None) -> dict:
        """Expose word-specific floors and one declared application timeout policy.

        Binding precedes mode/output selection, so its allowance must exceed
        every published word floor. The margin follows the existing application
        client policy, without promising completion or physical device support.
        """
        native_type = self._native_type(expect, catalog_json)
        cheapest = self._read_budget_floor(native_type, catalog_json)
        floors = self._read_floors_by_word(native_type, catalog_json)
        maximum = max([value for value in [cheapest, *(floors or {}).values()]
                       if value is not None], default=None)
        default = self._timeout if maximum is None else max(
            self._timeout, maximum + self.READ_ALLOWANCE_MARGIN_SECONDS,
        )
        return dict(
            cheapest_read_allowance_floor_seconds=cheapest,
            read_allowance_floors_seconds_by_word=(
                {str(word): value for word, value in floors.items()} if floors is not None else None
            ),
            all_modes_read_allowance_floor_seconds=maximum,
            default_binding_timeout_seconds=default,
            binding_timeout_margin_seconds=self.READ_ALLOWANCE_MARGIN_SECONDS,
            limitation="Exceeding driver floors is necessary, not sufficient for a read",
        )

    # -- internals --------------------------------------------------------------

    def _catalog_map(self, catalog_json: str | None) -> dict[int, str]:
        if catalog_json is not None:
            return instrument_catalog(catalog_json)
        if self._catalog is None:
            self._catalog = instrument_catalog(self._client.catalog_json())
        return self._catalog

    def _disclosure(self, native_type: int, catalog_json: str | None) -> tuple[bool, str | None]:
        """``(assessed, refusal)`` for one instrument TYPE, from the build's catalog.

        The enumeration reports these per PATH, which is the same answer for an
        identified one and no answer at all for a serial port. A catalog that does
        not carry them -- an older manifest, or a fixture written before they were
        published -- reads as UNASSESSED rather than as harmless, because the
        absence of a statement has never been an assurance here.
        """
        for row in self._rows(catalog_json):
            if row.get("native_type") == native_type:
                return bool(row.get("initialization_assessed")), row.get("initialization_refusal")
        return False, None

    def _read_budget_floor(self, native_type: int, catalog_json: str | None) -> float | None:
        """The driver's declared floor for one read, from the build's catalog.

        Absent for every driver that declares none. A malformed value is refused
        rather than read as "no floor", which would let a doomed read start.
        """
        for row in self._rows(catalog_json):
            if row.get("native_type") != native_type:
                continue
            floor = row.get("read_allowance_floor_s")
            if floor is None:
                return None
            if type(floor) not in (int, float) or not math.isfinite(floor) or floor <= 0:
                raise ValueError(f"Malformed read-allowance floor for {row.get('enum')}")
            return float(floor)
        return None

    def _read_floors_by_word(self, native_type: int,
                             catalog_json: str | None) -> dict[int, float] | None:
        """The driver's floor per exact mode word, from the build's catalog, or None.

        Keys are inst_mode words, which JSON carries as text. A malformed entry is
        refused rather than dropped, which would hold that word to a lower floor.
        """
        for row in self._rows(catalog_json):
            if row.get("native_type") != native_type:
                continue
            floors = row.get("read_allowance_floors_s")
            if floors is None:
                return None
            if not isinstance(floors, dict) or not all(
                    isinstance(word, str) and word.isdigit() and type(value) in (int, float)
                    and math.isfinite(value) and value > 0 for word, value in floors.items()):
                raise ValueError(f"Malformed read-allowance floors for {row.get('enum')}")
            return {int(word): float(value) for word, value in floors.items()}
        return None

    def _native_facts(self, native_type: int, catalog_json: str | None) -> bool:
        """Whether this type's driver publishes retained native facts. A catalog
        without the fact was built with no support adapter, so none does."""
        for row in self._rows(catalog_json):
            if row.get("native_type") == native_type:
                value = row.get("native_facts", False)
                if type(value) is not bool:
                    raise ValueError(f"Malformed native_facts for {row.get('enum')}")
                return value
        return False

    def _native_controls(self, native_type: int, catalog_json: str | None) -> bool:
        """Only authenticated compiled metadata can advertise the settings adapter."""
        for row in self._rows(catalog_json):
            if row.get("native_type") == native_type:
                value = row.get("native_controls", False)
                if type(value) is not bool:
                    raise ValueError(f"Malformed native_controls for {row.get('enum')}")
                return value
        return False

    def _typed_serial(self, native_type: int, catalog_json: str | None) -> bool:
        """Whether the build selects this TYPE on a serial path without probing.

        Read from the catalog, which the build derives from the compiled
        ``serial_policy.h``. A catalog without the fact reads as False, which asks
        for generic identification; the worker then refuses rather than probes.
        """
        return any(row.get("native_type") == native_type
                   and row.get("typed_serial_selection") is True
                   for row in self._rows(catalog_json))

    def _generic_serial(self, native_type, catalog_json):
        """Use the catalog's compiled shared serial policy, never a model-name branch."""
        return any(row.get("native_type") == native_type
                   and row.get("generic_serial_identification") is True
                   for row in self._rows(catalog_json))

    def _rows(self, catalog_json: str | None) -> list[dict]:
        text = catalog_json if catalog_json is not None else self._client.catalog_json()
        rows = json.loads(text).get("instrument_types")
        return rows if isinstance(rows, list) else []

    def _native_type(self, name: str, catalog_json: str | None) -> int:
        """The ordinal this build gives a name, refusing a name it does not carry."""
        wanted = instrument_binding_name(name)
        for native_type, symbol in self._catalog_map(catalog_json).items():
            try:
                if instrument_binding_name(symbol) == wanted:
                    return native_type
            except KeyError:
                continue
        raise ValueError(f"This build's catalog carries no instrument named {wanted}")
