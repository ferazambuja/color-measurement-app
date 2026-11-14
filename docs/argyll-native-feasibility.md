# Native Argyll build and software checks

The native checker prepares pinned ArgyllCMS source, imports reviewed driver
contributions, applies application policies, and builds the shared acquisition
and description workers. It links the production workers without executing
them. Executed checks use inert extracts or substituted transports and synthetic
observations. These checks establish software behavior, not instrument
communication, calibration validity or optical accuracy.

## Inputs and invocation

Use a supported Python environment, a C compiler and platform development
headers, an `ar` archiver, and the separately obtained
[ArgyllCMS 3.5.0 source ZIP](https://www.argyllcms.com/Argyll_V3.5.0_src.zip).
The checker does not download source. Its required SHA-256 is:

```text
f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f
```

From the checkout, create an owned temporary parent and a new output directory:

```sh
native_run="$(mktemp -d "${TMPDIR:-/tmp}/color-measurement-native.XXXXXX")"
uv run --locked python tools/argyll_native_check.py \
    --archive /path/to/Argyll_V3.5.0_src.zip --output "$native_run/check" \
    --contribution tools/native_argyll/contributions/cr_pr
```

The output directory must not exist. The broad build is the only build recipe.
Omitting `--contribution` selects the committed `repairs` projection; the app's
combined CR/PR build selects `cr_pr` explicitly. A complete application gate uses
that combined projection and both substituted workers; see
[testing](testing.md#one-command).

`--substituted-binding` runs a smaller binding rehearsal through the same source
preparation and substituted acquisition/description checks. Its report declares
`check_scope="substituted_binding"`; ordinary production report selection refuses
that receipt. It does not establish full native or scientific coverage.

Inspect the overall status, component results and failure logs in `report.json`.
A successful compile alone is not a complete check. The standalone tool retains
its output on success or failure. Keep useful logs, source revision, environment,
input hashes, compact results and unique inputs before removing owned temporary
builds after all consumers finish. The full gate owns and removes its temporary
native workspace after its dependent pytest stage.

## Platform and device access

| Platform | Build and transport boundary |
| --- | --- |
| macOS | Apple compiler/SDK; Argyll USB/HID and serial sources. Serial candidates are identified from IORegistry metadata. |
| Linux | GNU-compatible ELF linking and pthreads; USB and serial sources. Serial listing uses metadata without opening candidates. Upstream's disabled HID enumerator supplies no separate Linux HID route. |
| Windows x64 | Native MinGW-w64/Windows-GNU toolchain with `clang`, `ar`, `nm` and `objdump`; libusb0, Windows HID and registry-based serial listing. |

The Windows checker accepts the `-w64-mingw32` or `-w64-windows-gnu` compiler
target matching the interpreter's architecture, `x86_64` or `aarch64`. CI uses
MSYS2 CLANG64 with compiler-rt. PE inspection checks the matching executable format
and records DLL imports without loading the production workers;
it does not establish that every target runtime dependency is available.
Production and substituted workers share the Windows wide-entrypoint wrapper in
[`platform_io.h`](../tools/native_argyll/platform_io.h). Strict UTF-16/UTF-8
conversion preserves argument boundaries and rejects malformed input before
backend dispatch. POSIX builds use their ordinary entrypoint.

The builder removes automatic serial identification, Linux's candidate-open
validity probe and the Windows USB enumerator's kernel-debug-level write.
Explicit identification opens only the selected instrument. Passive listing can
still read USB descriptors or use OS query handles. Software tests substitute
those interactions. The checker installs no device driver, changes no device
permissions and terminates no vendor service. Actual access depends on existing
[Linux permissions](https://www.argyllcms.com/doc/Installing_Linux.html) or
[Windows driver binding](https://www.argyllcms.com/doc/Installing_MSWindows.html).

The standalone macOS package requires Apple silicon and macOS 27 or later;
compatibility with other Mac configurations remains unqualified. The Windows x64
and Linux x64 packages use the same desktop host and are experimental. Windows
ARM64 runs the x64 package through emulation. See the [platform guide](platforms.md)
for reporting scope. Cross-compilation, another platform's pass and substituted I/O do not
establish execution or physical operation on the target.

## Source ownership and composition

Argyll contributions own portable C repairs, the CR/PR protocol engines and
drivers, and ordinary Argyll registration. This app owns process supervision,
passive discovery, serialization and application admission policies. All
Argyll-backed instruments, including each i1Pro generation, use the shared
[`session.c`](../tools/native_argyll/session.c) and description interface.

The generated series under
[`contributions/`](../tools/native_argyll/contributions/) authenticate the source
repository commit/tree, official archive, declared paths, preimages, postimages,
authorship and license notices. The importer refuses unlisted paths, symlinks,
ambiguous diffs and any digest mismatch. Portable repairs are imported once.
Application policies are applied afterwards and recorded separately.

The combined catalog has 43 names and 42 linked types. It includes CR-250,
CR-100, CR-300, PR-655 and PR-670. The imported
[CR contract](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250.txt)
and [PR contract](../tools/native_argyll/contributions/cr_pr/files/spectro/pr655.txt)
own protocol/scientific behavior; the
[native control guide](argyll-native-controls.md) describes application overlays.
The contribution unit runner compiles the relocated `inst.c` and `inst.h` with
its driver, engine, headers and scripted actor before application overlays.
It records actual check counts and sanitizer availability.

### Regenerating a contribution

Maintainers edit and review the driver source first; its history is not
published with this release. From this app checkout, import a full reviewed
commit with the committed recipe and official archive:

```sh
PYTHONPATH=src:tools uv run --locked python -m native_argyll.import_contribution \
    --source-repo /path/to/argyll-contributions --commit FULL_COMMIT_HASH \
    --archive /path/to/Argyll_V3.5.0_src.zip
```

Add `--check` to rederive and compare without writing generated output. The
default recipe/output selects `cr_pr`; `--recipe` and `--output` select another
explicit import. The source must be clean and descend from `upstream/v3.5.0`.
The importer reads exact Git bytes with implicit fetch and replacement objects
disabled. It never merges driver branches. Commit recipe, generated series and
affected application policies/tests together. Test both source projections when
shared repairs change. Source consistency does not establish upstream acceptance
or physical qualification.

## Source-bound regression proofs

The BEFORE side of each C proof is a declared, authenticated predecessor in
[`proof_inputs`](../tools/native_argyll/proof_inputs/manifest.json). Unmodified
inputs reuse the official ZIP; changed predecessors are stored once as compressed
source with their SHA-256, upstream notices and license. The manifest is itself
pinned and includes the independent repair census. Corrupt, missing, extra or
misattributed inputs fail validation.

The AFTER side reads the actual prepared production source, including later
reviewed changes. It does not reconstruct that source from Python replacements
or reverse later repairs. A harmless C comment is accepted and included in the
receipt's source hash. Behavior regressions and corrupt predecessor bindings
must fail independently. Test-only mutants edit owned production-source copies.

Proofs compile real functions or explicitly bounded fragments with scripted
inputs and inert dependencies. Each report states its extraction, support-file
hashes, commands, compiler, results and limits. Sanitizer diagnostics are failures,
including during runtime discovery; missing instrumentation is not a sanitizer
pass. Contribution-specific Windows runtime limitations are recorded explicitly.

| Proof owner | Behavior checked |
| --- | --- |
| `calibration_repairs` | DTP41 acknowledgment, DTP92 completion masks and specbos state/lock cleanup. |
| `ss_xy_metadata`, `ss_protocol` | X-Y measurement metadata and retries; communication-error prefixes and unsigned decoding. |
| `spyder_calibration_slots`, `spyder_selection_path`, `spyder_black_cache` | Advertised calibration capacity, matching measurement paths and black-cache channel ownership. |
| `disptype_list_release`, `disptype_list_member_release` | Driver/list-entry ownership on failure, successful listings and selector payloads. |
| `legacy_reflective_calibration`, `legacy_nonadaptive_calibration`, `nonadaptive_calibration` | Original i1Pro and i1Pro3 calibration decisions, mode restoration, failure validity and cleanup. |
| `legacy_scan_recognition`, `scan_normalization` | Finite strip recognition, normalization, refusal and returned quantities. |
| `munki_scan_margin`, `munki_nonadaptive_calibration` | Margin agreement and nonadaptive exposure selection without stale success. |
| `munki_curve_release`, `munki_white_buffer_release`, `munki_dark_copy` | Resource ownership and dark-bank exposure pairing. |
| `munki_ledtemp_slope`, `i1pro3_wavelength_finite`, `scan_finite` | Finite compensation and wavelength results, invalid averaging and caller refusal. |
| `xyz_only_conversion`, `conversion_allocation` | Corrected XYZ when spectra are not requested; complete helper/caller allocation-error propagation. |
| `filter_values`, `xcalstd_option`, `scan_tolerance_option` | Transactional filter refusal and valid option domains. Three-sample cubic inputs use the production linear fallback. |
| `ccss_cgats`, `ieee754_special` | Numeric CCSS storage/grid checks and bounded IEEE special-value conversion. Integer spectral columns remain supported. |
| `display_timing`, `display_timing_spectral` | Sampling prerequisites, timing state and spectral-driver behavior. |
| `flash_capture`, `native_resync_proof` | Flash endpoints/saturation, current calibration validity, finite publication and cleanup across shared drivers. |

### Shared-driver calibration repairs

Calibration success requires the requested calibration to complete with its
proper acknowledgment and masks. Failed capture does not publish new factors or
leave overwritten calibration data valid. Mode restoration, cached-state
handling and cleanup are checked with scripted failures. Drivers retain genuine
device differences; the application does not introduce family-specific frontend,
persistence or acquisition stacks. See the [instrument contract](instrument.md).

### Calibration state and failure cleanup

The proof set checks invalidation before live dark/white writes, failed exposure
selection, finite factors, saved exposure bounds and refusal before publication.
Distinct native failure codes, required-calibration masks, original quantities
and optional diagnostic outcomes remain distinct. A prior valid factor/date can
remain stored after a failed refresh while its validity flag is cleared; stored
bytes are not proof that they may be used.

Synthetic probes do not execute EEPROM writes, instrument calibration, physical
switches or optical measurements. They do not establish accuracy, repeatability,
real cancellation latency or general correctness beyond their declared cases.
Use the [instrument-testing guide](testing-with-an-instrument.md) only within a
separately authorized instrument session.
