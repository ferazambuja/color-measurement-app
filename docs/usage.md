# Using the simulator and conversion API

## Install from the checkout

The package declares Python 3.11–3.14 and has no third-party runtime dependencies.
Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
uv sync --locked
uv run color-measurement --help
uv run color-measurement devices
```

These are local project commands, not instructions to install a similarly named
package from a registry. `devices` lists the simulator's capabilities and a
static instrument-name table without discovering or opening devices. The
simulator supports `relative_xyz`, supplies XYZ without a spectrum, and has no
calibration operation. The `instruments` table identifies names requiring a
contribution; it does not establish that the selected native build can construct
or connect to those instruments.

## Acquire a synthetic observation

```sh
uv run color-measurement acquire --device simulator --sample-id example --output reading.json
```

Use a new path in an existing directory. The simulated XYZ is `(20,25,10)` on a
relative scale described as reference-white Y=100. Observer and illuminant are
unspecified. Each call creates fresh observation/operation IDs and a UTC timestamp;
the three synthetic numbers are deterministic. These are software fixtures, not
physical observations or an emulation of a particular meter.

Complete JSON goes to stdout; progress and errors go to stderr. `--format csv`
selects the saved file format; stdout is still JSON. No output file is required.
The canonical JSON/CSV output is never overwritten by the application's save
function. Eligible TI3 companion files have their own update rules below.

xyY is computed automatically. Lab is included only with an explicit white:

```sh
uv run color-measurement acquire --device simulator --white 80 100 120 --output lab.json
```

This example white is synthetic, not D50 or D65. The caller must supply white
and sample on compatible scales and under compatible reference conditions.
The software does not infer those conditions, adapt color or normalize a sample
to its own Y. See the [science conventions](science.md).

On an instrument, `--white` is applied to the native XYZ the reading reports.
A reading with no native XYZ (a spectrum-only result) keeps Lab absent, records
why in the observation's `warnings`, and still exits 0: the exit status is the
acquisition's, not the derivation's. An invalid white is refused before the
instrument is bound on either route.

## Cancel and recover from a failed save

To exercise cancellation, run the following and press Ctrl-C during the delay:

```sh
uv run color-measurement acquire --device simulator --delay 10
```

Cancellation before completion returns no observation, exits with status 130,
and reports that only simulation stopped. There is no claim about physical
instrument readiness. A delay must be finite and between 0 and 3600 seconds.

The command prints the completed record before attempting `--output`. To keep a
recoverable copy, redirect stdout to a **different, new** filename:

```sh
uv run color-measurement acquire --device simulator --output reading.json > recovery.json
```

If `reading.json` already exists or cannot be saved, the completed record remains
in `recovery.json`. Shell redirection can replace files independently of this
application, so choose that filename carefully. Retry with `export`:

```sh
uv run color-measurement export recovery.json --output recovered.csv --format csv
uv run color-measurement export recovered.csv --input-format csv --output recovered.json
```

Export reloads the existing record; it does not reacquire or recalculate it.
Observation IDs, original values and computed reference context are preserved.

### Automatic TI3 files and save messages

Saving with `acquire --output` or `export --output` first writes the requested
JSON/CSV record, then attempts eligible `measurements-*.ti3` companions in the
same directory. You do not need a separate export command for those companions.
Keep the canonical record: a TI3 file contains only supported color quantities,
not all retained metadata. See [TI3 quantities and limits](data-format.md#automatic-ti3-measurement-companions).

The command reports the canonical save and TI3 outcome on **stderr**:

```text
Saved complete simulated record to reading.json
TI3 companions: unavailable (retryable=false, relocatable=false)
```

The canonical-save message identifies the record's provenance. Per-file status and
per-record/component reasons follow when present. `current` means the reported
files match the saved data; `stale` means an update failed or earlier output no
longer matches; `unavailable` means no eligible projection was produced. Inspect
the reasons even when some files are current.

Actionable outcomes report separate `retryable` and `relocatable` booleans.
A write or lock failure can be retryable; malformed or unsupported retained data
and an ownership refusal are not. Retryability describes the companion update,
not permission to acquire another reading. Per-file lines omit an empty reason
instead of ending with a blank space.
`relocatable=true` means a new destination can resolve an active output-folder
or publication failure using the same saved source. An ownership refusal can
therefore be nonretryable but relocatable. Unsupported or malformed data and
inactive historical output set both flags false.

An individual file may be `retired` when its group is no longer in the saved
data. It remains historical output and contributes no current readings or
retry request. A changed or foreign output file is an ownership refusal, not
a transient write problem; use a new destination instead of repeatedly
retrying or replacing that file.

The simulator above has no spectrum and does not declare the perfect-diffuser
Y=100, D50/2-degree conditions required for reflective TI3 XYZ. Its canonical
JSON/CSV save can therefore succeed while TI3 is unavailable. This does not
require another reading. Unsupported or malformed retained quantities cannot
be repaired by retrying their export; correct the cause of a retryable file-write
failure before trying saved-data recovery.

The CSV-to-JSON recovery example writes two distinct canonical files. Eligible
companions belong to each saved source path separately, even when observation
IDs match. A companion retry for the same source keeps its filename stable.
Do not rename one source's TI3 to replace another source's output.
The TI3 source header contains an opaque digest rather than the canonical save
path. Other retained metadata can still include names, device serials, notes
and configuration paths; see the [metadata and privacy limits](data-format.md#ti3-metadata-and-privacy)
before sharing a companion.

Publication can leave a hidden `.ti3-export.lock` file in the companion folder.
Its existence does not mean an export is running: the operating system holds
the actual lock only during publication. Leave the file in place; deleting it
is not a recovery step. Optional TI3 failure leaves the successful canonical
save intact and does not itself change a successful command's exit status.

| Exit | Meaning |
| --- | --- |
| `0` | Successful operation |
| `1` | Input, record-load, validation or instrument failure |
| `2` | Save failure; also used by argument parsing for malformed command syntax |
| `130` | Cancellation/interruption |

If an instrument reads successfully but its table cleanup fails, `acquire`
prints and saves that completed observation, including the native failure in raw
events and a warning, and exits with status `1`. The reading survives; the
operation is still reported as failed. A failed read itself produces no invented
observation. Save failure takes status `2` and retains the record on stdout.

## Python API

The runnable [example](../examples/simulated_reading.py) uses the same core as
the CLI. A minimal application can consume events directly:

```python
from color_measurement.operations import SimulatedDevice
from color_measurement.processing import compute

device = SimulatedDevice()
events = device.measure(sample_id="example")
try:
    for event in events:
        if event.observation is not None:
            result = compute(event.observation, reference_white=(80.0, 100.0, 120.0))
            print(result.project_computed.lab.values)
finally:
    events.close()
```

`measure` is a generator: advancing it starts the operation. It yields `ready`,
then normally `measuring` followed by `result` or `cancelled`. If cancellation
is already requested before acquisition starts, it yields `ready` then
`cancelled`, omitting `measuring`. Pass a `threading.Event` as `cancel`
and call its `set()` method to request cancellation. One active generator owns
each simulator instance; close it if abandoning iteration. `compute` returns a
new immutable observation with derived values, preserving native data and text.
Pure conversions are also available in [`color_measurement.science`](science.md).

## Supported scope

The CLI simulator described here does not discover or operate hardware.
The separate [generic instrument API](instrument.md) drives any ArgyllCMS
instrument by name over one native helper: spot, strip, X-Y table and stored
chart, with the modes, options and workflows each instrument's own capability
words admit, rather than a per-device list. Its real workers are built and
linked only; physical operation can access the instrument and calibration cache,
and every check here uses substituted methods instead. Terminal operations
support [JSON/MAT and bounded reflective SPECT export](data-format.md#operation-files-json-and-mat),
and the [shared frontend operations](frontend.md) support instrument clients and
[hardware-free equipment demos](equipment-demo.md). This
package also supplies the [offline desktop application](desktop.md), which uses
the same Python acquisition and saving owners. It does not supply a Swift client,
general CGATS/XLSX exporter, or project spectrum-to-XYZ integration.

The software CI workflow uses Linux with Python 3.11 and 3.14 for pushes to `main` and
pull requests. Scheduled and manually dispatched runs use Linux and Windows
with Python 3.11–3.14. macOS checks run locally. Configuration alone is not a
completed CI run or a physical device support claim.
For developer checks and source excerpt maintenance, see [Contributing](../CONTRIBUTING.md).
