"""Desktop bridge over the existing batch and scientific owners; no device transport."""

import hashlib
import json
import math
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from threading import Event, RLock, get_ident
from uuid import uuid4

from . import chart_context, chart_state, chart_targets
from .analysis_exports import AnalysisExportError, publish_analysis, publish_prepared
from .application import BatchApplication, _fields, _uuid, review_saved
from .colorimeter_correction import correction_material_json
from .dataset_binding import DatasetBinding
from .demo import is_demo_frontend
from .desktop_setup import DesktopInstrumentSetup
from .exports import _read_regular
from .frontend import MAX_REQUEST_BYTES, _json
from .frontend import _load as _load_science_json
from .instrument_frontend import InstrumentFrontend, descriptor_context
from .models import Spectrum, _decode
from .serial_frontend import DirectSerialFrontend

_SCIENCE_MATERIAL_BYTES = 131072
_SCIENCE_MATERIAL_QUANTITIES = {
    "illuminant": "relative_spectral_power", "characterization": "reflectance",
}


def _science_material_role(role):
    if not isinstance(role, str) or role not in _SCIENCE_MATERIAL_QUANTITIES:
        raise ValueError("Choose illuminant or characterization material")
    return role


def _renderer_options(options):
    """Keep correction file paths at the native-dialog boundary."""
    if not isinstance(options, dict):
        raise ValueError("Selected frontend options must be an object")
    correction = options.get("correction")
    if correction is not None and (not isinstance(correction, dict)
                                   or set(correction) != {"token"}
                                   or not isinstance(correction["token"], str)):
        raise ValueError("Select correction materials through the native file dialog")
    return deepcopy(options)


class DesktopSession:
    """One process lifetime, one batch owner, fenced renderer calls and read-only analysis.

    Native dialogs supply paths directly to create/review; renderer requests cannot
    select filesystem paths. The batch retains commands, drafts, stop and save facts.
    A host serializes bridge calls off its UI thread; owner callbacks remain independent.
    """

    def __init__(self, frontends, *, setup_executor=None):
        self.batch = None
        self._demo_preload_running = False
        self.name = "Untitled batch"
        self.notes = ""
        self._mutex = RLock()
        self._live_binding = None
        self._binding = DatasetBinding()
        self._equipment_setup = DesktopInstrumentSetup(self._mutex, self._setup_idle,
                                                      executor=setup_executor)
        self._analysis_recovery = {}
        self._chart_source = None
        self._settings_generation = 0
        self._corrections = {}
        self._science_materials = {}
        if not isinstance(frontends, (list, tuple)) or any(
            not isinstance(frontend, (InstrumentFrontend, DirectSerialFrontend))
            for frontend in frontends
        ):
            raise ValueError("Provide an explicit collection of bound instrument frontends")
        self._selections = {}
        for frontend in frontends:
            self.register_frontend(frontend)

    def register_frontend(self, frontend, *, replacing=None):
        """Offer a native caller's already-bound frontend without changing the batch.

        InstrumentBuild owns report authentication, deliberate enumeration,
        selected initialization and binding. This method reads cached metadata
        only and gives the resulting source one session-owned selection token.
        """
        with self._mutex:
            self._require_no_setup()
            if not isinstance(frontend, (InstrumentFrontend, DirectSerialFrontend)):
                raise ValueError("Register an already-bound instrument frontend")
            previous = [token for token, source in self._selections.items()
                        if source["frontend"] is replacing] if replacing is not None else []
            if replacing is not None:
                self._binding_source_idle(replacing)
                if len(previous) != 1:
                    raise ValueError("Replacement source is no longer registered")
            descriptor = json.loads(frontend.describe_json())
            if previous and descriptor["device_id"] != self._selections[previous[0]][
                    "descriptor"]["device_id"]:
                raise ValueError("Replacement must retain the reported source identity")
            if any(source["descriptor"]["device_id"] == descriptor["device_id"]
                   for token, source in self._selections.items() if token not in previous):
                raise ValueError("Bound frontend selections require distinct device identities")
            token = str(uuid4())
            self._selections[token] = dict(frontend=frontend, descriptor=descriptor)
            self._settings_generation += 1
            for old in previous:
                del self._selections[old]
            return token

    def _binding_source_idle(self, frontend):
        self._setup_idle()
        if self.batch is not None and not self.batch._closed and self.batch._frontend is frontend \
                and self.batch.snapshot()["status"] == "open":
            raise ValueError("Finish the current batch before retiring or replacing its source")

    def _retire_frontend(self, frontend):
        """Retire session selection after the setup owner confirms required host cleanup."""
        self._binding_source_idle(frontend)
        previous = [token for token, source in self._selections.items()
                    if source["frontend"] is frontend]
        for token in previous:
            del self._selections[token]
        if previous:
            self._settings_generation += 1
        return previous

    @contextmanager
    def _equipment_registration_transaction(self):
        """Keep selection and setup registries coherent across callback interruptions."""
        with self._mutex:
            previous = dict(self._selections)
            previous_generation = self._settings_generation
            with self._equipment_setup.registration_transaction():
                try:
                    yield
                except BaseException:
                    self._selections = previous
                    self._settings_generation = previous_generation
                    raise

    def settings(self, selection_id):
        """Expose already-bound sources and one cached descriptor, without discovery.

        Descriptor refresh is the frontend's read-only JSON method. A changed
        descriptor replaces its opaque selection token; creation with an old
        token or cached context is refused rather than changing the selection.
        """
        with self._mutex:
            selected = None
            if selection_id is not None:
                if not isinstance(selection_id, str) or selection_id not in self._selections:
                    raise ValueError("Unknown or stale frontend selection")
                selected = self._selections[selection_id]
            descriptors = {
                token: json.loads(source["frontend"].describe_json())
                for token, source in self._selections.items()
            }
            identities = [descriptor["device_id"] for descriptor in descriptors.values()]
            if len(set(identities)) != len(identities):
                raise ValueError("Bound frontend selections require distinct device identities")
            for token, source in tuple(self._selections.items()):
                descriptor = descriptors[token]
                if descriptor_context(descriptor) != descriptor_context(source["descriptor"]):
                    self._settings_generation += 1
                    del self._selections[token]
                    source["descriptor"] = descriptor
                    self._selections[str(uuid4())] = source
                else:
                    # Keep current read-only report/debt visible without replacing
                    # a source token whose material and receipt fences are unchanged.
                    source["descriptor"] = descriptor
            return deepcopy(dict(
                settings_generation=self._settings_generation,
                selections=[dict(
                    selection_id=token,
                    **{field: source["descriptor"][field]
                       for field in ("device_id", "device", "provenance", "synthetic")},
                ) for token, source in self._selections.items()],
                selection_id=next(
                    (token for token, source in self._selections.items() if source is selected),
                    None,
                ),
                descriptor=selected["descriptor"] if selected is not None else None,
            ))

    def _setup_idle(self):
        self._require_no_setup()
        if self._demo_preload_running or (self.batch and (
            self.batch.snapshot()["phase"] != "idle"
            or self.batch.snapshot()["software_stopped"] is not True
        )):
            raise ValueError("Stop current acquisition before changing equipment setup")

    def _require_no_setup(self):
        active = self._equipment_setup._active
        if active is not None and active["thread_id"] != get_ident():
            raise ValueError("Device setup is running. Cancel it or wait for its outcome.")

    def select_build_report(self, path, *, expected_setup_generation, simulated=False,
                            source_root=None, scenario="success", simulated_session="generic"):
        """Authenticate the native chooser's build report without device access."""
        with self._mutex:
            self._setup_idle()
            return self._equipment_setup.select_report(
                path, expected_generation=expected_setup_generation,
                simulated=simulated, source_root=source_root, scenario=scenario,
                simulated_session=simulated_session,
            )

    def close_direct_sources(self):
        """Release persistent direct sources after the confirmed native batch close.

        Each close uses the same setup ledger and retirement owner as an explicit
        equipment close. Failed resources stay registered for an explicit retry.
        The native host handles ordinary failures and propagated controls separately.
        """
        with self._mutex:
            self._require_no_setup()
            bindings = [view for view in self._equipment_setup.snapshot()["bindings"]
                        if view.get("backend") == "direct_serial" and view["state"] != "closed"]
            if not bindings:
                return dict(outcomes=[], failures=[])
            if self.batch is not None and not self.batch._closed:
                raise ValueError("Close the retained batch before releasing its direct sources")
            self._setup_idle()
            outcomes, failures = [], []
            for view in bindings:
                outcome = self._equipment_setup.close(
                    self._retire_frontend, command_id=str(uuid4()),
                    setup_generation=self._equipment_setup.generation,
                    binding_id=view["binding_id"], binding_generation=view["generation"],
                    validate_retirement=self._binding_source_idle,
                    registration_transaction=self._equipment_registration_transaction,
                )
                outcomes.append(outcome)
                if outcome["state"] != "completed":
                    failures.append(dict(binding_id=view["binding_id"], model=view["model"],
                                         reason=outcome["reason"],
                                         command_id=outcome["command_id"]))
            return dict(outcomes=outcomes, failures=failures)

    def connect_direct_serial(
        self, port, *, command_id, setup_generation, model, expected_serial,
        accept_disclosed_initialization, response_timeout, measurement_timeout,
        transport=None, clock=time, executor=None,
    ):
        """Native selected port; never a renderer-supplied filesystem path."""
        return self._equipment_setup.connect_direct(
            self.register_frontend, port, command_id=command_id,
            setup_generation=setup_generation, model=model, expected_serial=expected_serial,
            accept_disclosed_initialization=accept_disclosed_initialization,
            response_timeout=response_timeout, measurement_timeout=measurement_timeout,
            transport=transport, clock=clock, executor=executor,
            registration_transaction=self._equipment_registration_transaction,
            validate_replacement=self._binding_source_idle,
        )

    def reopen_direct_serial(
        self, port, *, command_id, setup_generation, binding_id, binding_generation,
        accept_disclosed_initialization, response_timeout, measurement_timeout,
        transport=None, clock=time, executor=None,
    ):
        """Native chosen port; reported identity comes only from the retained binding."""
        with self._mutex:
            record = self._equipment_setup._bindings.get(binding_id)
            if record is None:
                raise ValueError("Unknown equipment binding")
            model, serial = record["view"]["model"], record["view"]["serial"]
        return self._equipment_setup.connect_direct(
            self.register_frontend, port, command_id=command_id,
            setup_generation=setup_generation, model=model,
            expected_serial=serial, binding_id=binding_id,
            binding_generation=binding_generation,
            accept_disclosed_initialization=accept_disclosed_initialization,
            response_timeout=response_timeout, measurement_timeout=measurement_timeout,
            transport=transport, clock=clock, executor=executor,
            registration_transaction=self._equipment_registration_transaction,
            validate_replacement=self._binding_source_idle,
        )

    def _correction_scope(self, *, selection_id, mode, capability_id, settings_generation):
        if (type(settings_generation) is not int
                or settings_generation != self._settings_generation):
            raise ValueError("Correction settings generation changed")
        source = self._selections.get(selection_id)
        if source is None:
            raise ValueError("Unknown or stale frontend selection")
        descriptor = source["descriptor"]
        if (descriptor["capability_id"] != capability_id
                or descriptor_context(json.loads(source["frontend"].describe_json()))
                != descriptor_context(descriptor)):
            raise ValueError("Correction frontend descriptor changed")
        return source["frontend"], descriptor, dict(
            selection_id=selection_id, mode=mode, capability_id=capability_id,
            settings_generation=settings_generation,
        )

    def _current_correction_scope(self, *, batch_id, generation, context_id, draft_id, version):
        if self.batch is None or self._binding.kind != "live":
            raise ValueError("Correction requires the current live batch")
        state = self.batch.snapshot()
        if (type(generation) is not int or type(version) is not int
                or any(state[key] != value for key, value in
                       (("batch_id", batch_id), ("generation", generation),
                        ("context_id", context_id)))
                or (state["draft"]["id"], state["draft"]["version"]) != (draft_id, version)):
            raise ValueError("Correction current settings changed")
        if state["phase"] != "idle" or state["status"] != "open" or not state["context_current"]:
            raise ValueError("Correction requires idle current frontend context")
        return self.batch._frontend, state["descriptor"], dict(
            batch_id=batch_id, generation=generation, context_id=context_id,
            draft_id=draft_id, version=version, mode=state["mode"],
            capability_id=state["descriptor"]["capability_id"],
        )

    @staticmethod
    def _validate_correction_options(frontend, descriptor, mode, options):
        # Reuse the complete frontend validator, including installed corrections,
        # mode-specific facility checks and source-backed matrix restrictions.
        frontend._validate_request(_json(dict(
            schema_version=1, device_id=descriptor["device_id"],
            capability_id=descriptor["capability_id"], mode=mode, workflow="spot",
            patch_ids=["correction-validation"], options=options, layout={},
            event_contract="retained_patch_v1",
        )))

    def _stage_correction(self, path, frontend, descriptor, scope, options):
        options = _renderer_options(options)
        path = Path(path).absolute()
        if path.lstat().st_size > MAX_REQUEST_BYTES:
            raise ValueError("Correction source exceeds the frontend request byte bound")
        raw = _read_regular(path)
        text = raw.decode("utf-8")
        material = correction_material_json(text)
        source = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                      material_sha256=hashlib.sha256(material.encode("utf-8")).hexdigest())
        correction = dict(text=text, source=source)
        self._validate_correction_options(
            frontend, descriptor, scope["mode"], dict(options, correction=correction),
        )
        if _read_regular(path) != raw:
            raise ValueError("Correction source bytes changed during selection")
        token = str(uuid4())
        view = dict(
            kind=json.loads(material)["kind"], source_name=path.name,
            source_sha256=source["sha256"], material_sha256=source["material_sha256"],
        )
        self._corrections[token] = dict(scope=scope, correction=correction, raw=raw, material=view)
        return dict(correction=dict(token=token), material=deepcopy(view))

    def stage_correction(self, path, *, selection_id, mode, capability_id,
                         settings_generation, options):
        """Native chooser only: capture a validated correction for New batch."""
        with self._mutex:
            frontend, descriptor, scope = self._correction_scope(
                selection_id=selection_id, mode=mode, capability_id=capability_id,
                settings_generation=settings_generation,
            )
            return self._stage_correction(path, frontend, descriptor, scope, options)

    def stage_current_correction(self, path, *, batch_id, generation, context_id,
                                 draft_id, version, options):
        """Native chooser only: capture material for the exact current draft version."""
        with self._mutex:
            frontend, descriptor, scope = self._current_correction_scope(
                batch_id=batch_id, generation=generation, context_id=context_id,
                draft_id=draft_id, version=version,
            )
            return self._stage_correction(path, frontend, descriptor, scope, options)

    def _resolve_correction_options(self, options, scope, *, replay=False):
        options = _renderer_options(options)
        choice = options.get("correction")
        if choice is None:
            return options
        record = self._corrections.get(choice["token"])
        if record is None:
            raise ValueError("Unknown correction material token")
        if not replay:
            if record["scope"] != scope:
                raise ValueError("Correction material belongs to different settings")
            if _read_regular(Path(record["correction"]["source"]["path"])) != record["raw"]:
                raise ValueError("Correction source bytes changed after selection")
        options["correction"] = deepcopy(record["correction"])
        return options

    def create(self, folder, name, notes, *, selection_id, mode, options):
        # The batch owner validates setup text before any folder mutation.
        with self._mutex:
            self._require_no_setup()
            if not isinstance(selection_id, str) or selection_id not in self._selections:
                raise ValueError("Unknown or stale frontend selection")
            source = self._selections[selection_id]
            frontend = source["frontend"]
            if json.loads(frontend.describe_json()) != source["descriptor"]:
                raise ValueError(
                    "Frontend context changed; refresh settings before creating a batch"
                )
            selected_options = self._resolve_correction_options(options, dict(
                    selection_id=selection_id, mode=mode,
                    capability_id=source["descriptor"]["capability_id"],
                    settings_generation=self._settings_generation,
            ))
            context = dict(
                frontend=frontend, mode=mode, options=selected_options,
                expected_descriptor=deepcopy(source["descriptor"]),
                name=name, notes=notes,
            )
            self.batch = (
                self.batch.new_batch(folder, **context)
                if self.batch
                else BatchApplication(folder, **context)
            )
            # Frozen setup metadata and sample metadata stay at the batch owner.
            self.name, self.notes = name, notes
            self._live_binding = DatasetBinding(owner=self.batch)
            self._binding = self._live_binding
            return self.bind()

    def bind(self):
        with self._mutex:
            state = self.batch.bind() if self.batch else None
            self._binding.bind()
            return self._packet(state)

    def snapshot(self):
        with self._mutex:
            return self._packet(self.batch.snapshot() if self.batch else None)

    def _packet(self, snapshot):
        material = None
        if snapshot is not None and snapshot["draft"]["options"].get("correction") is not None:
            correction = snapshot["draft"]["options"]["correction"]
            scope = dict(
                batch_id=snapshot["batch_id"], generation=snapshot["generation"],
                context_id=snapshot["context_id"], draft_id=snapshot["draft"]["id"],
                version=snapshot["draft"]["version"], mode=snapshot["mode"],
                capability_id=snapshot["descriptor"]["capability_id"],
            )
            matches = [token for token, record in self._corrections.items()
                       if record["scope"] == scope and record["correction"] == correction]
            if not matches:
                originals = [record for record in self._corrections.values()
                             if record["correction"] == correction]
                if originals:
                    token = str(uuid4())
                    self._corrections[token] = dict(originals[0], scope=scope)
                    matches = [token]
            if matches:
                choice = matches[0]
                material = deepcopy(self._corrections[choice]["material"])
            else:
                choice = None
                material = dict(reason="Reselect correction material through the native dialog")
            snapshot["draft"]["options"]["correction"] = dict(token=choice)
        return dict(
            batch=snapshot,
            name=snapshot["batch_name"] if snapshot else self.name,
            notes=snapshot["batch_notes"] if snapshot else self.notes,
            view=deepcopy(self._binding.view),
            dataset_binding=self._binding.metadata(),
            pending_exports=self.export_recovery(),
            correction_material=material,
        )

    def dataset(self):
        with self._mutex:
            return dict(self._binding.data(), binding_id=self._binding.id,
                        generation=self._binding.generation)

    def open_saved(self, folder, *, expected_binding_id, expected_generation):
        """Select authenticated read-only files while preserving the live batch."""
        with self._mutex:
            self._require_no_setup()
            self._binding.fence(expected_binding_id, expected_generation, authenticate=False)
            if self._demo_preload_running or (self.batch and (
                self.batch.snapshot()["phase"] != "idle"
                or self.batch.snapshot()["software_stopped"] is not True
            )):
                raise ValueError("Stop current work before opening saved data")
            candidate = DatasetBinding(folder=folder)
            previous = self._binding
            self._binding = candidate
            if previous.kind == "saved":
                previous.close()
            return self.snapshot()

    def open_record(self, path, *, format, expected_binding_id, expected_generation):
        """Native selected current record file; preserve the retained live acquisition owner."""
        with self._mutex:
            self._require_no_setup()
            self._binding.fence(expected_binding_id, expected_generation, authenticate=False)
            self._setup_idle()
            candidate = DatasetBinding(source_file=path, format=format)
            previous = self._binding
            self._binding = candidate
            if previous.kind == "saved":
                previous.close()
            return self.snapshot()

    def activate_live(self, *, expected_binding_id, expected_generation):
        with self._mutex:
            # A changed saved file must not prevent returning to retained live work.
            self._binding.fence(expected_binding_id, expected_generation, authenticate=False)
            previous = self._binding
            self._binding = self._live_binding or DatasetBinding()
            if previous.kind == "saved":
                previous.close()
            if self.batch:
                self.batch.bind()
            self._binding.bind()
            return self.snapshot()

    def _dataset_params(self, params):
        params = dict(params)
        binding_id = params.pop("binding_id")
        generation = params.pop("generation")
        self._binding.fence(binding_id, generation)
        return params

    def _science_material_scope(self, binding_id, generation, dataset_revision):
        self._binding.fence(binding_id, generation)
        if self._binding.data()["dataset_revision"] != dataset_revision:
            raise ValueError("Reading revision changed; reselect scientific material")
        return binding_id, dataset_revision

    def stage_science_material(self, path, *, binding_id, generation, dataset_revision, role):
        """Capture one native-selected current Spectrum document without device access."""
        with self._mutex:
            role = _science_material_role(role)
            scope = self._science_material_scope(binding_id, generation, dataset_revision)
            path = Path(path).absolute()
            before = path.lstat()
            raw = _read_regular(path, limit_bytes=_SCIENCE_MATERIAL_BYTES)
            spectrum = _decode(Spectrum, _load_science_json(raw.decode("utf-8")))
            if (spectrum.quantity != _SCIENCE_MATERIAL_QUANTITIES[role]
                    or spectrum.unit != "1"):
                raise ValueError(f"{role.title()} material requires "
                                 f"{_SCIENCE_MATERIAL_QUANTITIES[role]} with unit 1")
            identity = before.st_dev, before.st_ino
            after = path.lstat()
            if ((after.st_dev, after.st_ino) != identity
                    or _read_regular(path, limit_bytes=_SCIENCE_MATERIAL_BYTES) != raw):
                raise ValueError("Scientific material changed during selection")
            after = path.lstat()
            if (after.st_dev, after.st_ino) != identity:
                raise ValueError("Scientific material changed during selection")
            self._science_material_scope(binding_id, generation, dataset_revision)
            digest = hashlib.sha256(raw).hexdigest()
            view = dict(token=str(uuid4()), source_name=path.name, sha256=digest,
                        identity=f"{path.name}; SHA256={digest}", role=role)
            self._science_materials[view["token"]] = dict(
                path=path, file_identity=identity, raw=raw, spectrum=spectrum,
                scope=scope, view=view,
            )
            return deepcopy(view)

    def _resolve_science_materials(self, derivation, dataset_revision):
        """Resolve captured materials into the existing owner's unchanged typed request."""
        request = deepcopy(derivation)
        if not isinstance(request, dict) or not isinstance(request.get("context"), dict):
            return request
        scope = self._science_material_scope(
            self._binding.id, self._binding.generation, dataset_revision,
        )

        def resolve(context, field, role):
            if not isinstance(context, dict):
                return
            choice = context.get(field)
            if not isinstance(choice, dict) or "token" not in choice:
                return
            _fields(choice, {"token"})
            token = choice["token"]
            record = self._science_materials.get(token) if isinstance(token, str) else None
            if record is None:
                raise ValueError("Unknown scientific material token; choose its file again")
            if record["view"]["role"] != role:
                raise ValueError("Scientific material token has a different role")
            if record["scope"] != scope:
                raise ValueError("Scientific material belongs to another dataset revision")
            path = record["path"]
            current = path.lstat()
            if ((current.st_dev, current.st_ino) != record["file_identity"]
                    or _read_regular(path, limit_bytes=_SCIENCE_MATERIAL_BYTES) != record["raw"]):
                raise ValueError(
                    "Scientific material changed after selection; choose its file again")
            current = path.lstat()
            if (current.st_dev, current.st_ino) != record["file_identity"]:
                raise ValueError(
                    "Scientific material changed after selection; choose its file again")
            identity_field = "illuminant_id" if role == "illuminant" else "characterization_id"
            if context.get(identity_field) not in (None, record["view"]["identity"]):
                raise ValueError("Selected scientific material supplies its captured identity")
            context[field] = asdict(record["spectrum"])
            context[identity_field] = record["view"]["identity"]

        context = request["context"]
        method = request.get("method")
        if method in {"spectral_xyz", "relight_reflectance"}:
            resolve(context, "illuminant", "illuminant")
        if method == "characterized_reflectance":
            resolve(context, "characterization", "characterization")
        elif method == "relight_reflectance":
            basis = context.get("reflectance")
            if isinstance(basis, dict) and basis.get("kind") == "characterized-reference":
                resolve(basis.get("context"), "characterization", "characterization")
        self._science_material_scope(self._binding.id, self._binding.generation, dataset_revision)
        return request

    def export_analysis(self, path, *, binding_id, generation, dataset_revision,
                        comparison_version, scope, selected, scale, format, derivation=None):
        """Publish one source-bound analysis file chosen through a native dialog."""
        with self._mutex:
            self._binding.fence(binding_id, generation)
            derivation = self._resolve_science_materials(derivation, dataset_revision)
            try:
                return publish_analysis(
                    self._binding, path, dataset_revision=dataset_revision,
                    comparison_version=comparison_version, scope=scope, selected=selected,
                    scale=scale, format=format, derivation=derivation,
                )
            except AnalysisExportError as error:
                identity = str(uuid4())
                self._analysis_recovery[identity] = error
                error.recovery_id = identity
                raise

    def _record_export_input(self, *, binding_id, generation, dataset_revision, reading_ref):
        from .models import observation_from_dict
        from .reading_dataset import resolve_reading

        self._binding.fence(binding_id, generation)
        data = self._binding.data()
        if data["dataset_revision"] != dataset_revision:
            raise ValueError("Reading revision changed")
        if reading_ref is None:
            if self._binding.kind != "standalone":
                raise ValueError("Select one original reading for a batch component export")
            return self._binding._standalone.record
        key = resolve_reading(data["readings"], reading_ref)
        if not data["readings"][key]["saved"]:
            raise ValueError("Save the selected reading before component export")
        if data["readings"][key]["record_kind"] != "color":
            raise ValueError("Export the original operation for a scalar or timing observation")
        return observation_from_dict(data["readings"][key]["observation"])

    def record_formats(self, *, binding_id, generation, dataset_revision, reading_ref):
        """Inert component eligibility, through the same serializers and TI3/SPECT owners."""
        from . import spectral_exports, ti3_exports
        from .operation_records import OperationResult

        with self._mutex:
            record = self._record_export_input(
                binding_id=binding_id, generation=generation, dataset_revision=dataset_revision,
                reading_ref=reading_ref,
            )
            mat = self.format_capabilities()["operation"]["mat"]
            spect_reason = None
            try:
                canonical = spectral_exports._record_parts(record)[4]
                spectral_exports._projection(record, "canonical.json", canonical)
            except (TypeError, ValueError, UnicodeError) as error:
                spect_reason = str(error)
            self._binding.assert_current()
            return dict(formats=[
                dict(format="json", available=True, reason=None),
                dict(format="mat", **mat),
                dict(format="csv", available=not isinstance(record, OperationResult),
                     reason="CSV stores one current color observation" if
                     isinstance(record, OperationResult) else None),
                dict(format="sp", available=spect_reason is None, reason=spect_reason),
            ], ti3=ti3_exports.component_eligibility(record))

    def export_record(self, path, *, binding_id, generation, dataset_revision, format,
                      reading_ref=None, canonical_path=None):
        """Native ordinary record export through current canonical and component owners."""
        from . import exports, operation_exports, spectral_exports
        from .operation_records import OperationResult

        with self._mutex:
            record = self._record_export_input(
                binding_id=binding_id, generation=generation, dataset_revision=dataset_revision,
                reading_ref=reading_ref,
            )
            if format not in {"json", "mat", "csv", "sp"}:
                raise ValueError("Choose a current native record output format")
            if format == "sp" and canonical_path is None or \
                    format != "sp" and canonical_path is not None:
                raise ValueError("SPECT alone requires a separately selected canonical JSON path")
            self._binding.assert_current()
            context = dict(export_kind="native_record", binding_id=binding_id,
                           dataset_revision=dataset_revision,
                           source=self._binding.metadata()["source"],
                           reading_ref=deepcopy(reading_ref), format=format)
            try:
                if format == "sp":
                    saved = spectral_exports.save_sp(record, path, canonical_path=canonical_path)
                elif isinstance(record, OperationResult):
                    saved = operation_exports.save(record, path, format=format)
                else:
                    saved = exports.save(record, path, format=format)
                return dict(path=str(saved.path), ti3=deepcopy(saved.ti3), context=context)
            except (exports.SaveError, operation_exports.OperationSaveError) as failure:
                error = AnalysisExportError(failure.path, failure.payload, failure.format, failure)
                error.context = context
                error.native_record = record
                error.native_format = format
                identity = str(uuid4())
                self._analysis_recovery[identity] = error
                error.recovery_id = identity
                raise error from failure

    def retry_record_export(self, path, *, recovery_id, canonical_path=None):
        """Native explicit retry of retained originals, through the existing format owners."""
        from . import exports, operation_exports, spectral_exports, ti3_exports

        with self._mutex:
            original = self._analysis_recovery.get(recovery_id)
            if original is None or not hasattr(original, "native_record"):
                raise ValueError("Unknown retained native record export")
            format, record = original.native_format, original.native_record
            if format == "sp" and canonical_path is None or \
                    format != "sp" and canonical_path is not None:
                raise ValueError("SPECT alone requires a separately selected canonical JSON path")
            try:
                if format == "sp":
                    saved = spectral_exports.save_sp(record, path, canonical_path=canonical_path)
                else:
                    publication = publish_prepared(
                        path, payload=original.payload, format=original.format,
                        context={key: deepcopy(value) for key, value in original.context.items()
                                 if key != "format"},
                    )
                    target = Path(publication["path"])
                    saved = exports.SaveResult(
                        target, ti3_exports.save_companions(record, target, original.payload),
                    )
            except (exports.SaveError, operation_exports.OperationSaveError,
                    AnalysisExportError) as failure:
                replacement = failure if isinstance(failure, AnalysisExportError) else \
                    AnalysisExportError(failure.path, failure.payload, failure.format, failure)
                replacement.context = deepcopy(original.context)
                replacement.native_record, replacement.native_format = record, format
                replacement.recovery_id = recovery_id
                self._analysis_recovery[recovery_id] = replacement
                if replacement is failure:
                    raise
                raise replacement from failure
            del self._analysis_recovery[recovery_id]
            return dict(path=str(saved.path), ti3=deepcopy(saved.ti3),
                        context=deepcopy(original.context))

    def export_recovery(self):
        """Expose retained snapshot identity/reasons; prepared bytes stay in Python."""
        with self._mutex:
            return [dict(
                recovery_id=identity, format=error.format, attempted_path=str(error.path),
                payload_sha256=hashlib.sha256(error.payload).hexdigest(),
                payload_bytes=len(error.payload), reason=str(error),
                context=deepcopy(error.context),
                published=error.publication is not None,
                retry_method="retry_record_export" if hasattr(error, "native_record")
                else "retry_analysis_export",
                native_format=getattr(error, "native_format", None),
            ) for identity, error in self._analysis_recovery.items()]

    def export_batch_mat(self, path, *, binding_id, generation):
        """Native chooser only: archive exact current batch bytes through the MAT owner."""
        from .batch_archive import prepare

        with self._mutex:
            self._binding.fence(binding_id, generation)
            if self._binding.kind == "standalone":
                raise ValueError(
                    "A standalone record is not a batch archive; export its native record",
                )
            payload, source = prepare(self._binding)
            try:
                return publish_prepared(path, payload=payload, format="mat", context=dict(
                    export_kind="batch", source=source, binding_id=binding_id,
                ))
            except AnalysisExportError as error:
                identity = str(uuid4())
                self._analysis_recovery[identity] = error
                error.recovery_id = identity
                raise

    def format_capabilities(self):
        """Report actual optional MAT availability; this query starts no instrument."""
        from .operation_exports import MAT_UNAVAILABLE_REASON, _mat_dependencies
        from .source_records import FORMATS

        reason = None
        try:
            _mat_dependencies()
        except ImportError:
            reason = MAT_UNAVAILABLE_REASON
        mat = dict(available=reason is None, reason=reason)
        return dict(operation=dict(json=True, mat=deepcopy(mat)),
                    analysis=dict(json=True, csv=True, mat=deepcopy(mat)),
                    batch=dict(mat=deepcopy(mat)),
                    observation=dict(json=True, csv="color records only",
                                     mat=deepcopy(mat),
                                     spect="qualified native spectrum only"),
                    standalone_imports=[dict(
                        format=format, record_kind="operation" if format.startswith("operation_")
                        else "observation", available=mat["available"] if format.endswith("_mat")
                        else True, reason=mat["reason"] if format.endswith("_mat") else None,
                    ) for format in FORMATS])

    def retry_analysis_export(self, path, *, recovery_id):
        """Native chooser only: publish the explicitly retained snapshot, never recalculate."""
        with self._mutex:
            if recovery_id not in self._analysis_recovery:
                raise ValueError("Unknown retained analysis export")
            original = self._analysis_recovery[recovery_id]
            if hasattr(original, "native_record"):
                raise ValueError("Use retry_record_export for a retained native record")
            try:
                receipt = publish_prepared(path, payload=original.payload, format=original.format,
                                           context=deepcopy(original.context))
            except AnalysisExportError as error:
                error.recovery_id = recovery_id
                self._analysis_recovery[recovery_id] = error
                raise
            del self._analysis_recovery[recovery_id]
            return dict(receipt, retained_snapshot=True, recovery_id=recovery_id)

    def discard_analysis_export(self, *, recovery_id, payload_sha256):
        """Native explicit discard of one exact retained payload; preserve all other work."""
        with self._mutex:
            found = next((item for item in self.export_recovery()
                          if item["recovery_id"] == recovery_id), None)
            if found is None or found["payload_sha256"] != payload_sha256:
                raise ValueError("Retained analysis export changed")
            del self._analysis_recovery[recovery_id]
            return self.snapshot()

    def request(self, method, params):
        """Closed JSON-value boundary; command UUIDs stay caller-owned for reconciliation."""
        try:
            wire = json.dumps(params, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise ValueError("Desktop request must contain bounded JSON values") from error
        if len(wire) > 131072:
            raise ValueError("Desktop request exceeds 131072 UTF-8 bytes")
        params = json.loads(wire)
        with self._mutex:
            if method in {"bind", "snapshot"}:
                _fields(params, ())
                return getattr(self, method)()
            if method == "settings":
                _fields(params, ("selection_id",))
                return self.settings(**params)
            if method == "equipment_setup":
                _fields(params, ())
                return self._equipment_setup.snapshot()
            if method == "equipment_outcome":
                _fields(params, ("command_id",))
                return self._equipment_setup.outcome(**params)
            if method == "cancel_setup":
                _fields(params, ("command_id",))
                return self._equipment_setup.cancel(**params)
            if method not in {"refresh_devices", "connect_device", "close_device", "reopen_device"}:
                self._require_no_setup()
            if method == "export_recovery":
                _fields(params, ())
                return self.export_recovery()
            if method == "formats":
                _fields(params, ())
                return self.format_capabilities()
            if method == "stage_chart":
                _fields(params, ("batch_id", "generation", "context_id", "recipe"))
                recipe = params.pop("recipe")
                if not isinstance(recipe, dict):
                    raise ValueError("Choose a chart recipe")
                kind = recipe.get("kind")
                _fields(recipe, ("kind", "name", "rows", "columns")
                        if kind == "custom" else ("kind",))
                if kind not in {"classic", "sg", "custom"}:
                    raise ValueError("Use the native TI2 chooser for imported targets")
                owner = self._native_chart_owner(**params)
                return owner.stage_chart_definition(chart_state.definition(**recipe), **params)
            if method == "continue_chart":
                _fields(params, ("batch_id", "generation", "context_id", "source_token",
                                 "command_id", "action", "source_pass_id",
                                 "acknowledged_material"))
                token = params.pop("source_token")
                command_id = params.pop("command_id")
                _uuid(command_id)
                fence = {key: params.pop(key) for key in ("batch_id", "generation", "context_id")}
                owner = self._native_chart_owner(**fence)
                held = self._chart_source
                if held is None or token != held["token"] or fence != held["fence"]:
                    raise ValueError("Saved chart selection changed; choose the source again")
                return owner.command(
                    command_id, fence["generation"], "continue_chart",
                    dict(folder=held["folder"], source_sha256=held["sha256"], **params),
                    batch_id=fence["batch_id"],
                )
            if method == "record_formats":
                _fields(params, ("binding_id", "generation", "dataset_revision", "reading_ref"))
                return self.record_formats(**params)
            if method in {"refresh_devices", "connect_device", "close_device", "reopen_device"}:
                fields = {"command_id", "setup_generation"}
                if method == "connect_device":
                    fields |= {"listing_generation", "candidate_id", "expect", "expected_serial",
                               "allow_serial_identification", "accept_disclosed_initialization",
                               "accept_unassessed_initialization", "timeout"}
                elif method == "close_device":
                    fields |= {"binding_id", "binding_generation"}
                elif method == "reopen_device":
                    fields |= {"binding_id", "binding_generation", "listing_generation",
                               "candidate_id", "allow_serial_identification",
                               "accept_disclosed_initialization",
                               "accept_unassessed_initialization", "timeout"}
                _fields(params, fields)
                if method == "refresh_devices":
                    return self._equipment_setup.refresh(**params)
                if method == "close_device":
                    return self._equipment_setup.close(
                        self._retire_frontend,
                        validate_retirement=self._binding_source_idle,
                        registration_transaction=self._equipment_registration_transaction, **params,
                    )
                if method == "reopen_device":
                    return self._equipment_setup.connect(
                        self.register_frontend, registered_ids=set(),
                        validate_replacement=self._binding_source_idle,
                        registration_transaction=self._equipment_registration_transaction,
                        expect=None, expected_serial=None, reopening=True, **params,
                    )
                return self._equipment_setup.connect(
                    self.register_frontend,
                    registration_transaction=self._equipment_registration_transaction,
                    registered_ids={source["descriptor"]["device_id"]
                                    for source in self._selections.values()}, **params,
                )
            if method == "dataset":
                _fields(params, ("binding_id", "generation"))
                self._dataset_params(params)
                return self.dataset()
            chart_queries = {
                "chart_view": ("pass_id",),
                "chart_history": ("pass_id", "cell", "offset", "limit"),
                "chart_details": ("pass_id", "attempt_id"),
                "chart_decision_details": (),
                "chart_readings": ("pass_id",),
                "chart_colors": ("pass_id", "color_space", "expected_context"),
                "operation_details": ("command_id",),
                "activity": (),
                "activity_details": ("key", "activity_revision"),
            }
            if method in chart_queries:
                _fields(params, ("binding_id", "generation", *chart_queries[method]))
                result = getattr(self._binding, method)(**self._dataset_params(params))
                return dict(binding_id=self._binding.id, generation=self._binding.generation,
                            value=result)
            if method in {"projection", "analysis"}:
                fields = {"binding_id", "generation", "scale", "dataset_revision",
                          "comparison_version"}
                if method == "analysis":
                    fields.add("selected")
                _fields(params, fields)
                result = self._binding.analyze(method, self._dataset_params(params))
                return dict(result, binding_id=self._binding.id,
                            generation=self._binding.generation)
            if method == "annotation":
                _fields(params, ("binding_id", "generation", "reading_ref"))
                return self._binding.annotation_reference(
                    self._dataset_params(params)["reading_ref"])
            if method == "set_analysis_context":
                _fields(params, ("binding_id", "generation", "command_id", "comparison_version",
                                 "reference", "white"))
                values = self._dataset_params(params)
                values["version"] = values.pop("comparison_version")
                return self._binding.set_context(**values)
            if method == "derive":
                _fields(params, ("binding_id", "generation", "dataset_revision", "selected",
                                 "derivation"))
                values = self._dataset_params(params)
                values["derivation"] = self._resolve_science_materials(
                    values["derivation"], values["dataset_revision"],
                )
                result = self._binding.derive(**values)
                return dict(result, binding_id=self._binding.id,
                            generation=self._binding.generation)
            if method == "set_view":
                _fields(params, ("binding_id", "generation", "view"))
                values = self._dataset_params(params)
                view = values["view"]
                self._validate_view(view)
                self._binding.view = deepcopy(view)
                return self.snapshot()
            if not self.batch:
                raise ValueError("Create a batch first")
            if method == "preview_draft":
                _fields(
                    params,
                    ("batch_id", "generation", "draft_id", "version", "name", "number_samples"),
                )
                return self.batch.preview_draft(**params)
            if method == "command":
                _fields(params, ("command_id", "generation", "action", "payload", "batch_id"))
                if params["action"] in {
                    "another_folder", "continue_chart", "close", "abandon", "confirm_process_exit",
                }:
                    raise ValueError("Use the native folder or shutdown dialog")
                if self._binding.kind != "live" and params["action"] not in {"cancel", "respond"}:
                    raise ValueError("Saved data are read-only; return to the live batch")
                if (params["action"] == "edit_draft" and isinstance(params["payload"], dict)
                        and "options" in params["payload"]):
                    params["payload"]["options"] = _renderer_options(params["payload"]["options"])
                    try:
                        self.batch.command_outcome(params["command_id"])
                    except ValueError:
                        replay = False
                    else:
                        replay = True
                    scope = None
                    if not replay and params["payload"]["options"].get("correction") is not None:
                        _, _, scope = self._current_correction_scope(
                            batch_id=params["batch_id"], generation=params["generation"],
                            context_id=self.batch.snapshot()["context_id"],
                            draft_id=params["payload"].get("draft_id"),
                            version=params["payload"].get("version"),
                        )
                    params["payload"]["options"] = self._resolve_correction_options(
                        params["payload"]["options"], scope, replay=replay,
                    )
                return self.batch.command(**params)
            if method == "outcome":
                _fields(params, ("command_id",))
                return self.batch.command_outcome(params["command_id"])
            if method == "discard_summary":
                _fields(params, ())
                return self.batch.discard_summary()
            raise ValueError("Unknown desktop method")

    def _native_chart_owner(self, *, batch_id, generation, context_id):
        if self._binding.kind != "live" or self.batch is None:
            raise ValueError("Return to a new live batch before configuring a chart")
        current = self.batch.snapshot()
        _uuid(batch_id)
        _uuid(context_id)
        if (type(generation) is not int or any(current[key] != value for key, value in
                (("batch_id", batch_id), ("generation", generation),
                 ("context_id", context_id)))):
            raise ValueError("Chart setup binding changed; open setup again")
        return self.batch

    def stage_chart_target(self, path, *, batch_id, generation, context_id, strips_as):
        """Import native chooser bytes locally; only the bounded preview reaches HTML."""
        with self._mutex:
            fence = dict(batch_id=batch_id, generation=generation, context_id=context_id)
            owner = self._native_chart_owner(**fence)
            if strips_as not in {"rows", "columns"}:
                raise ValueError("Choose whether TI2 strips form rows or columns")
            definition = chart_state.definition(
                "ti2", target_json=chart_targets.import_ti2_json(str(path)),
                strips_as=strips_as, mapping_confirmed=True,
            )
            return owner.stage_chart_definition(definition, **fence)

    def stage_chart_continuation(self, folder, *, batch_id, generation, context_id):
        """Keep an authenticated native source selection outside renderer commands."""
        with self._mutex:
            fence = dict(batch_id=batch_id, generation=generation, context_id=context_id)
            self._native_chart_owner(**fence)
            binding = DatasetBinding(folder=folder)
            view = binding.chart_view()
            details = binding.chart_decision_details()
            passes = []
            for item in details["passes"]:
                summary = binding.chart_view(item["id"])
                declarations = []
                for key, fact in item["context"]["material"].items():
                    if (key in chart_context._DECLARABLE
                            or key.startswith(("effective_option:", "native_setting:"))):
                        if fact["certainty"] not in {"unknown", "requested"}:
                            declarations.append(dict(key=key, value=deepcopy(fact["value"]),
                                                     certainty=fact["certainty"]))
                passes.append(dict(id=item["id"], coverage=summary["coverage"],
                                   total=summary["total"], declarations=declarations))
            source = binding.metadata()["source"]
            token = str(uuid4())
            self._chart_source = dict(token=token, fence=fence, binding=binding,
                                      folder=str(folder), sha256=source["manifest_sha256"])
            return dict(fence, source_token=token, source_name=source["name"],
                        definition=view["definition"], passes=passes)

    def preload_demo(self, *, batch_id, generation, count, stop_event=None):
        """Append 40 or 160 packaged Demo readings through ordinary owner commands.

        A native caller runs this off its UI thread and may set its Event from
        the UI thread. Only Demo instructions are acknowledged automatically.
        Already retained readings and failures remain owned by the batch.
        """
        if type(count) is not int or count not in (40, 160):
            raise ValueError("Choose 40 or 160 Demo readings")
        if stop_event is not None and not isinstance(stop_event, Event):
            raise ValueError("Demo stop control must be a native threading.Event")
        stop_event = stop_event if stop_event is not None else Event()
        with self._mutex:
            self._require_no_setup()
            if (self._binding.kind != "live" or not self.batch
                    or not is_demo_frontend(self.batch._frontend)):
                raise ValueError("Preload requires the bound packaged Demo frontend")
            if self._demo_preload_running:
                raise ValueError("Demo preload is already running")
            owner = self.batch
            initial = owner.snapshot()
            if (batch_id != initial["batch_id"] or type(generation) is not int
                    or generation != initial["generation"]):
                raise ValueError("Demo preload binding changed")
            if not initial["can_measure"]:
                raise ValueError("Demo preload requires an idle saved open batch")
            self._demo_preload_running = True
        completed, command_id, operation_id = 0, None, None
        reason, status = None, "completed"

        def command(state, action, payload):
            return owner.command(str(uuid4()), state["generation"], action, payload,
                                 batch_id=state["batch_id"])

        try:
            while completed < count:
                state = owner.snapshot()
                if stop_event.is_set():
                    status, reason = "stopped", "Demo loading stopped"
                    break
                if (self.batch is not owner or state["batch_id"] != batch_id
                        or state["generation"] != generation):
                    status, reason = "stopped", "Demo preload binding changed"
                    break
                draft = state["draft"]
                command_id = str(uuid4())
                outcome = owner.command(command_id, generation, "measure", dict(
                    context_id=state["context_id"], draft_id=draft["id"],
                    draft_version=draft["version"],
                ), batch_id=batch_id)
                if outcome["admission"] != "admitted":
                    status, reason = "failed", outcome.get("reason")
                    break
                operation_id = outcome["operation_id"]
                cancelled = False
                while True:
                    state = owner.snapshot()
                    changed = self.batch is not owner or state["generation"] != generation
                    if state["operation_id"] == operation_id and not state["operation_terminal"]:
                        if (stop_event.is_set() or changed) and not cancelled:
                            reason = "Demo preload binding changed" if changed else \
                                "Demo loading stopped"
                            command(state, "cancel", {"command_id": command_id})
                            cancelled = True
                        elif state["instructions"] and not cancelled:
                            pending = state["instructions"]
                            reply = command(state, "respond", dict(
                                operation_id=pending["operation_id"],
                                request_id=pending["request_id"], response="accept",
                            ))
                            if reply["admission"] != "admitted":
                                command(state, "cancel", {"command_id": command_id})
                                cancelled = True
                                reason = reply.get("reason")
                    if state["phase"] in {"idle", "cleanup_uncertain"}:
                        break
                    time.sleep(0.005)
                outcome = owner.command_outcome(command_id)
                if (outcome.get("result_status") != "completed"
                        or outcome.get("software_stopped") is not True or state["save_reason"]):
                    status = "stopped" if cancelled or stop_event.is_set() else "failed"
                    terminal_status = outcome.get("result_status", outcome["state"])
                    reason = (reason or state["save_reason"] or outcome.get("reason")
                              or f"Demo operation ended as {terminal_status}")
                    break
                completed += 1
            final = owner.snapshot()
            return dict(
                batch_id=batch_id, requested=count, completed=completed,
                retained=final["sample_count"] - initial["sample_count"],
                state=status, reason=reason,
            )
        except BaseException as error:
            try:
                state = owner.snapshot()
                if (command_id is not None and state["operation_id"] == operation_id
                        and not state["operation_terminal"]):
                    command(state, "cancel", {"command_id": command_id})
            except BaseException as cleanup_error:
                error.add_note(f"Demo preload cancellation failed: {cleanup_error}")
            raise
        finally:
            with self._mutex:
                self._demo_preload_running = False

    def confirm_process_exit(self, *, batch_id, generation, discard_token):
        """Forward the native dialog's exact binding and confirmed discard token.

        The batch owner alone admits the memory-only exit effect. A refused
        confirmation is never refreshed or retried under a new binding here.
        """
        with self._mutex:
            if not self.batch:
                raise ValueError("Create a batch first")
            return self.batch.command(
                str(uuid4()), generation, "confirm_process_exit",
                {"discard_token": discard_token}, batch_id=batch_id,
            )

    def another_folder(self, folder):
        with self._mutex:
            state = self.batch.snapshot()
            return self.batch.command(
                str(uuid4()),
                state["generation"],
                "another_folder",
                {"folder": str(folder)},
                batch_id=state["batch_id"],
            )

    @staticmethod
    def review(folder):
        return review_saved(folder)

    def _validate_view(self, view):
        _fields(
            view,
            (
                "plotSlots",
                "plotted",
                "previousPlotted",
                "displayScale",
                "follow",
                "tool",
                "inspector",
                "controls",
                "expanded",
                "zoom",
            ),
        )
        slots = view["plotSlots"]
        if (
            not isinstance(slots, list)
            or len(slots) != 2
            or any(not isinstance(slot, str) or slot not in {
                "spectrum", "chromaticity", "space", "chart"
            } for slot in slots)
            or slots[0] == slots[1]
        ):
            raise ValueError("Presentation plot positions must name two distinct views")
        identities = set(self._binding.data()["readings"])
        for group in (view["plotted"], view["previousPlotted"]):
            if group is not None and (
                not isinstance(group, list)
                or any(not isinstance(key, str) or key not in identities for key in group)
            ):
                raise ValueError("Presentation membership must name retained readings")
        for number in (view["displayScale"], view["zoom"]):
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError("Presentation scales must be finite and positive")
        if view["tool"] not in {"measure", "compare", "color", "data"} or any(
            type(view[key]) is not bool for key in ("follow", "inspector")
        ):
            raise ValueError("Invalid presentation tool or visibility")
        if not isinstance(view["controls"], dict) or not isinstance(view["expanded"], list):
            raise ValueError("Invalid presentation controls")
