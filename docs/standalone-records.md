# Standalone retained records

The native desktop API opens current application record files without creating
a batch, acquiring equipment or assigning new native IDs. The same
[`DatasetBinding`](../src/color_measurement/dataset_binding.py) projects these
validated observations and delegates analysis to the existing scientific owners.
Imported data remain read-only. Original status, errors, provenance, quantities,
units, calibration, configuration and raw output remain in the source record.
Authenticated persistence does not mean that a measurement succeeded or that
physical equipment was validated.

## Explicit formats and source authentication

The host calls:

```text
open_record(path, *, format, expected_binding_id, expected_generation)
```

The path comes from a native chooser. The renderer cannot supply it.
`format` must name exactly one current format:

| Format | Original record and authoritative reader |
| --- | --- |
| `operation_json` | `OperationResult`, `operation_records.result_from_json` |
| `operation_mat` | Operation MAT schema 1, `operation_exports._decode_mat` |
| `observation_json` | Color `Observation`, `exports.from_json` |
| `observation_mat` | JSON-bearing MAT document kind `observation`, `exports.from_mat` |
| `observation_csv` | Current complete one-observation CSV, `exports.from_csv` |

These formats do not import arbitrary vendor CSV, analysis CSV, MATLAB session
files or v7.3/HDF5 data. The CSV reader verifies its convenience columns against
the complete canonical record. MAT readers verify their declared type and
original canonical data; the operation reader also verifies its inspection view.
MAT requires the optional SciPy dependency. `formats.standalone_imports` reports
actual format availability without opening a source or instrument.

[`StandaloneRecord`](../src/color_measurement/source_records.py) captures at most
32 MiB through the existing nonblocking ordinary-file reader. Symlinks, hardlinks
and directories refuse. Source identity retains the real path, format, original
byte hash and file inode. Opening rechecks the bytes after parsing. Source loss,
changed bytes and an equal-byte inode replacement refuse further consumption
until explicit selection again. Failed opening preserves the previous binding.
Opening a standalone record retains any actual live batch and its original files.
The existing `activate_live` method can return to that owner even after the
imported source disappears. While imported data are selected, renderer editing
and finish commands cannot modify the retained live batch.

## Native IDs and captured originals

Dataset rows retain original operation/observation IDs. A color observation's
recorded operation ID does not manufacture an enclosing `OperationResult`.
Partial, failed and zero-row terminal operations keep their actual status, error,
software-stop and instrument-state facts. The source metadata exposes those
facts independently of authenticated reading availability.

The shared projection receives explicit source identity: real batch/context UUIDs
for batches, or `kind`, declared `format` and `source_sha256` for standalone files.
Standalone digests never occupy batch/context ID fields. Scientific revision
combines that identity with the original observation IDs and content hashes.
Comparison choices are ephemeral and retain their separate version fence.

`capture()` returns a frozen standalone header, real file identity and original
filename-to-byte mapping. The header retains the validated original record,
minimal read-only annotations and `original_file` with exact base64 source bytes.
Its `native_input` contains the original kind/format/source hash, canonical JSON
and a separate canonical-JSON hash. JSON retains exact original text; operation
MAT retains exact embedded JSON through its one decode owner. Observation
MAT/CSV retain validated canonical JSON alongside their exact original bytes.
`assert_capture(header, identity, payloads)` reauthenticates that capture through
the binding's actual file owner. Batch captures continue through existing
directory/original-record authentication with missing and uncopyable witnesses.
Capture headers declare exact current `kind="batch"` or `kind="standalone"`;
an omitted discriminator cannot borrow another source's authentication path.
Both paths compare the typed canonical header, real file or directory identity
and exact payload map with a new authenticated capture from the selected binding.
Batch `ti3`, `export_state` and `export_reason` are separate unverified optional
receipt information; their progress does not change canonical qualification.
A valid capture from another source cannot replace the bound original.

## Native record and component exports

The host calls native methods with the selected binding and scientific revision:

```text
export_record(path, *, binding_id, generation, dataset_revision, format,
              reading_ref=None, canonical_path=None)
retry_record_export(path, *, recovery_id, canonical_path=None)
```

With no `reading_ref`, a standalone source exports its actual original record.
An explicit reference can export one saved color observation from any selected
dataset. Scalar/timing data retain their original terminal operation; a color
observation is not invented for them. JSON/MAT use existing canonical serializers;
CSV stores one complete color observation. Writers refuse existing destinations.
They retain prepared payloads and original typed data after publication failure.
Explicit retry uses those retained originals, even after source loss, and reuses
the existing format/publication owners. Canonical JSON/CSV/MAT retries publish
the exact prepared bytes, including the original MAT header, without serializing
again. Generic analysis retry refuses these
native export tokens. Recovery metadata supplies the appropriate `retry_method`
and original `native_format`, separately from the failed payload's `format`.
A failed SPECT canonical JSON companion therefore still selects a SPECT pair
retry. Analysis recovery has a null `native_format`.

`record_formats` takes the same binding/revision fence and explicit
`reading_ref` (null selects the original standalone record). It exposes output
eligibility and reasons through the existing SPECT and TI3 policies. SPECT is
available only for qualified native reflective M1/M2/M3 data with its complete,
uniform integer-nanometre grid and declared percent normalization. It requires a
distinct same-directory canonical JSON companion. A standalone observation keeps
that exact observation as companion, without fabricated operation status.
Symlink companions refuse. No spectrum is resampled or invented.

Canonical publication reports automatic TI3 outcomes separately, including each
unavailable native component and its reason. TI3 eligibility does not create
profiling device channels. A SPECT failure may leave its canonical JSON and TI3
companions; the pair is not an atomic transaction. An explicit native retry
selects both destinations and reuses the same retained native record.

Standalone sources do not have guided-chart state, a writable acquisition owner,
batch archive or application command receipt. Analysis queries use the existing
owners; source-kind-aware analysis snapshot publication is qualified separately
from this import/authentication/native-export component.

## Software scope

[`tests/test_source_records.py`](../tests/test_source_records.py) exercises actual
current JSON/MAT/CSV readers, exact byte/canonical-JSON provenance, source drift,
read-only live-owner preservation, native export/retry and SPECT/TI3 reasons.
It uses declared synthetic records and an actual software Demo operation. No
physical equipment, OS serial connection or native desktop UI is exercised.
