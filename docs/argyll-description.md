# Native driver catalog and selected-device description

[`NativeDescriptionClient`](../src/color_measurement/argyll_description.py)
provides blocking metadata operations around a separately built ArgyllCMS 3.5.0
helper. It distinguishes the compiled driver catalog, OS transport candidates
and active initialization of one selected device. It does not provide a generic
measurement or calibration route, typed measurement controls, or the asynchronous
[frontend operation interface](frontend.md).

The native recipes build and link the real helper on macOS, Linux or Windows
x64 MinGW-w64. Software checks execute a separate worker with substituted
instrument methods, including contributed CR/PR drivers over scripted serial
transport. They do not run the production worker. A successful build or simulated
description does not establish physical initialization, calibration, acquisition
or optical accuracy. Each target OS needs its own executed software evidence.

## Build and source identity

Use the [native checker's inputs](argyll-native-feasibility.md#inputs-and-invocation)
with its broader driver selection and a new output directory:

```sh
uv run --locked python tools/argyll_native_check.py --archive /path/to/Argyll_V3.5.0_src.zip --output local/native-feasibility/broad-check-01
```

This command builds the real workers without running them and executes only
synthetic/substituted checks. It retains `report.json`, `driver-manifest.json`,
patches, compiler/dependency records and the separately linked
`description-stub-worker`. The authenticated
[upstream source archive](https://www.argyllcms.com/Argyll_V3.5.0_src.zip), not an
arbitrary installed Argyll binary, defines the source enums and masks described
here. Upstream declarations are in `spectro/insttypes.h` and `spectro/inst.h`.

The broader build reconciles preprocessed constructor dispatch against archive
symbols: the stock selection has 37 constructible instrument enum types that
share 21 distinct driver constructors. These are not counts of separately tested
physical models. `instSpectrocam` remains in the source catalog with an upstream
not-implemented constructor exclusion. Optional absent/private and
non-measurement sources are listed separately in the manifest. Windows omits
UNIX-only `pollem.c` after the full driver source union is formed. The report
lists the translation units and link results actually produced on its platform;
source inspection or substituted OS branches do not establish a target build.

The [manifest implementation](../tools/native_argyll/driver_manifest.py) and
[shared transport patches](../tools/native_argyll/no_termination.py) retain
original/modified hashes and attributed diffs. They disable automatic service
termination on USB/HID paths and automatic serial identification during listing,
check the selected family before construction, and supply standalone build
compatibility declarations. The broad build also applies the established i1Pro
and i1Pro3 measurement corrections and a narrow Huey emission-type correction:
`huey.c:839-863` already computes calibrated emission XYZ, while `:1242-1250`
incorrectly labels the emission result as unknown. The patch changes only that
label. The build also composes [DTP41, DTP92/DTP94 and Specbos calibration
repairs](argyll-native-feasibility.md#shared-driver-calibration-repairs), with
executable source proofs bound to the compiled inputs. Shared acquisition
remains in [one session helper](instrument.md).

## API and access boundaries

| Entry point | Behavior |
| --- | --- |
| `NativeDescriptionClient.from_build(report_path, timeout=30)` | Read a passed broad-driver report, require pinned version/archive/scope, bind the sibling `argyll-description-worker-never-run` by SHA-256, and retain the report hash; no process starts |
| `NativeDescriptionClient.from_simulated_build(report_path, source_root=..., scenario="success", timeout=30)` | Authenticate the original description substitute against the explicit current source checkout; retain the original catalog and simulated provenance without starting a worker. `InstrumentBuild` separately authenticates its selected acquisition substitute |
| `NativeDescriptionClient.for_offline_tests(helper, scenario="success", timeout=5)` | Select the checker's dedicated `description-stub-worker` for software fixtures; constructing the client starts no process |
| `catalog_json()` | Return the static source/build manifest without launching a process; unavailable on a stub-only client |
| `list_candidates_json()` | Launch the helper to list OS paths without driver initialization or automatic protocol identification |
| `describe_json(candidate_json, expected_initialized_dtype=None, expected_serial=None, allow_serial_identification=False, accepted_initialization_reason=None, accept_unassessed_initialization=False)` | Re-enumerate and recheck one exact candidate, then actively initialize it if the route is permitted; return identity and raw capabilities/configuration |

`accepted_initialization_reason` must exactly match the native disclosure for
the selected type. `InstrumentBuild.describe(...,
accept_disclosed_initialization=True)` carries that reason to the helper.
Unassessed initialization uses its separate boolean. Firmware/PLD download is
always refused; neither acknowledgement grants permission for it. The
acquisition helper enforces the firmware prohibition too.

The keyword arguments after `candidate_json`, and factory timeout arguments,
are keyword-only. A client permits one metadata request at a time; this is a
per-client lock, not cross-process instrument ownership. Use trusted build
artifacts: report/helper hash agreement binds those files together, but does not
authenticate an arbitrary edited report. The helper hash is rechecked before
each request. Direct construction without a build binding cannot launch physical
metadata operations.

The methods block and provide no public asynchronous poll/respond/cancel API.
Call them outside a frontend event loop. `timeout` must be finite and in
`(0, 600]` seconds; it bounds individual response/exit waits, not optical exposure
or one total wall-clock deadline. Failed protocol/timeout paths clean up the
owned child, potentially terminating it; this does not establish device readiness.
There is no automatic retry. `ARGYLL_*` variables are removed from the child
environment; other variables and upstream cache behavior are not isolated.

Listing is an explicit OS-enumeration action. Description is active device
access and needs separately scoped instrument ownership/operation authorization;
the API does not obtain it. Initialization can communicate and load calibration
state even when no measurement is requested. The calls below demonstrate only
static metadata and the dedicated stub.

### Static catalog example

After the build above, this reads the report and helper bytes without running it:

```python
import json
from color_measurement.argyll_description import NativeDescriptionClient

client = NativeDescriptionClient.from_build(
    "local/native-feasibility/broad-check-01/report.json"
)
catalog = json.loads(client.catalog_json())
print(catalog["linked_instrument_type_count"])  # 37
print(catalog["linked_constructor_count"])      # 21
coverage = catalog["coverage_summary"]
print(coverage["application_measurement_instrument_type_count"])  # 35
print(coverage["application_measurement_mode_route_count"])       # 35
```

Broad manifests use `build_scope="all_public"`; their reports use the same
`build_scope` and `driver_scope`. The machine OS is recorded separately as
`build_system="Darwin"`, `"Linux"` or `"Windows"`; `platform` remains diagnostic
text. Both production and authenticated substituted bindings require the report's
`build_system` to match the current host. They resolve only the dedicated sibling
worker filename, adding `.exe` on Windows. A file for another OS is refused even
when its digest matches. The `coverage_summary` derives linked and
application-route counts from the catalog rows. These admission checks do not
establish target-platform build or runtime qualification.

Linked types with permitted initialization carry the shared route
`generic_ladder:capability_derived`. Two linked Spyder types requiring a PLD
firmware download have no eligible acquisition route. These counts describe
implementation routes, not demonstrated hardware support or every mode.

The modes are deliberately **not** enumerated. A route comes from the instrument's
own capability words at bind time, and `check_mode()` is definitive over the
advisory macro (`inst.h:305-306`), so a static source catalog naming modes would
assert what only an attached instrument can answer. The manifest says an
instrument is served and by what, never what it will offer.

`hardware_tested` is false.

Actual implementation of
[`NativeDescriptionClient.catalog_json`](../src/color_measurement/argyll_description.py)
(checked against the source method):

```python
    def catalog_json(self) -> str:
        """Return the build's static catalog. Never starts a process or lists devices."""
        if self._catalog is None:
            raise ValueError("A substituted worker has no linked upstream catalog")
        return json.dumps(self._catalog, allow_nan=False)
```

### Substituted description example

Use only the separately built stub at the retained build location:

```python
import json
from color_measurement.argyll_description import NativeDescriptionClient

client = NativeDescriptionClient.for_offline_tests(
    "local/native-feasibility/broad-check-01/description-stub-worker",
    scenario="unknown_serial",
)
listing = json.loads(client.list_candidates_json())
candidate = listing["data"]["candidates"][0]
result = json.loads(client.describe_json(json.dumps(candidate)))
print(result["provenance"], result["status"])   # simulated completed
print(result["data"]["serial"])                # None
print(result["data"]["application_measurement_routes"])  # []
```

Description also publishes `ambient_ccmx`, a source-backed native policy fact
for ambient matrix applicability. It does not turn raw capabilities into
acquisition proof: `application_measurement_routes` remains empty here.

## Candidate identity and description fields

A candidate contains exactly `path`, `dtype`, `transport`,
`initialization_assessed` and `initialization_refusal`. Keep the full object when
requesting description.

Those last two are computed from the candidate's **`dtype`**, so for an
unidentified serial path they describe `instUnknown` — that is, the port, and not
whatever model answers on it. The driver manifest carries the same two fields per
instrument **type** (`parse_init_refusals` and `parse_init_harmless`, both read
out of `description.c` so there is one source), which is how a serial unit is
judged once it says what it is. In
[`description.c`](../tools/native_argyll/description.c), the call
`init_blocker((instType)expected_initialized_dtype)` checks that expected type
before `cm_description_construct`; a refusal ends the operation before
construction.
The path, native type and transport must match exactly one current candidate;
an absent, ambiguous or changed path fails before construction. Path text is
limited to 1,024 UTF-8 bytes and serial text to 4,096 UTF-8 bytes; missing/empty
generic native serials become null. Verified PR identity still requires a nonempty
reported serial matching the initialized description. An expected serial assertion
is compared exactly. Candidate JSON input is limited to 16,384 characters. Shared
native helper frames are separately limited to 65,536 bytes, including framing;
public frontend requests and client events retain their 16,384-byte cap.

At most 512 candidates can be listed, subject also to the smaller serialized
payload budget. An oversized list fails rather than silently truncating possible
selections. A same-model pair retains separate paths. The validator accepts the
pinned driver's i1Pro2-to-i1Pro identity refinement and retains both the selected
and the initialised type, exercised with substituted responses. The current active
metadata route refuses these older i1Pro types before initialization because
their startup resets the device. Other cross-family changes are rejected.
An explicit expected initialized type is checked as well. Expected serial is
checked after initialization, so it does not prevent all active access to a
wrong unit.

Unknown serial candidates require explicit identification permission and an
expected native type. That type must belong to a real serial driver, and blocked
types are refused before probing/constructor dispatch. All currently linked
serial-driver families have an initialization refusal in this metadata-only
route; the argument is not a way to enable those paths. Their source/build
catalog entries remain present.

| Description field | Meaning |
| --- | --- |
| `candidate`, `initialized_dtype`, `serial` | Original selected candidate plus initialized identity; unknown serial stays null |
| `native_identity` | Required availability field: verified facts copied from this initialized instance, or null when no adapter can provide them |
| `capabilities.primary/secondary/tertiary` | Raw unsigned upstream masks, not decoded typed controls or proof a requested mode can measure |
| `current_configuration` | Current configuration query: `status`, native return code, index, raw modes and calibration-condition mask |
| `baseline_mode_checks` | Initial capability mask and native `check_mode` results for each advertised baseline word, before measurement mode/options are configured |
| Unavailable configuration | Nonzero native code and null index/modes/conditions; no fabricated zero masks |
| `display_types` | Native display-calibration catalogue, loaded-data identities and separate technology/refresh/base readback; no selection is applied |
| `application_measurement_routes` | Empty for this description interface, even if raw capabilities advertise measurement |

Configuration querying does not select a measurement mode, calibrate or acquire.
A calibration configuration mask describes setup, not a performed calibration.
The source/build catalog separately reports eligibility for the shared
acquisition route. This description client still performs no acquisition.

### Current binding identity

The common description helper asks the selected driver's protected identity
adapter for a snapshot after successful initialization. The adapter issues no
additional instrument commands and copies only facts from that instance. The
current CR adapter requires initialized communications, verified identification
and a synchronized engine under the driver's lock. Other drivers and the wholly
substituted description worker publish `native_identity=null`; they do not
invent driver or firmware facts.

An available packet has `driver` and `identity`, plus the explicit boolean
`native_controls` feature marker when published by the current adapter. An absent
marker does not establish settings support. For `driver="cr250"`,
`identity` contains exactly `verified`, `model`, `serial`, `firmware`,
`firmware_components` and `instrument_type`. `verified` must be true, and the
reported serial must equal the description's serial. The reported model must
resolve through the existing binding catalogue to the observed initialized
type. Firmware text and its one-to-three integer components remain reported
facts; unavailable values stay null. The CR `instrument_type` is the device's
kind, 0 (photometer), 1 (colorimeter) or 2 (spectroradiometer), or null when
unavailable. It is independent of Argyll's `initialized_dtype` enum.

For `driver="pr655"`, `identity` contains exactly `verified`, `model`, `serial`,
`firmware` and `grid`. The verified model is `PR-655` or `PR-670`, and its serial
matches the initialized description. The grid retains `count`, `start_nm`,
`end_nm`, `step_nm`, `pixels`, `first_pixel`, `last_pixel` and `bandwidth_nm`.
PR-655 requires 101 samples at 4 nm and 128 detector pixels; PR-670 requires 201
samples at 2 nm and 256 pixels. Both span 380–780 nm. Pixel bounds and nonnegative
bandwidth remain reported facts and are checked without inventing fitted optics.
Binding resolves the short model within the validated driver's namespace:
Photo Research for PR and Colorimetry Research for CR.

Both PR models disclose `initialization_enters_remote_mode`. PHOTO disables the
touch screen, so construction requires explicit acceptance of that effect.
Clean close sends Q; uncertain exchanges can prevent cleanup. The description
does not establish physical qualification or permission to operate equipment.

[`InstrumentBuild.describe`](../src/color_measurement/discovery.py) preserves
these facts and the original authenticated description-worker receipt.
`InstrumentBuild.bind` revalidates both before publishing this exact packet
under the frontend descriptor's `current_binding_identity`:

```text
{
  device_id,
  initialized_type,
  native_identity: {driver, identity} | null,
  provenance: {
    backend_version,
    description_helper_sha256,
    session_helper_sha256,
    build_report_sha256
  }
}
```

`initialized_type` preserves the observed `initialized_dtype`; it is not a
second guessed identity. `device_id` is the bound label, defaulting to the
selected path. The provenance retains both worker hashes and the nullable build
report hash. A description from another build cannot inherit this build's
session-worker receipt. The description also retains an immutable original
native-identity observation. Binding rejects altered identity content even when
the changed firmware or other facts still satisfy the schema and catalogue.
Binding and descriptor reads perform no additional enumeration, initialization
or measurement. Published nested identity data is copied so later caller edits
cannot change a published descriptor.

The shared frontend `capability_identity` includes the stable binding and native
identity fields; within this packet, it excludes only `provenance`. Rebuilding
workers therefore does not change capability identity solely because receipt
hashes change; a different bound device, initialized type or reported firmware
does. Native settings reports and pending recovery are separate runtime fields
with their own [descriptor identity rules](generic-frontend.md#descriptor).
Selected-source comparisons still retain this packet's exact build provenance.
Frontends created without a current binding packet omit the field. A genuine
binding with unavailable native facts includes the packet with
`native_identity=null`. Neither case establishes calibration, current readiness,
physical device qualification or permission to acquire. Consumers requiring
those facts must preserve the unavailable state. Historical descriptions that
omit native-identity availability are refused by the current contract; retained
historical captures remain unchanged.

`baseline_mode_checks` is required and has exactly `scope`, `mode_capabilities`
and `checks`. Scope is `initialized_before_mode_configuration`; the mask equals
this description's initial `capabilities.primary`. Each check contains exactly
`mode_word` and `native_code`, both unsigned 32-bit integers, except that the code
is null when the method is absent. Booleans are not integers here. The list
contains exactly the unique words returned by `baseline_mode_words(primary)`
in [`argyll_capability.py`](../src/color_measurement/argyll_capability.py).
Its candidates come from `MODE_ROUTES` and the supported nonadaptive emission
spot/telephoto variants. For each candidate advertised by the initial mask, it
requests XYZ alone and, when spectral output is advertised, XYZ plus spectrum.
The native candidate table is generated from that same vocabulary.
[`write_mode_check_candidates`](../tools/argyll_native_check.py) sets
`CM_MODE_CHECK_CAPACITY` to twice the number of baseline candidates, reserving
space for the XYZ-only and XYZ-plus-spectrum words. The C storage capacity
therefore follows the generated candidate set.
[`validate_baseline_mode_checks`](../src/color_measurement/mode_acceptance.py)
requires this exact set rather than a separate fixed list-size limit. High
resolution and reflective conditions are not part of this baseline query.

The Python validator refuses malformed, duplicate, missing or invented candidate
checks. Python interprets zero as confirmed, the `inst_unsupported` error class
as rejected, and every other nonzero code as unknown. Raw codes are retained.
These are initialized-state facts, not calibration/readiness or proof that a
future option combination works. Acquisition still checks its exact request.

## Initialization refusals

The original [description boundary](../tools/native_argyll/description.c) discloses
the following startup effects and refuses them until the matching effect is
accepted, except firmware loading, which remains prohibited. The cited pinned
upstream files establish those effects. They remain linked and listed; this is not a declaration that Argyll
cannot operate them.

| Native types | Startup behavior excluded here | Pinned source |
| --- | --- | --- |
| Spyder1/2 | PLD download | `spectro/spyd2.c` |
| Spyder3/4/5 | Startup sensor reading | `spectro/spyd2.c:3017–3025` |
| SpyderX/X2/2024 | Ambient measurement | `spectro/spydX.c`, `spydX2.c` |
| SMCube | Background measurement monitor | `spectro/smcube.c` |
| Klein K10 family | Startup flicker measurement | `spectro/kleink10.c:525–528` |
| DTP20/22/51 | Instrument/configuration reset | `spectro/dtp20.c:240–243,371–373`, `dtp22.c`, `dtp51.c` |
| i1Monitor/i1Pro/i1Pro2 | Device reset during initialization, including high-power setup | `spectro/i1pro_imp.c:544,1418,4881–4914` |
| DTP41 | Calibration behavior/expiry changes | `spectro/dtp41.c` |
| Specbos1201/2501/Specbos/Spectraval | Automatic calibration settings; some models also restore defaults | `spectro/specbos.c:589–599` |
| DTP92/94 | Factory-calibration selection | `spectro/dtp92.c` |
| Spectrolino | White-reference restoration | `spectro/ss.c` |
| SpectroScan/T | Scanning-table initialization or movement, including cleanup | `spectro/ss.c` |

These cover 26 linked enum types; the other 11 are eligible under the current
code's selected-initialization rules, not physically validated. A callback also
refuses non-communication operator requests; it cannot intercept arbitrary
driver-internal reads/resets, hence the explicit startup exclusions.

## Outcomes and software checks

Returned JSON contains schema/versioned identity, a backend version, helper
SHA-256, nullable build-report SHA-256, status, `data`, `error` and ordered
`events`. Native failures return
`status="failed"`, null data and the error stage/native code/refusal reason.
Invalid arguments, changed helper bytes and malformed protocol can instead raise
exceptions. `last_events` preserves validated events, including a description
seen before later protocol failure; their presence alone does not establish a
completed operation.

Events are `started`, one `candidates`/`description`/`error`, then `terminal`.
The terminal distinguishes active-access attempted, initialization attempted
(`initialization_performed`) and completed, callback refusal and cleanup.
`destructor_returned` reports a software call outcome. `software_stopped=true`
and `instrument_state="unknown"` do not claim device readiness. The client also
requires the expected process exit and no late output before returning success.

[`tests/test_argyll_description.py`](../tests/test_argyll_description.py) checks
Python validation, factory/report binding, identity refinement, missing values
and event/terminal rules without native device access.
[`description_check.py`](../tools/native_argyll/description_check.py) exercises
the shared C core against wholly substituted methods, including pre-initialization
refusals and cleanup, and separately checks an extracted constructor guard with
substituted OS calls. These checks establish software behavior; they do not run
the real helper or establish the OS ABI, physical startup effects or native
portability beyond the build that was actually performed.

Display catalogue entries arrive as bounded `display_type` frames before the
`description` frame. `NativeDescriptionClient` validates their order, identities,
count and summary, then joins them into `data.display_types.entries`. The
required boolean `native_default` preserves each row's native default flag;
it does not identify a current selection and can mark zero or multiple rows.
An unavailable entry keeps its flag too. The catalogue query recreates the
installed-file list through the shared vtable;
it does not select a display type, trigger, option or calibration. Failed list
queries preserve their native code and offer no choices. These operations still
require the same authorization for active initialization as the description
itself. The [instrument guide](instrument.md) defines identity scope, revalidation
and partial readback semantics.
