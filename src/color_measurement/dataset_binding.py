"""Selected live or authenticated saved data; never an acquisition owner."""

import hashlib
import json
import stat
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from . import chart_state
from .application import _manifest, _uuid, review_captured, review_saved
from .exports import _read_regular
from .operation_records import result_from_json
from .presentation import DEFAULT_PREVIEW_SCALE
from .reading_dataset import (
    _reading_key,
    analyze_dataset,
    dataset_white_descriptor,
    project_dataset,
    reading_identity,
    resolve_reading,
    validate_analysis,
)
from .source_records import StandaloneRecord


def content_digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def referenced_paths(manifest, source_files=()):
    """The validator's declared canonical inputs, including terminal recovery evidence."""
    names = {"batch.json"}
    for entry in manifest["operations"]:
        names.add(entry["path"])
        if entry["recovery"] is not None:
            names.add(entry["recovery"]["path"])
        diagnostic = entry["native_diagnostics"]
        if diagnostic is not None and diagnostic["sha256"] is not None:
            names.add(diagnostic["path"])
    for source in source_files:
        digest = source["sha256"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or source["path"] != f"source-{digest}.json"):
            raise ValueError("Chart source file must have its declared content identity")
        names.add(source["path"])
    return names


def chart_source_files(review):
    chart = review["chart"]
    return chart["source_files"] if chart is not None and chart["available"] else []


def _uncopyable_identity(path):
    """Observe an excluded path without following a symlink or reading a directory."""
    try:
        metadata = path.lstat()
    except OSError as error:
        return dict(error_type=type(error).__name__, errno=error.errno)
    return dict(device=metadata.st_dev, inode=metadata.st_ino, mode=metadata.st_mode,
                size=metadata.st_size, modified_ns=metadata.st_mtime_ns,
                link=str(path.readlink()) if stat.S_ISLNK(metadata.st_mode) else None)


def capture_saved(folder):
    """Capture declared original inputs; only accepted outcomes enter numerical data."""
    review = review_saved(folder)
    folder = Path(review["folder"])
    metadata = folder.lstat()
    identity = metadata.st_dev, metadata.st_ino
    payloads = {"batch.json": _read_regular(folder / "batch.json")}
    if hashlib.sha256(payloads["batch.json"]).hexdigest() != review["manifest_sha256"]:
        raise ValueError("Saved manifest changed during capture")
    source_files = chart_source_files(review)
    names = referenced_paths(review["manifest"], source_files)
    uncopyable = {}
    for name in sorted(names - payloads.keys()):
        try:
            payloads[name] = _read_regular(folder / name)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            uncopyable[name] = dict(reason=str(error), identity=_uncopyable_identity(folder / name))
    assert_review_files(review, payloads)
    missing = sorted(names - payloads.keys() - uncopyable.keys())
    # Revalidate canonical bytes through the shared frozen-input owner. The
    # initial optional receipt remains explicitly unverified informational data.
    frozen = review_captured(folder, payloads, unreadable=uncopyable)
    assert_captured(folder, identity, payloads, missing=missing, uncopyable=uncopyable)
    review = dict(frozen, **{key: review[key] for key in ("ti3", "export_state", "export_reason")})
    review["capture"] = dict(missing_files=missing, uncopyable_files=uncopyable)
    return review, identity, payloads


def assert_review_files(review, payloads):
    """Check captured bytes against the already authenticated review's identities."""
    if ("batch.json" not in payloads
            or hashlib.sha256(payloads["batch.json"]).hexdigest() != review["manifest_sha256"]):
        raise ValueError("Captured manifest differs from its authenticated review")
    for item in review["operations"]:
        if (item["review_state"] == "accepted"
                and hashlib.sha256(payloads.get(item["path"], b"")).hexdigest() != item["sha256"]):
            raise ValueError("Accepted operation changed during capture")
        if item["review_state"] == "terminal_unknown":
            recovery = item["recovery"]
            if (hashlib.sha256(payloads.get(recovery["path"], b"")).hexdigest()
                    != recovery["sha256"]):
                raise ValueError("Terminal recovery evidence changed during capture")
        if item["native_diagnostic_review"]["state"] == "authenticated":
            diagnostic = item["native_diagnostics"]
            if (diagnostic["path"] not in payloads
                    or hashlib.sha256(payloads[diagnostic["path"]]).hexdigest()
                    != diagnostic["sha256"]):
                raise ValueError("Native diagnostic original changed during capture")
    for source in chart_source_files(review):
        if (source["path"] not in payloads
                or hashlib.sha256(payloads[source["path"]]).hexdigest() != source["sha256"]):
            raise ValueError("Authenticated chart source changed during capture")


def assert_captured(folder, identity, payloads, *, missing=(), uncopyable=None):
    """Reauthenticate an ordinary source directory and exact retained file bytes."""
    def directory():
        metadata = folder.lstat()
        if (not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != identity):
            raise ValueError("Saved source directory changed")

    directory()
    for path, expected in payloads.items():
        if _read_regular(folder / path) != expected:
            raise ValueError("Saved source bytes changed; open the source again")
    for path in missing:
        try:
            (folder / path).lstat()
        except FileNotFoundError:
            continue
        raise ValueError("Missing saved source appeared; open the source again")
    for path, expected in (uncopyable or {}).items():
        if _uncopyable_identity(folder / path) != expected["identity"]:
            raise ValueError("Excluded saved source changed; open the source again")
        try:
            _read_regular(folder / path)
        except (OSError, ValueError):
            continue
        raise ValueError("Excluded saved source became readable; open the source again")
    directory()


class DatasetBinding:
    """One projection/analysis facade for a live owner or frozen read-only input.

    A saved binding holds accepted source bytes, not a writable BatchApplication.
    Its comparison and view choices are ephemeral. The live owner retains all
    admission, metadata publication, software-stop and acquisition ownership.
    """

    def __init__(self, *, owner=None, folder=None, source_file=None, format=None):
        if sum(value is not None for value in (owner, folder, source_file)) > 1:
            raise ValueError("Choose one dataset source")
        if (source_file is None) != (format is None):
            raise ValueError("Standalone source requires a file and explicit current format")
        self.owner = owner
        self.kind = ("live" if owner is not None else "saved" if folder is not None
                     else "standalone" if source_file is not None else "empty")
        self.id = str(uuid4()) if self.kind != "empty" else None
        self._generation = 0 if self.kind == "empty" else 1
        self.view = {}
        self._observations = {}
        self._hashes = {}
        self._dataset = None
        self._dataset_key = None
        self._commands = {}
        self._delegated_versions = {}
        self._inherited_annotations = {}
        self._inherited_signature = None
        self._ephemeral_context = None
        self._owner_context_version = owner.snapshot()["comparison"]["version"] if owner else None
        self._context_version = self._owner_context_version or 0
        self._archive_temporary = None
        self._archive_path = None
        self._archive_bytes = None
        self._standalone = None
        if self.kind == "standalone":
            self._standalone = StandaloneRecord(source_file, format=format)
            self._comparison = dict(
                version=0, reference=None, white=None, white_descriptor=None,
                available=True, reason=None, saved=False, persisted=False,
            )
            for record in self._standalone.observations:
                observation = asdict(record)
                pair = record.operation_id, record.observation_id
                self._observations[pair] = observation
                self._hashes[pair] = content_digest(observation)
        if self.kind == "saved":
            if Path(folder).is_file():
                from .batch_archive import unpack

                self._archive_path = Path(folder).resolve()
                self._archive_bytes = _read_regular(self._archive_path)
                self._archive_temporary = unpack(self._archive_bytes)
                folder = self._archive_temporary.name
            try:
                self._review, self._directory_identity, self._payloads = capture_saved(folder)
                self._missing_files = self._review["capture"]["missing_files"]
                self._uncopyable_files = self._review["capture"]["uncopyable_files"]
                if self._archive_path is not None and _read_regular(self._archive_path) != \
                        self._archive_bytes:
                    raise ValueError("Saved MAT archive changed during opening")
            except BaseException:
                self.close()
                raise
            self._folder = Path(self._review["folder"])
            self._comparison = dict(deepcopy(self._review["comparison"]), persisted=True)
            self._context_version = self._comparison["version"]
            for item in self._review["operations"]:
                if item["review_state"] == "accepted":
                    result = result_from_json(json.dumps(item["result"], allow_nan=False))
                    for patch in result.patches:
                        observation = asdict(patch.observation)
                        pair = observation["operation_id"], observation["observation_id"]
                        self._observations[pair] = deepcopy(observation)
                        self._hashes[pair] = content_digest(observation)
            self._load_inherited(self._review["inherited_operations"])

    def _load_inherited(self, operations):
        self._inherited_annotations = {}
        for item in operations:
            result = result_from_json(json.dumps(item["result"], allow_nan=False))
            overlays = {(annotation["operation_id"], annotation["observation_id"]): annotation
                        for annotation in item["annotations"]}
            for patch in result.patches:
                observation = asdict(patch.observation)
                reference = dict(segment_id=item["segment_id"],
                                 source_manifest_sha256=item["source_manifest_sha256"],
                                 operation_id=observation["operation_id"],
                                 observation_id=observation["observation_id"])
                identity = reading_identity(reference)
                self._observations[identity] = deepcopy(observation)
                self._hashes[identity] = content_digest(observation)
                self._inherited_annotations[identity] = dict(
                    deepcopy(overlays[(observation["operation_id"],
                                       observation["observation_id"])]),
                    reading_ref=reference,
                )

    def _held_inherited(self):
        """Use admitted immutable originals; no instrument or source-folder access."""
        with self.owner._mutex:
            chart = self.owner._data["chart"]
            sources = chart["sources"] if chart is not None else []
            signature = tuple((item["segment_id"], item["sha256"]) for item in sources)
            if signature == self._inherited_signature:
                return
            operations = []
            for source in sources:
                manifest = _manifest(self.owner._source_blobs[source["sha256"]])
                for (segment, operation_id), result in self.owner._source_results.items():
                    if segment == source["segment_id"]:
                        operations.append(dict(
                            segment_id=segment, source_manifest_sha256=source["sha256"],
                            result=asdict(result), annotations=manifest["annotations"],
                        ))
            self._load_inherited(operations)
            self._inherited_signature = signature

    def _sync_owner_context(self, state):
        current = state["comparison"]["version"]
        if current != self._owner_context_version:
            self._context_version = max(self._context_version + 1, current)
            self._owner_context_version = current
            self._ephemeral_context = None
        state["comparison"] = (deepcopy(self._ephemeral_context) if self._ephemeral_context else
                               dict(state["comparison"], version=self._context_version))

    @property
    def generation(self):
        return self.owner.snapshot()["generation"] if self.owner else self._generation

    def bind(self):
        if self.kind != "live":
            self._generation += 1

    def fence(self, binding_id, generation, *, authenticate=True):
        if binding_id != self.id or type(generation) is not int or generation != self.generation:
            raise ValueError("Dataset binding changed; refresh the current snapshot")
        if authenticate:
            self.assert_current()

    def assert_current(self):
        if self.kind == "standalone":
            self._standalone.assert_current()
        if self.kind == "saved":
            if self._archive_path is not None and _read_regular(self._archive_path) != \
                    self._archive_bytes:
                raise ValueError("Saved MAT archive bytes changed; open the source again")
            assert_captured(self._folder, self._directory_identity, self._payloads,
                            missing=self._missing_files, uncopyable=self._uncopyable_files)

    def close(self):
        """Retire only this binding's materialized read-only MAT source."""
        if self._archive_temporary is not None:
            self._archive_temporary.cleanup()
            self._archive_temporary = None

    def state(self, *, authenticate=True):
        if authenticate:
            self.assert_current()
        if self.kind == "live":
            state = self.owner.snapshot()
            self._held_inherited()
            self._sync_owner_context(state)
            state["annotations"].extend(self._inherited_summaries())
            return state
        if self.kind == "empty":
            return None
        if self.kind == "standalone":
            return dict(
                source_identity=dict(kind="standalone", format=self._standalone.format,
                                     source_sha256=self._standalone.sha256), revision=0,
                annotations=[dict(item, notes_preview="", notes_length=0, saved=True)
                             for item in self._standalone.annotations()],
                annotation_drafts=[], comparison=deepcopy(self._comparison),
            )
        manifest = self._review["manifest"]
        return dict(
            batch_id=manifest["batch_id"], context_id=manifest["context_id"],
            revision=manifest["revision"],
            annotations=[dict(
                operation_id=item["operation_id"], observation_id=item["observation_id"],
                name=item["name"], notes_preview=item["notes"][:256],
                notes_length=len(item["notes"]), version=item["version"], saved=True,
            ) for item in manifest["annotations"]
                if (item["operation_id"], item["observation_id"]) in self._observations]
                + self._inherited_summaries(),
            annotation_drafts=[], comparison=deepcopy(self._comparison),
        )

    def _inherited_summaries(self):
        return [dict(item, notes_preview=item["notes"][:256], notes_length=len(item["notes"]),
                     saved=True) for item in self._inherited_annotations.values()]

    def metadata(self):
        reason = None
        try:
            self.assert_current()
        except (OSError, ValueError, UnicodeError) as error:
            reason = str(error)
        state = self.state(authenticate=False)
        source = None
        review = None
        comparison = None
        if state is not None:
            if self.kind != "standalone":
                source = dict(batch_id=state["batch_id"], context_id=state["context_id"],
                              manifest_sha256=None)
            comparison = deepcopy(state["comparison"])
            if self.kind == "standalone":
                source = self._standalone.metadata()
                review = dict(accepted_readings=len(self._observations), excluded_operations=[])
            elif self.kind == "saved":
                source.update(
                    manifest_sha256=self._review["manifest_sha256"], folder=str(self._folder),
                    name=self._review["manifest"]["batch_name"],
                    descriptor=deepcopy(self._review["manifest"]["descriptor"]),
                    mode=self._review["manifest"]["mode"],
                )
                if self._archive_path is not None:
                    source.update(folder=None, path=str(self._archive_path), format="mat",
                                  archive_sha256=hashlib.sha256(self._archive_bytes).hexdigest())
                review = dict(
                    accepted_readings=len(self._observations),
                    excluded_operations=[dict(
                        operation_id=item["operation_id"], state=item["review_state"],
                        reason=item["review_reason"],
                    ) for item in self._review["operations"] if item["review_state"] != "accepted"],
                )
            else:
                source.update(folder=state["folder"], name=state["batch_name"],
                              descriptor=deepcopy(state["descriptor"]), mode=state["mode"])
                comparison["persisted"] = comparison["saved"]
        return dict(
            id=self.id, generation=self.generation, kind=self.kind,
            read_only=self.kind != "live" or self.owner._closed or state["status"] != "open",
            closed=self.owner is not None and self.owner._closed, source=source,
            review=review, comparison=comparison,
            available=reason is None, reason=reason,
            roster_revision=self._roster_revision(state),
        )

    def _roster_revision(self, state):
        if state is None:
            return "empty"
        identity = content_digest(state["source_identity"]) if self.kind == "standalone" \
            else state["batch_id"]
        return f"{identity}/{state['revision']}"

    def data(self):
        state = self.state()
        if state is None:
            return dict(readings={}, drafts={}, eligible_white_sources=[],
                        dataset_revision="empty", roster_revision="empty",
                        preview_scale=DEFAULT_PREVIEW_SCALE)
        key = self._roster_revision(state)
        if key != self._dataset_key:
            drafts = {}
            for item in state["annotations"]:
                pair = reading_identity(item.get("reading_ref", {name: item[name]
                                        for name in ("operation_id", "observation_id")}))
                if pair not in self._observations:
                    observation = self.owner.reading_details(*pair)
                    self._observations[pair] = observation
                    self._hashes[pair] = content_digest(observation)
            for item in state["annotation_drafts"]:
                if item["dirty"]:
                    pair = item["operation_id"], item["observation_id"]
                    drafts[_reading_key(*pair)] = self.owner.annotation_details(*pair)["draft"]
            identity = (state["source_identity"] if self.kind == "standalone" else dict(
                kind="batch", batch_id=state["batch_id"], context_id=state["context_id"],
            ))
            # Repeat lineage and chart cells follow each source's own operations,
            # never the live batch's, so saved review keeps the labels it recorded.
            operations = (state["operations"] if self.kind == "live" else
                          self._review["manifest"]["operations"] if self.kind == "saved" else ())
            labels = {item["operation_id"]: dict(repeat_of=item["repeat_of"])
                      for item in operations}
            for operation_id, cell in self._chart_cells().items():
                labels.setdefault(operation_id, {})["chart_cell"] = cell
            self._dataset = project_dataset(
                state["annotations"], self._observations, self._hashes, drafts, key,
                source_identity=identity, operation_labels=labels,
            )
            self._dataset_key = key
        return deepcopy(self._dataset)

    def annotation(self, operation_id, observation_id):
        self.assert_current()
        if self.owner:
            return self.owner.annotation_details(operation_id, observation_id)
        if self.kind not in {"saved", "standalone"} or \
                (operation_id, observation_id) not in self._observations:
            raise ValueError("Unknown authenticated reading identity")
        annotations = (self._standalone.annotations() if self.kind == "standalone"
                       else self._review["manifest"]["annotations"])
        item = next(item for item in annotations
                    if (item["operation_id"], item["observation_id"]) ==
                    (operation_id, observation_id))
        return deepcopy(dict(operation_id=operation_id, observation_id=observation_id,
                             applied=dict(item, saved=True), draft=None))

    def annotation_reference(self, reference):
        data = self.data()
        resolve_reading(data["readings"], reference)
        identity = reading_identity(reference)
        if len(identity) == 2:
            return self.annotation(*identity)
        annotation = self._inherited_annotations[identity]
        return deepcopy(dict(reading_ref=reference, operation_id=reference["operation_id"],
                             observation_id=reference["observation_id"],
                             applied=dict(annotation, saved=True), draft=None, read_only=True))

    def context(self, version):
        self.assert_current()
        if self.owner:
            self.state()
            if type(version) is not int or version != self._context_version:
                raise ValueError("Comparison version changed")
            if self._ephemeral_context is not None:
                return deepcopy(self._ephemeral_context)
            return dict(self.owner.comparison_context(self._owner_context_version),
                        version=self._context_version)
        if self.kind not in {"saved", "standalone"}:
            raise ValueError("Choose a dataset first")
        if type(version) is not int or version != self._comparison["version"]:
            raise ValueError("Comparison version changed")
        if not self._comparison["available"]:
            raise ValueError(self._comparison["reason"])
        return deepcopy(self._comparison)

    def set_context(self, command_id, version, reference, white):
        _uuid(command_id)
        self.state()
        signature = content_digest(dict(version=version, reference=reference, white=white))
        if command_id in self._commands:
            old_signature, outcome = self._commands[command_id]
            if signature != old_signature:
                raise ValueError("Analysis command ID reused with different content")
            if command_id not in self._delegated_versions:
                return deepcopy(outcome)
            state = self.owner.snapshot()
            return self.owner.command(command_id, state["generation"], "set_comparison_context",
                                      dict(version=self._delegated_versions[command_id],
                                           reference=reference, white=white),
                                      batch_id=state["batch_id"])
        qualified = any(isinstance(value, dict) and "segment_id" in value
                        for value in (reference, white))
        if self.owner and not qualified:
            if type(version) is not int or version != self._context_version:
                raise ValueError("Comparison version changed")
            state = self.owner.snapshot()
            native_version = self._owner_context_version
            outcome = self.owner.command(command_id, state["generation"], "set_comparison_context",
                                         dict(version=native_version, reference=reference,
                                              white=white), batch_id=state["batch_id"])
            self._commands[command_id] = signature, outcome
            self._delegated_versions[command_id] = native_version
            if outcome["admission"] in {"admitted", "no_change"}:
                self._sync_owner_context(self.owner.snapshot())
                if self._ephemeral_context is not None:
                    # An unchanged native choice can still replace an inherited
                    # analysis choice. Its binding revision must advance once.
                    self._ephemeral_context = None
                    self._context_version += 1
            return outcome
        self.assert_current()
        if self.kind == "empty":
            raise ValueError("Choose a dataset first")
        if type(version) is not int or version != self._context_version:
            raise ValueError("Comparison version changed")
        readings = self.data()["readings"]
        if reference is not None:
            key = resolve_reading(readings, reference)
            if not readings[key]["saved"]:
                raise ValueError("Comparison reference requires a saved authenticated reading")
        descriptor = dataset_white_descriptor(readings, white)
        context = dict(version=version + 1, reference=deepcopy(reference), white=deepcopy(white),
                       white_descriptor=descriptor)
        self._context_version = context["version"]
        current = dict(context, available=True, reason=None, saved=False, persisted=False)
        if self.owner:
            self._ephemeral_context = current
        else:
            self._comparison = current
        outcome = dict(admission="admitted", state="completed", effect="analysis_context_changed",
                       comparison=deepcopy(current))
        self._commands[command_id] = signature, outcome
        return deepcopy(outcome)

    def analyze(self, method, params):
        data = validate_analysis(method, params, self.data)
        context = self.context(params["comparison_version"])
        result = analyze_dataset(method, params, data, context)
        self.assert_current()
        return result

    def capture(self):
        if self.kind == "standalone":
            return self._standalone.capture()
        if self.kind == "saved":
            self.assert_current()
            return dict(deepcopy(self._review), kind="batch"), self._directory_identity, \
                dict(self._payloads)
        if self.owner is None:
            raise ValueError("Choose a dataset first")
        state = self.owner.snapshot()
        if state["phase"] != "idle" or state["save_reason"]:
            raise ValueError("Save and stop current work before exporting")
        with self.owner._mutex:
            expected = self.owner._manifest_bytes
            expected_directory = self.owner._folder_identity
        review, identity, payloads = capture_saved(state["folder"])
        if identity != expected_directory or payloads["batch.json"] != expected:
            raise ValueError("Live manifest cannot be authenticated")
        with self.owner._mutex:
            if self.owner._manifest_bytes != expected:
                raise ValueError("Live manifest changed during capture")
        assert_captured(Path(review["folder"]), identity, payloads,
                        missing=review["capture"]["missing_files"],
                        uncopyable=review["capture"]["uncopyable_files"])
        return dict(review, kind="batch"), identity, payloads

    def assert_capture(self, header, identity, payloads):
        """Authenticate one frozen batch or standalone capture through its real file owner."""
        if not isinstance(header, dict) or not isinstance(payloads, dict):
            raise ValueError("Captured source header and payload mapping must be objects")
        if self.kind not in {"live", "saved", "standalone"} or header.get("kind") != (
            "standalone" if self.kind == "standalone" else "batch"
        ):
            raise ValueError("Captured source kind differs from the bound dataset")
        # Reuse the bound owner's capture authentication, including batch witnesses.
        # A valid capture from another directory must not authenticate this binding.
        expected, actual_identity, actual_payloads = self.capture()
        if self.kind != "standalone":
            # These optional receipt fields are explicitly unverified by capture_saved.
            # Their progress cannot change the frozen canonical source qualification.
            informational = {"ti3", "export_state", "export_reason"}
            header = {key: value for key, value in header.items() if key not in informational}
            expected = {key: value for key, value in expected.items() if key not in informational}
        if (content_digest(header) != content_digest(expected)
                or identity != actual_identity or payloads != actual_payloads):
            raise ValueError("Captured source differs from its bound original typed header/bytes")

    def derive(self, *, dataset_revision, selected, derivation):
        from .qualified_science import derive_readings

        data = self.data()
        if dataset_revision != data["dataset_revision"]:
            raise ValueError("Reading revision changed")
        result = derive_readings(data["readings"], selected, deepcopy(derivation))
        self.assert_current()
        if self.data()["dataset_revision"] != dataset_revision:
            raise ValueError("Dataset changed during derivation")
        return dict(result, dataset_revision=dataset_revision)

    def _chart_cells(self):
        """Chart cell labels for table rows; an absent or unavailable chart has none."""
        if self.owner:
            # Read under the owner's lock; only the small label map leaves it.
            with self.owner._mutex:
                chart = self.owner._data["chart"]
                return {} if chart is None else chart_state.reading_cells(chart)
        if self.kind == "saved" and (self._review["chart"] is None
                                       or self._review["chart"]["available"]):
            chart = self._review["manifest"]["chart"]
        else:
            chart = None
        return {} if chart is None else chart_state.reading_cells(chart)

    def _chart(self):
        self.assert_current()
        if self.owner:
            with self.owner._mutex:
                chart = deepcopy(self.owner._data["chart"])
        elif self.kind == "saved":
            review = self._review["chart"]
            if review is not None and not review["available"]:
                raise ValueError(review["reason"])
            chart = self._review["manifest"]["chart"]
        else:
            chart = None
        if chart is None:
            raise ValueError("This dataset has no guided chart")
        return chart

    def chart_view(self, pass_id=None):
        if pass_id is not None:
            _uuid(pass_id)
        return chart_state.view(self._chart(), pass_id=pass_id)

    def chart_colors(self, pass_id, color_space, *, expected_context=None):
        """Compose selected native and declared TI2 swatches through their existing owners."""
        from .chart_science import chart_expected_colors, chart_reading_colors
        from .chart_targets import _expected_context
        from .models import observation_from_dict
        from .presentation import RESOURCE
        from .science import _rgb_space

        _rgb_space(color_space)
        if expected_context is not None and not isinstance(expected_context, dict):
            raise ValueError("Expected chart context must be an object or null")
        expected_context_json = json.dumps(expected_context, allow_nan=False)
        _expected_context(expected_context_json)
        if pass_id is not None:
            _uuid(pass_id)
        chart = self._chart()

        def canonical_revision():
            if self.owner:
                with self.owner._mutex:
                    return f"{self.owner._data['batch_id']}/{self.owner._data['revision']}"
            manifest = self._review["manifest"]
            return f"{manifest['batch_id']}/{manifest['revision']}"

        source_revision = canonical_revision()
        chosen = chart["active_pass"] if pass_id is None else pass_id
        wrappers = self.chart_readings(chosen)
        observer = RESOURCE.read_bytes()
        measured = []
        for wrapper in wrappers:
            result = chart_reading_colors(
                dict(wrapper, observation=observation_from_dict(wrapper["observation"])),
                observer_csv=observer, color_space=color_space,
            )
            # Original records and accepted context remain in chart_details.
            # The swatch DTO carries their exact identities, not another raw copy.
            source = result["source"]
            result["source"] = dict(
                reading_ref=source["reading_ref"],
                observation_sha256=content_digest(source["observation"]),
                accepted_context_sha256=content_digest(source["accepted_context"]),
            )
            measured.append(result)
        expected = chart_expected_colors(chart["definition"], expected_context_json,
                                         color_space=color_space)
        if self._chart()["revision"] != chart["revision"]:
            raise ValueError("Chart revision changed during color query")
        if canonical_revision() != source_revision:
            raise ValueError("Dataset revision changed during chart color query")
        return dict(pass_id=chosen, chart_revision=chart["revision"],
                    definition_id=chart["definition"]["id"], color_space=color_space,
                    source_revision=source_revision,
                    observer_sha256=hashlib.sha256(observer).hexdigest(),
                    expected=expected, measured=measured)

    def chart_history(self, pass_id, cell, *, offset=0, limit=50):
        _uuid(pass_id)
        self._chart()
        if self.owner:
            return self.owner.chart_history(pass_id, cell, offset=offset, limit=limit)
        return chart_state.history(self._chart(), pass_id, cell, offset=offset, limit=limit)

    def chart_details(self, pass_id, attempt_id):
        _uuid(pass_id)
        _uuid(attempt_id)
        self._chart()
        if self.owner:
            return self.owner.chart_details(pass_id, attempt_id)
        return chart_state.attempt_details(self._chart(), attempt_id, pass_id=pass_id)

    def chart_decision_details(self):
        self._chart()
        if self.owner:
            return self.owner.chart_decision_details()
        return chart_state.decision_details(self._chart())

    def chart_readings(self, pass_id=None):
        if pass_id is not None:
            _uuid(pass_id)
        chart = self._chart()
        if self.owner:
            return [dict(wrapper, observation=asdict(wrapper["observation"]))
                    for wrapper in self.owner.chart_readings(pass_id)]
        chosen = chart["active_pass"] if pass_id is None else pass_id
        matches = [item for item in chart["passes"] if item["id"] == chosen]
        if len(matches) != 1:
            raise ValueError("Unknown chart pass")
        selected = matches[0]
        attempts = {item["id"]: item for item in selected["attempts"]}
        readings = self.data()["readings"]
        wrappers = []
        for identity in selected["selected_attempts"]:
            if identity is None:
                continue
            attempt = attempts[identity]
            original = attempt["readings"][0]
            reference = {key: original[key] for key in ("operation_id", "observation_id")}
            if original["segment_id"] != self._review["manifest"]["batch_id"]:
                digest = next(source["sha256"] for source in chart["sources"]
                              if source["segment_id"] == original["segment_id"])
                reference.update(segment_id=original["segment_id"], source_manifest_sha256=digest)
            observation = readings[resolve_reading(readings, reference)]["observation"]
            if observation["sample_id"] != original["sample_id"]:
                raise ValueError(
                    "Selected chart reading differs from its original native sample ID")
            wrappers.append(dict(cell_index=attempt["cell"], observation=deepcopy(observation),
                                 reading_ref=deepcopy(original),
                                 accepted_context=deepcopy(attempt["accepted_context"])))
        return wrappers

    def operation_details(self, command_id):
        _uuid(command_id)
        self.assert_current()
        if self.owner:
            return self.owner.operation_details(command_id)
        if self.kind == "standalone":
            raise ValueError("A standalone record has no application command receipt")
        if self.kind != "saved":
            raise ValueError("Choose a dataset first")
        matches = [item for item in self._review["operations"] if item["command_id"] == command_id]
        if len(matches) != 1:
            raise ValueError("Unknown operation command")
        return deepcopy(dict(matches[0], read_only=True))

    def activity(self):
        from .activity import activity

        return activity(self)

    def activity_details(self, key, activity_revision):
        from .activity import activity_details

        return activity_details(self, key, activity_revision)
