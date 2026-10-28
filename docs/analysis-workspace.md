# Retained-data analysis workspace

`DesktopSession` keeps its actual live `BatchApplication` while selecting one
`DatasetBinding` for display and analysis. A saved binding reads canonical
schema-5 batch files without acquiring a frontend, creating a writable owner,
resuming an instrument or writing annotations. Existing live drafts, retained
results, save failures and software-stop ownership remain with the live batch.

## Native source selection and renderer fences

`open_saved(folder, *, expected_binding_id, expected_generation)` receives a
native folder-dialog result and the binding captured for that dialog. It refuses
stale binding/generation and current active/loading work. It authenticates the
manifest and only final `review_state="accepted"` operations, including their
exact bytes, original pairs, frozen context and annotations. A parsed nonnull
result from failed review is excluded, with the final diagnostic retained.
The saved binding's `review.accepted_readings` counts authenticated local and
inherited observations in its dataset. The canonical review's `sample_count`
retains its separate meaning: accepted observations in the current segment.

The native `activate_live(*, expected_binding_id, expected_generation)` restores
the preserved current owner and its view. It can leave a changed saved source;
source damage does not hide or authorize discarding current live work.
Native source methods cannot be invoked through renderer filesystem paths.

Every snapshot retains `batch` as the real native owner and exposes a separate
`dataset_binding` with id/generation, empty/live/saved kind, read-only status,
source identity, roster revision, comparison and review facts. `available=false`
and `reason` retain a changed-source diagnostic without making up empty measured
data. Dataset and numerical queries recheck exact source bytes and refuse reuse
after source replacement or mutation. These are bounded read-only checks, not a
multi-file filesystem transaction or authenticity of physical measurements.
Canonical source capture revalidates its frozen bytes through the shared saved
review owner and checks every captured path, missing path and excluded-file
witness against the source directory. Optional TI3 receipt progress does not
change the captured native inputs. Its initial receipt metadata remains separate
and explicitly unverified; it cannot authenticate those inputs or their exports.
Returned operation, diagnostic, chart and comparison qualification comes from
that frozen review, so a source becoming readable during capture cannot leave
the returned reading set inconsistent with its captured bytes or MAT readback.

Renderer data/view requests use required `binding_id` and integer `generation`.
Projection/analysis also carry `dataset_revision`, `comparison_version` and
positive finite `scale`; analysis carries unique selected paired keys. The
scientific revision excludes names, notes and view changes. Identity, roster
freshness, comparison version and scientific identity have distinct purposes.
The shared projection receives original annotations and an explicit source identity.
Batch identity contains the real batch and context UUIDs. Standalone identity
contains its declared format and original file SHA-256; it has no invented batch
or context UUID. Both identities combine with original observation hashes in the
scientific revision. Dictionary ordering does not change that revision. Projection
does not read or authenticate a file; the selected-source owner supplies those facts.

`set_analysis_context` takes `command_id`, `comparison_version`, original-pair
`reference` and explicit `white` besides the binding fence. For a live owner it
uses existing command deduplication and saved comparison publication. For a saved
source it validates the same declaration through the existing white/comparison
owners, increments an ephemeral version and leaves source files unchanged.
Repeated command content is idempotent; changed content under the same ID refuses.
Saved reference/white analysis requires authenticated available comparison input.
After an inherited live choice, a local-only choice again uses the live owner's
saved publication. Clearing roles or choosing a theoretical white follows the
same rule. The binding revision advances even when those choices already match
the saved owner context; a refused command retains the inherited choice.

Inherited chart observations use an explicit `reading_ref` containing segment ID,
original source manifest SHA-256, and unchanged native operation/observation IDs.
Local readings retain their ordinary native pair. DTO keys include inherited
qualifiers, so equal native IDs from different original sources do not collapse.
The original observation receives no added or replaced identifiers. Inherited
annotations are read-only; `annotation_reference` returns their full original
metadata without invoking the live owner's local annotation editor.

Qualified references also work in ephemeral live analysis. They never enter the
live owner's persisted ordinary comparison or reading-details interfaces, which
continue to accept local pairs. Dataset comparison revisions advance monotonically
through both ephemeral choices and later persisted owner changes; an old ephemeral
revision cannot accidentally authorize a newer ordinary context. Live inherited
views reuse admitted immutable originals held by the owner. Export additionally
reauthenticates the complete declared source files.

The same binding exposes `chart_view`, `chart_history`, `chart_details`,
`chart_decision_details`, `chart_readings` and local `operation_details`. Saved
queries reauthenticate their input and delegate to the shared chart-state owner;
live queries delegate to the held batch owner. History stays chronological and
bounded to 50 attempt identities per page. Selected reading wrappers preserve
their exact original chart `reading_ref`, cell and accepted context. These queries
create no writable owner or acquisition.

`activity()` returns a compact, source-bound roster with `activity_revision`,
`source`, `entries` and an unavailable-source `reason`. Operations retain separate
native status, intent state, review/exclusion and save facts, including failed,
cancelled and zero-reading results and raw terminal recovery. Standalone
observations have no invented command, workflow or operation status. Inherited
entries retain the original segment and manifest digest. Only currently qualified
`reading_refs` support a reading jump; a parsed excluded result remains diagnostic
evidence; its reported reading count remains separate from qualified jump targets.
`activity_details(key, activity_revision)` rechecks that same source and
revision before and after inspection, returning original result/recovery, exact
applied annotations and drafts, unchanged observations and coordinate reasons.
Live events are scoped to the operation within the owner's latest 32 batch events;
saved/imported records do not acquire
a reconstructed application timeline. Optional diagnostics keep unmodified
capture metadata, file review and retention reasons separately. On-demand originals
are exact base64 bytes plus `{source_offset, length, base64}` retained segments;
truncation and capture errors remain visible, including when live memory retains
an original whose file publication failed. Roster polling carries no native bytes
or full note text. These queries acquire, publish and alter nothing.

`chart_colors(pass_id, color_space, *, expected_context=None)`
composes the existing chart-science expected and selected-reading color owners.
It retains common-white scale, observer, clipping and unavailable reasons. TI2
expected colors require the existing explicit reference-context declaration;
the default null supplies no assumed white or units. A declaration is a typed
object with `observerId`, `xyzUnits`, `xyzScale`, `whiteXYZ`, `illuminant`,
`interpretation`, `meaning` and `sourceFields`. Native selected readings
remain separate from expected values. The DTO includes source and chart revisions, canonical
definition identity, actual observer-resource SHA-256, and source reading/context
digests. Full original observations remain available through the existing detail
queries. A source change or chart revision change during calculation refuses.
The source revision follows canonical batch changes, separately from live roster
or optional-export status progress. Companion serialization and publication do
not invalidate unchanged selected native colors.

## JSON, CSV and MAT export

The native host calls `export_analysis(path, *, binding_id, generation,
dataset_revision, comparison_version, scope, selected, scale, format,
derivation=None)`. `scope` is `selected` or `all`; `all` requires an empty
selection array. Selected keys must be unique and resolve to saved authenticated
inputs. `format` is `json`, `csv` or `mat`. The renderer cannot select an output path.
MAT uses the existing optional NumPy/SciPy owner. `formats` reports actual
runtime availability with an unavailable reason; a producer capability does not
establish that a native bundle includes or has qualified those dependencies.

Export uses the same projection/analysis composition as live display. It freezes
source bytes, paired IDs, full applied annotations, source hashes, selection,
reference/white, scale and methods. Raw XYZ/spectra and native observations remain
separate from xyY/u′v′, Lab, ΔE00, signed native-Y percentage, coordinate distance,
signed XYZ/xy/u′v′/Lab differences, chroma and hue differences, exact-grid spectral
residual/RMS and illustrative RGB/clipping. Missing values
remain null with reasons. Full notes come from authenticated annotation detail,
never the 256-character row preview. Out-of-selection reference and white inputs
remain explicitly labelled in `role_inputs`, including their exact native records.

JSON schema 1 is an analysis snapshot, separate from batch schema 5, native
operation schema 1 and TI3 snapshot schema 3. Its `source` identifies either an
authenticated batch or a standalone original operation/observation. Batch source
metadata retains its manifest, descriptor and mode; standalone source metadata
retains only the original file, validated record and device facts. A standalone
observation retains its native operation ID, but does not acquire an enclosing
operation status, repeat relation, batch or descriptor.

`source_documents` stores each needed original JSON document once, keyed by its
SHA-256. Each entry contains `kind` and `canonical_json`. Batch records and roles
refer to their exact original operation through `operation_document`. Standalone
records also retain `native_input` with input kind/format, original file SHA-256
and `document_sha256`; that document describes the validated original operation
or observation. The standalone `original_file` retains exact original bytes once
as `{encoding: "base64", data, sha256}`, including original JSON whitespace or
MAT container bytes. An observation has null enclosing-operation SHA-256,
status, repeat relation and `operation_document`. Its original native identifiers,
quantities, comparison assumptions and separate derived fields remain available.
Batch exports have a null `original_file`. Failed original operation statuses
remain failed; export does not relabel them as passed measurements.

CSV provides inspectable native XYZ/unit/scale, derived coordinates and metrics,
plus JSON cells for each record and its derived values. Only its first data row
contains `source_documents_json`, with the common `source_documents`,
`original_file`, full `role_inputs` and full `derived_science` packets. Later rows
have an empty cell in that column. Per-row `export_context_json` retains source
and comparison metadata, scientific methods, summaries, compact role references
and derivation identity/digests. It does not repeat the complete original
documents, base64 bytes or all selected derivation results for every row. Original
stored-chart and X-Y documents therefore remain one copy even when many readings
refer to the same operation. Convenience text escapes spreadsheet formula
prefixes; numerical signed values remain numerical. Empty convenience cells
correspond to unavailable values, with null/reasons in the JSON fields rather
than invented zeros.

Publication uses the existing exclusive new-file owner and never overwrites an
existing destination. `prepare_analysis` returns its frozen capture header,
identity and payloads alongside the snapshot. `DatasetBinding.assert_capture`
authenticates that same source before and after preparation and before publication,
for both batches and standalone files. The reply reports
file/source digests, binding, selection and observed file/directory synchronization
separately. `AnalysisExportError` retains exact prepared bytes and any publication
receipt after I/O failure. Select a new native filename for retry. A synchronization
receipt is not a power-loss, external acceptance or physical accuracy claim.

MAT analysis is a current level-5 document container with kind/schema, canonical
JSON and exact content digest. It preserves nulls, Unicode, raw operation JSON,
native quantities and all context without a contradictory numeric convenience
copy. `document_mat.from_mat(payload, kind="analysis")` verifies this declared
format. Operation MAT remains the separately implemented operation envelope and
typed inspection view; arbitrary MATLAB session files are not imported.

After publication failure the session holds the prepared bytes and context under
an opaque `recovery_id`. Snapshot `pending_exports` and path-free
`export_recovery` expose metadata, reason and payload identity, never raw bytes.
Native `retry_analysis_export(path, *, recovery_id)` saves that exact earlier
snapshot without recomputing it, even after its original source changed. Its
receipt explicitly says `retained_snapshot=true`; it is not a fresh current view.
Native `discard_analysis_export(*, recovery_id, payload_sha256)` discards one
exact pending payload. Native Close must resolve pending exports, including a
saved-only session with no live batch, before discarding its process lifetime.

Native `export_batch_mat(path, *, binding_id, generation)` archives exact current
manifest/operation bytes, every closed diagnostic original and every referenced terminal recovery original,
including available excluded or damaged bytes and original missing-file facts.
Only declared canonical inputs are archived; unrelated folder files are excluded.
Guided-chart archives also retain the complete authenticated parent source
closure, preserving each content-addressed original even when its bytes are
binary or empty. An unauthenticated parent graph refuses whole-batch archiving
without hiding healthy local readings. Reopening authenticates that same graph
before accepting the archive's exact declared inventory.
The archive requires an integer schema and binds its declared batch/context
identities to the authenticated original manifest. Saved bindings recheck all
captured originals and refuse if an absent declared input appears later.
An excluded nonregular or unreadable input retains its diagnostic and does not
hide healthy readings. Its unchanged filesystem identity is rechecked without
following symlinks. Whole-batch export refuses such uncopyable originals; it does
not replace them with invented bytes or missing-file claims.
Opening that MAT with `open_saved` materializes only an owned
temporary read-only source and runs the ordinary current batch validator. It
creates no writable batch or acquisition owner. The original MAT file bytes are
rechecked; source changes make the binding unavailable. Return to live retires
only this owned materialization. The MAT archive is separate from analysis MAT
and operation MAT, and supplies no earlier-development-schema compatibility.

The native publication owner can prepare companions from an already authenticated
frozen capture through `batch_archive.prepare_from_capture(review, files,
missing_files=())`. It returns independent `mat`, `csv` and `chart_selected_csv`
payload/reason pairs plus the original manifest SHA-256. The helper performs no
acquisition, filesystem publication or scientific calculation. Manual MAT
preparation uses the same `encode` implementation. A failed MAT dependency does
not prevent native CSV preparation; a batch without a chart explicitly has no
selected-chart CSV. The publication owner retains prepared bytes for retry,
including MAT's original dated header.

`native_csv(review, selected_passes=None)` retains typed native observations and full
applied annotations in JSON cells, plus native XYZ convenience columns with
declared units and scale. Non-color records have empty XYZ cells and retain their
native scalar/timing quantities, including genuine zeros, in the JSON record.
Empty batches have a header-only table. Text convenience cells use the shared
spreadsheet-literal escaping owner; original text remains in JSON. Each reading
retains segment, operation and observation IDs and its source manifest digest.
An explicit sequence of chart pass IDs selects each pass's original reading
wrappers in pass and canonical cell order, with `pass_id`, cell index and exact
`reading_ref_json`. Automatic `chart-selected.csv` includes every pass;
ordinary CSV includes all readings and leaves `pass_id` empty. Inherited annotations remain
original source metadata. Generated companions are outside the canonical archive
inventory, preventing recursive inclusion of earlier MAT files.

## Explicit derived science

Renderer `derive` takes binding/generation, scientific `dataset_revision`, unique
`selected` keys and a `derivation` object with exactly `method` and `context`.
Derived values return separately, with source-observation/context hashes and
independent unavailable reasons; native observations never receive enrichment.
The same optional request can be captured in an export.

Dataset rows dispatch the current record kinds: color, scalar and timing.
Color projections receive only native color inputs. Scalar/timing retain their
original native quantity, stimulus, calibration and operational context; timing
also retains its client presentation and worker clock facts separately. They
have unavailable color coordinates/Lab/previews/comparisons with reasons, never
invented XYZ or spectra. CSV names each record kind and keeps complete native,
stimulus and timing-context JSON beside empty color convenience cells. A genuine
native zero timing remains zero in its own typed quantity.

For `method="adapt_xyz"`, context contains explicit `source_white` and
`destination_white` using the existing retained-reading or
`theoretical-lab-reference` declarations. Observer, unit and scale must match.
Retained source-white conditions must match the native sample. The accepted
[full linear Bradford owner](science.md#full-linear-bradford-adaptation) normalizes
both adopted whites to Y=1 and preserves the native sample scale:

\[
X' = B^{-1}\operatorname{diag}((BW_d/Y_d)/(BW_s/Y_s))BX,
\qquad W_{Lab}=W_d(Y_s/Y_d).
\]

The separate Lab result uses this declared destination chromaticity at the source
reference Y. Adapting a source white with Y=203 toward a destination declaration
with Y=100 preserves Y=203 and yields Lab (100,0,0) under that matching Lab white.
The destination declaration remains Y=100 in metadata. If the normalized Lab
white is nonfinite or not strictly positive, Lab and its white are independently
unavailable with a reason; valid adapted XYZ remains available. This is a corresponding
color calculation, not spectral relighting, a display/profile transform, clipping
or an appearance model. The primary method is ICC.1:2004-10 Annex E.3,
[equations E.1–E.4](https://www.color.org/ICC1V42.pdf#page=102).

For `method="spectral_xyz"`, context contains `observer`,
`wavelength_convention`, nullable `illuminant`, nullable `illuminant_id` and boolean
`nonfluorescent_reflectance`. The accepted spectral owner requires CIE1931_2,
explicit standard-air-nm and authenticated original CIE CSV bytes. Actual typed
radiance/irradiance inputs retain W/mW conversion, absolute output units and
native grids. Limited ranges remain explicit; unknown medium/observer, flash
integrals, transmittance or unsupported grids return unavailable reasons.
An unreadable observer resource likewise returns a per-source unavailable reason
and a null derived result, without borrowing native XYZ or changing the retained
observation. The shared observer loader supplies the same explicit reason to
spectral integration and relighting.

Reflectance requires an explicitly declared nonfluorescent-factor model and an
identified, nonnegative, unit-one relative-power SPD on the exact native grid.
A nominal XYZ white is not an SPD. The illuminant identity is caller-declared,
not physically authenticated by its name. No native or illuminant resampling,
missing-band invention, automatic enrichment or physical calibration claim occurs.
See the spectral owner's equations, numerical tests and limitations in
[the scientific methods](science.md), the authenticated
[CIE table](https://doi.org/10.25039/CIE.DS.xvudnb9b), and
[BIPM/CIE G1 (2019)](https://www.bipm.org/documents/20126/52718503/G1-2019.pdf).

The following excerpt is the actual shared consumer. Tests exercise retained
Demo input, exact source preservation, explicit adopted-white scale, unsupported
context, limited radiance range and a labelled synthetic same-grid SPD. They
establish software composition, not MATLAB numerical equivalence or physical
instrument/display validity.

<!-- source: src/color_measurement/qualified_science.py:derive_readings -->
```python
def derive_readings(readings, selected, request):
    """Compose separate corresponding-color or spectral XYZ results; never enrich records.

    Bradford's normalized whites preserve the native sample scale. The Lab
    reference is destination_white/destination_white_Y*source_white_Y, using
    the library's declared convention. Spectral integration retains the original
    grid and uses only explicit observer, wavelength medium and optional SPD.
    Equations, primary sources and numerical limits remain at the science owner.
    """
    _fields(request, {"method", "context"})
    method, context = request["method"], request["context"]
    if method == "adapt_xyz":
        _fields(context, {"source_white", "destination_white"})
        source = dataset_white_descriptor(readings, context["source_white"])
        destination = dataset_white_descriptor(readings, context["destination_white"])
        if source is None or destination is None:
            raise ValueError("Adaptation requires two explicit adopted whites")
        if any(source[key] != destination[key] for key in ("unit", "scale", "observer")):
            raise ValueError("Adopted-white observer, unit and scale must match")
        lab_white = tuple(value / destination["reference_y"] * source["reference_y"]
                          for value in destination["xyz"])
        lab_white_reason = None
        if any(not math.isfinite(value) or value <= 0 for value in lab_white):
            lab_white = None
            lab_white_reason = (
                "Normalized destination Lab white is not finite and strictly positive"
            )
    elif method == "spectral_xyz":
        _fields(context, {"observer", "wavelength_convention", "illuminant", "illuminant_id",
                          "nonfluorescent_reflectance"})
        if type(context["nonfluorescent_reflectance"]) is not bool:
            raise ValueError("Declare the nonfluorescent-reflectance assumption explicitly")
        illuminant = _spectrum(context["illuminant"])
    elif method == "characterized_reflectance":
        reference, characterization = _reference_context(readings, context)
    elif method == "relight_reflectance":
        _fields(context, {"reflectance", "illuminant", "illuminant_id", "observer",
                          "wavelength_convention", "nonfluorescent_reflectance", "color_space"})
        if type(context["nonfluorescent_reflectance"]) is not bool:
            raise ValueError("Declare the nonfluorescent-reflectance assumption explicitly")
        if any(not isinstance(context[field], str)
               for field in ("observer", "wavelength_convention")):
            raise ValueError("Declare observer and wavelength convention as text")
        if context["color_space"] is not None and not isinstance(context["color_space"], str):
            raise ValueError("Corresponding-color encoding must be text or null")
        if context["illuminant_id"] is not None and not isinstance(context["illuminant_id"], str):
            raise ValueError("Illuminant identity must be text or null")
        if not isinstance(context["illuminant"], (str, dict)):
            raise ValueError("Declare a CIE illuminant name or a typed target SPD")
        basis = context["reflectance"]
        if (not isinstance(basis, dict)
                or basis.get("kind") not in {"native", "characterized-reference"}):
            raise ValueError("Choose native reflectance or an explicit characterized reference")
        _fields(basis, {"kind"} if basis["kind"] == "native" else {"kind", "context"})
        if basis["kind"] == "characterized-reference":
            reference, characterization = _reference_context(readings, basis["context"])
    else:
        raise ValueError(
            "Choose adapt_xyz, spectral_xyz, characterized_reflectance or relight_reflectance",
        )
    if method in {"spectral_xyz", "relight_reflectance"}:
        observer_error = None
        try:
            observer_csv = RESOURCE.read_bytes()
        except OSError as error:
            observer_csv, observer_error = None, f"Observer resource is unavailable: {error}"
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(key, str) or key not in readings for key in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError("Choose unique retained readings for derivation")
    output = {}
    for key in selected:
        row = readings[key]
        observation = row["observation"]
        result, reason = None, None
        try:
            if not row["saved"]:
                raise ValueError("Save the input before deriving a source-bound result")
            if _record_kind(observation) != "color":
                raise ValueError("Color derivation requires a retained color observation")
            if method == "adapt_xyz":
                native = observation["backend_reported"]["xyz"]
                if native is None:
                    raise ValueError("Native XYZ is unavailable")
                if any(native[field] != source[field] for field in ("unit", "scale", "observer")):
                    raise ValueError("Native XYZ and adopted-white observer/unit/scale differ")
                if source["kind"] == "retained-reading" and native["illuminant"] != \
                        source["illuminant"]:
                    raise ValueError("Native XYZ and retained source-white conditions differ")
                values = adapt_xyz(native["values"], source["xyz"], destination["xyz"])
                result = dict(
                    values=values, origin="derived", unit=native["unit"], scale=native["scale"],
                    observer=native["observer"], source_white=source, destination_white=destination,
                    lab_white=lab_white, lab_white_reason=lab_white_reason,
                    lab=None, lab_reason=lab_white_reason,
                    algorithm="bradford-full-linear", algorithm_version="1",
                    prediction="Corresponding colors; no spectral relighting or native replacement",
                )
                if lab_white is not None:
                    try:
                        result["lab"] = xyz_to_lab(values, lab_white)
                    except (ValueError, TypeError, OverflowError) as error:
                        result["lab_reason"] = str(error)
            elif method == "spectral_xyz":
                if observer_error is not None:
                    raise ValueError(observer_error)
                spectrum = _spectrum(observation["backend_reported"]["spectrum"])
                if spectrum is not None and spectrum.quantity == "reflectance" and not \
                        context["nonfluorescent_reflectance"]:
                    raise ValueError("Declare the nonfluorescent-reflectance model for integration")
                computed = spectral_xyz(
                    spectrum, observer=context["observer"], observer_csv=observer_csv,
                    wavelength_convention=context["wavelength_convention"], illuminant=illuminant,
                    illuminant_id=context["illuminant_id"],
                )
                result = dict(asdict(computed), origin="derived")
            elif method == "characterized_reflectance":
                _, result = _reference_factor(row, reference, characterization, context)
            else:
                factor_result = None
                if basis["kind"] == "characterized-reference":
                    factor, factor_result = _reference_factor(
                        row, reference, characterization, basis["context"],
                    )
                    spectrum = factor.spectrum
                else:
                    spectrum = _spectrum(observation["backend_reported"]["spectrum"])
                    if spectrum is None or spectrum.quantity != "reflectance":
                        raise ValueError(
                            "Native relighting requires retained reflectance, not radiance",
                        )
                result = _relighted_result(
                    spectrum, factor_result, row, context, observer_csv, observer_error,
                )
            result["source_observation_sha256"] = content_digest(observation)
            result["input_quantity_basis"] = dict(observation["configuration"]).get(
                "units_source", "Retained backend-reported quantity declaration",
            )
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            reason = str(error)
        output[key] = dict(result=result, reason=reason)
    return dict(
        method=method, request=request, context_sha256=content_digest(request), by_source=output,
        implementation_sha256=content_digest(dict(
            consumer=Path(__file__).with_suffix(".py").read_text(encoding="utf-8"),
            science=Path(__file__).with_name("science.py").read_text(encoding="utf-8"),
            illumination_owner=Path(__file__).with_name("illumination.py").read_text(encoding="utf-8"),
            white_owner=Path(__file__).with_name("presentation.py").read_text(encoding="utf-8"),
            dataset_owner=Path(__file__).with_name("reading_dataset.py").read_text(encoding="utf-8"),
        )),
        limitations="Explicit numerical assumptions do not establish physical validity or accuracy",
    )
```

## Signed pair comparisons in analysis and exports

`analysis.color_comparison_by_source[reading_key]` contains the unchanged plain
`processing.ColorComparison` result from
[`processing.compare_color`](illumination.md#signed-comparison-under-one-explicit-white).
The selected comparison reference is A and each selected reading is B. The
packet retains signed `delta_xyz`, `delta_xy`, `delta_uv`, `delta_lab`,
`delta_chroma` and `delta_hue`, plus `lab_reference`, `lab_sample` and
`delta_e2000`. Every unavailable metric keeps its own reason. Its
`xyz_unit`, `xyz_scale`, `observer`, `illuminant`, `algorithm` and
`algorithm_version` describe the comparison without changing native records.

The dataset composes the existing retained-XYZ and nominal-white owners. No
white or luminance scale is chosen from the display preview setting. Retained
whites must match pair unit, scale, observer and illuminant. An explicitly
selected nominal white is a numerical assumption and matches unit, scale and
observer; the two native readings must still have the same illuminant. A valid
per-reading Lab projection under that assumption does not authorize a pair
comparison across different native illuminants. The existing
`delta_e_by_source` map now uses this shared pair result, including its context
refusal, rather than comparing separately projected Lab values.

Missing white withholds Lab-derived metrics while preserving compatible XYZ and
coordinate differences. Undefined coordinates or neutral hue retain their own
nulls and reasons without hiding independent values. Scalar and timing records
retain their native quantities and have no invented color metrics. Local and
source-qualified inherited reference identities use the same dataset owner;
inheritance never replaces the original operation or observation IDs.

The analysis `summary` retains its existing fields and adds
`delta_xyz_range`, `delta_xy_range`, `delta_uv_range` and `delta_lab_range`.
An available vector range is an ordered list of component `{min, max, count,
unit}` packets, with lengths 3, 2, 2 and 3 respectively. A metric with no
available values has a null range. XYZ ranges retain native units; coordinate
and Lab ranges use unit one. `delta_chroma_range` and `delta_hue_range` are scalar
range packets. Each of these six metrics also has a matching `_unavailable`
count. All ranges exclude the selected reference itself, preserve signed values
and use only independently available results.

Each analysis-export record retains the complete packet in
`derived.color_comparison`. JSON and MAT also retain the same summary; CSV
retains it in `export_context_json` and provides `delta_XYZ_json`,
`delta_xy_json`, `delta_uv_json`, `delta_Lab_json`, `delta_chroma` and `delta_hue`
convenience columns. Unavailable scalar cells are empty; genuine zero values
remain numeric. The full packet, its reasons and its shared implementation
identity remain in `derived_json` and `record_json`. These calculations do not
establish matched physical geometry, material conditions, uncertainty or
measurement accuracy.


## Characterized-reference and identified-target derivations

The same fenced `derive` query accepts `characterized_reflectance` and
`relight_reflectance`. Both compose the existing illumination/science owners;
they do not acquire measurements, enrich native records or create another
scientific implementation. Reading selection remains bound to the dataset's
scientific revision, and each retained role includes its complete qualified
`reading_ref`, original observation and observation SHA-256. Inherited references
retain their segment and original manifest digest, including when native IDs
also occur in the local segment.

For `characterized_reflectance`, context has exactly `reference`,
`characterization`, `characterization_id`, `assume_ideal_white`,
`setup_declaration`, `matched_setup` and `nonfluorescent_reflectance`.
The three flags must be explicit booleans. The reference is a full retained
reading reference, including source qualifiers where present. Characterization
is a typed spectrum or null. A characterized curve requires a nonempty caller
source identity. An explicitly chosen ideal-white assumption instead requires
null characterization and identity; neither absent data nor a missing file
selects this assumption. Setup text and the true matched/nonfluorescent claims
remain operator declarations, not authenticated physical facts.

The [existing factor owner](illumination.md#characterized-measured-reference-spectral-factor)
requires saved sample/reference color observations with compatible same-grid
radiance. It preserves signed factors and values above one, original spectra,
characterization data and its digest, and the numerical weak-reference policy.
Only the declared characterization may interpolate; native spectra do not.
The returned `result` remains separate from the observation. Missing or
incompatible quantities, grids or declarations produce a per-reading reason.

For `relight_reflectance`, context has exactly `reflectance`, `illuminant`,
`illuminant_id`, `observer`, `wavelength_convention`,
`nonfluorescent_reflectance` and nullable `color_space`. `reflectance` is either
`{"kind":"native"}` for an actual retained reflectance spectrum, or
`{"kind":"characterized-reference","context":...}` containing the complete
factor context above. Radiance cannot enter the native-reflectance route.
Named D50/D65/A selects the existing authenticated CIE resource and requires
null `illuminant_id`. A supplied typed relative-power SPD instead requires its
explicit caller identity and the exact input grid. Observer, wavelength medium,
nonfluorescent applicability and any display encoding remain explicit.

The [relighting owner](illumination.md#relighting-with-one-explicit-target-spd)
returns relative XYZ with its own grid-specific diffuser white. The composer
uses that same white for independent Lab and optional corresponding-color
projection; it never borrows native XYZ or a nominal XYZ white as an SPD.
`target_illuminant` retains the declared data/digest or the authenticated CIE
resource identity, original-byte digest, sampled spectrum and interpolation
limits. The observer digest comes from the accepted spectral owner.

Relighting returns `reflectance`, nullable `reference_factor`, `source_inputs`,
`target_illuminant`, and independently nullable `xyz`, `lab` and
`corresponding_color`, each with its own reason. A valid reference factor
survives a refused target calculation; valid XYZ survives an unavailable Lab
white or display model. Null encoding declines the display model explicitly.
Signed linear RGB and clipping flags remain separate from illustrative bounded
RGB. None of these calculations establish fluorescence absence, matched optical
setup, instrument accuracy, calibrated display color or physical qualification.
The common export snapshot retains the request and these separate values in
`derived_science`, alongside the unchanged selected native records.
For either a standalone factor or characterized-reference relighting,
`role_inputs.reflectance_reference` also retains the authenticated reference
input, even when it is outside the exported selection. This role preserves its
full qualified `reading_ref`, original observation and observation digest,
canonical operation JSON and operation SHA-256; `selected` records whether it
also appears among the selected records. Characterization and its explicit
identity remain in the derivation request and result, separate from native data.
