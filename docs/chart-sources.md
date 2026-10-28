# Portable guided-chart source authentication

`color_measurement.chart_sources` authenticates an exact-byte closure of retained
chart manifests, operation results, raw terminal recovery evidence and declared
native diagnostic originals. It is a
pure helper: callers supply bytes and canonical application validators. It does
not read files, acquire a lock, publish, resume an operation, run an instrument or
perform scientific calculations. Its focused software proof does not establish
connected application, GUI or hardware acceptance.

Each chart source contains exactly `source_id`, `segment_id` and `sha256`.
`source_id` is a bounded provenance label, not a path to resolve. `segment_id`
identifies the original batch segment. `sha256` resolves its original manifest
bytes from the supplied mapping. Each original manifest's existing operation and
recovery and diagnostic references resolve their exact bytes through the same mapping. Source
records do not embed another manifest or base64 copy of the history.

The application publishes returned blobs through its existing publication owner
as `source-<sha256>.json`. The helper treats those filenames as a caller concern.
It never consults an original destination or scans a directory, so a portable
source remains self-contained when the original directory is unavailable.
Historical `destinations` and `destination_issues` retain their original spelling,
including absolute POSIX paths such as `/measurements/batch`, Windows drive paths
such as `C:\Measurements\batch`, and UNC paths such as `\\server\share\batch`.
Another OS can authenticate that history without resolving those paths or
converting the original manifest bytes. Relative `batch`, drive-relative
`C:batch` and root-only `\batch` entries are refused. The live application selects
and authenticates current writable folders separately; history does not grant
write access or choose a destination.

## APIs and callback ownership

```python
authenticate(manifest, blobs_by_sha, *, validate_manifest,
             check_result, check_recovery)

collect_source(review, manifest_bytes, operation_bytes_by_path,
               inherited_blobs_by_sha, *, validate_manifest,
               check_result, check_recovery)
```

| Callback or input | Contract |
| --- | --- |
| `validate_manifest(bytes)` | Return the current application-validated manifest, including canonical chart invariants. Unsupported schemas and malformed fields raise `ValueError`. |
| `check_result(result, entry, descriptor)` | Validate the typed `OperationResult` against its original prepared operation identity, request, device, provenance and descriptor. |
| `check_recovery(bytes, entry)` | Return application-validated raw terminal evidence belonging to the original intent. Recovery stays distinct from an accepted observation. |
| `blobs_by_sha`, `inherited_blobs_by_sha` | `Mapping[str, bytes]`, including lazy mappings. The helper retrieves only declared hashes with `__getitem__`; it does not enumerate mapping keys or values. |
| `operation_bytes_by_path` | Mapping from each declared ordinary operation, recovery or native diagnostic path to its exact bytes, prepared by the existing read-only owner. Paths are keys, not opened here. |
| `review` | Contains `folder`, `manifest` and `manifest_sha256`. Collection verifies manifest equality and the exact-byte digest; a caller-supplied acceptance flag cannot grant authority. |

Both APIs return `segments`, `operations`, `recoveries` and `blobs`:

- `segments` maps each authenticated original segment UUID to its validated
  manifest. `authenticate` excludes the current segment; `collect_source`
  includes the original being collected.
- `operations` maps `(segment_id, operation_id)` to a typed canonical result.
  Accepted failed and cancelled results remain present with their original status.
- `recoveries` maps the same identity pair to validated raw terminal evidence.
  It never creates a reading from invalid result text.
- `blobs` contains only reachable exact bytes, once per SHA-256. Unrelated supplied
  blobs are neither inspected nor returned.

`collect_source` additionally returns `source`, the three-field metadata record
for the collected original. It authenticates the supplied review and ordinary
bytes, adds that original to the source closure, and preserves its inherited
originals. Publication and cleanup remain with the application.

## Admission and retained history

Every imported source must retain completed software-stop evidence. Reserved or
started work, missing terminal recovery, and a false or unknown software-stop
fact refuse continuation. Software stop is distinct from instrument readiness:
an original stopped failure may retain `instrument_state="unknown"`. Its failed
or cancelled status, partial readings and raw recovery text remain unchanged.

The helper checks exact hashes, typed results, canonical annotation membership,
Repeat source identity and each source-local chart attempt's frozen intent and
native reading references. The chart's `result_status` retains the native outcome;
its separate `status` may classify that outcome as unresolved. The chart owner
validates its terminal-outcome digest and scientific/context rules.

`result_reason` must equal the first 8,192 characters of the canonical operation's
`error` (or empty text when no error exists). A raw recovery attempt instead
retains `result_status="unresolved"` and the first 8,192 characters of the validated
recovery `reason`, with no admitted reading. This is an explicit bounded preview;
the exact full error or raw failure remains in its original authenticated blob.
Changing the preview and recomputing the chart digest cannot replace that source.

An inherited attempt must equal the original attempt in its original pass,
including intended and accepted contexts, original result facts, placement
assessment, request, digest and reading identities. Removing an original attempt
from inherited history is refused. Current selected slots, pass context and
other editable chart state remain under the chart owner's rules.

Source manifests must belong to the same chart run and definition. Cycles,
duplicate source metadata, one segment naming different manifest revisions,
missing or changed blobs, and forged source/segment associations are refused.
The portable closure contains at most 64 original source segments. Each manifest,
operation or recovery blob must be nonempty and no larger than 32 MiB.
Declared diagnostic originals may be empty. Their exact SHA-256 and byte count
must match the schema-2 retained artifact and its validated head/tail source
ranges. The artifact concatenates exact ordered stream segments with no inserted
gap bytes. A nonnull initial file digest must match precisely the first
`written_bytes` of that artifact. Capturing/prepared
or closed-but-unsaved diagnostic originals refuse source qualification.
`not_started` alone permits truthful zero-count absence without a digest.
An authenticated incomplete capture remains incomplete, including original
error, draining, initial file and worker-execution facts. Repairing its canonical
file does not rewrite those observations or establish a full native exchange.

Exact hashes bind retained bytes; they are not signatures or physical-measurement
proof. The application must rederive accepted native context and scientific
placement facts from the authenticated results at their existing owners. This
helper neither supplies missing quantities nor infers those facts from frontend
claims.
