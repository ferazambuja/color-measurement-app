# Testing without instruments

The complete software check runs without a connected meter. It tests synthetic
observations, numerical conversions, saving/reloading and the installed package.
It does not test instrument communication, calibration or optical accuracy.

## One command

From the repository root, run the complete software check including MAT
export/import:

```sh
npm ci --prefix tools/desktop_builder --ignore-scripts --no-audit --no-fund
uv run --locked --extra mat python tools/test_all.py --require-mat
```

Node.js 22, 24 or 26 and the locked desktop-builder dependencies are required by every form
of the gate, the default form above included. The gate checks them before any
stage runs and requires both actual JavaScript bridge and DOM test cases to
pass. Missing dependencies or skipped, missing, duplicated or failed renderer
anchors fail acceptance, and so does a DOM anchor that records no executed
renderer case. Structured Node events must contain successful named test leaves,
with matching plan and outcome counts. File-only or suite-only success, skipped,
todo, failed or cancelled coverage and filtering/replay selectors are refused.
An empty file can exit 0 and report a file-level pass, so that result cannot
qualify required screen coverage. The gate prints the actual executed leaf count.
The CI
workflows select Node.js 22 and install the same locked builder dependencies.
The builder declares these supported major versions in `package.json`; the
repository npm configuration refuses an unsupported engine during installation. These checks do not
qualify visual layout, WebGL, native keyboard or accessibility behavior.

For acceptance covering native composition and both substituted workers, use
the full form. It adds the pinned source archive and the combined CR/PR contribution.
Its native stage runs first and builds both substituted workers from this
checkout with that contribution; the pytest stage then uses them
([below](#optional-native-stub-integration-pytest)). The full form also needs
the `app` extra. The Qt host checks run on all three operating systems. Install
the documented desktop runtime libraries on Linux. Expected off-macOS scope is
`PARTIAL`, naming the [case-specific platform exceptions](platforms.md#send-a-software-report).
Other skips require investigation. Preserve the printed scope
line verbatim in a platform report:

```sh
npm ci --prefix tools/desktop_builder --ignore-scripts --no-audit --no-fund
uv sync --locked --extra mat --extra app
uv run --locked --extra mat --extra app python tools/test_all.py --require-mat \
    --argyll-archive <source.zip> \
    --argyll-contribution tools/native_argyll/contributions/cr_pr
```

For each change, test the affected behavior and callers with the
[focused profiles](#focused-component-checks) and relevant individual checks.
Run the full form when broader integration, native composition or
installed-package concerns remain, or when explicitly requested. A documentation
or comment change does not require repeating an unchanged accepted suite.
Schedule one owner for a long run and reuse receipts only when their source and
relevant inputs still match. Report the tested scope; a focused pass is not a
full-form result, and an older full-form result does not test later source.

For the base package without the optional MAT dependency, use:

```sh
uv run --locked python tools/test_all.py
```

The runner reports MAT coverage from its pytest report as `EXERCISED`,
`UNAVAILABLE` or `PARTIAL`. `--require-mat` fails if requested MAT cases skip;
the optional extra supplies SciPy. Without that requirement and without SciPy,
MAT cases may skip while JSON/SPECT checks still run; that run does not establish
MAT functionality. This coverage check uses Python and does not launch MATLAB.
CI supplies the `mat` extra and requests `--require-mat`. The isolated installed-wheel check deliberately
uses minimal dependencies and does not establish MAT functionality.

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), this
checkout with its lockfile, and a supported Python version (3.11–3.14). `uv`
selects the project interpreter and installs the locked development environment;
the first run can require network access for Python, dependencies and build
tools. Runtime/development dependencies use the project lockfile; the isolated
build backend is resolved from the range in `pyproject.toml` separately.
No Argyll installation, device driver or instrument is needed.

The command runs on the current operating system and selected interpreter.
The [CI matrix](../.github/workflows/software.yml) repeats the entrypoint with the `mat` extra:
Linux with Python 3.11 and 3.14 on public-repository pushes to `main` or pull request, and
Linux and Windows with Python 3.11–3.14 weekly or on demand. Private
repositories require explicit dispatch; they do not spend minutes on pushes or schedules. Each job allows
35 minutes for dependency setup, the shared 30-minute pytest stage and the
remaining checks. The matrix does not run macOS. Run the complete form locally on macOS when
qualifying its native host. Each CI result applies only to its actual OS,
interpreter, source and selected inputs; it does not qualify an unexecuted cell.

To include a repository contribution in the native stage, use
`tools/test_all.py --argyll-archive <source.zip> --argyll-contribution tools/native_argyll/contributions/cr_pr`.
This requires the broad build. The workers the native stage builds carry
the same contribution. A worker supplied with `--generic-stub` or `--chart-stub`
replaces the built one, so build it with the same contribution, using
`tools/argyll_native_check.py --archive <source.zip> --output <new-directory> --contribution tools/native_argyll/contributions/cr_pr`.
Worker checks include the selected series manifest and files as well as the
application's native sources and build transforms. The shared selection and
measurement routes include CR-250, CR-100, CR-300, PR-655 and PR-670; both production
drivers run over simulated serial transport. The composed catalog has 43 names
and 42 linked types. The shared session check runs nine base personas, three CR
models and both PR models, for fourteen model personas. Composing the series and
passing these checks does not establish physical operation.

## What the command checks

| Check | What it establishes |
| --- | --- |
| Full pytest suite | Numerical expectations, malformed-input refusal, operation/cancellation behavior, records, serialization, recovery and developer-tool regressions; every tracked example runs, and the coverage map below names exactly the test files that exist |
| Ruff | Configured Python style and static error checks |
| Documentation agrees with the tree | The four tristimulus conversions and `reflectance_fraction` have required matching source excerpts; all marked excerpts are checked. Repository-relative inline links and reference definitions in `docs/` and the top-level documents name existing paths; fragments into Markdown pages match ATX headings |
| Source distribution and wheel | The current checkout can build both local package formats |
| Isolated wheel check | Wheel package files must match the current build source's recursive SHA-256 inventory, including subpackages and resources. A fresh installation must match that same source inventory and pass the effect-guarded import walk, exact module paths, prefix origins and guard canaries. It then runs the CLI, simulated acquisition, saving/export and save recovery without changing the retained observation |

The build and installed-package checks use a unique temporary workspace, removed
on ordinary completion or failure. The runner does not clear the checkout's
`dist/` directory or measurement files. Local development environments and tool
caches can still be created or updated. Nothing is published and no
physical-device command is executed.

Source runs bind package imports to this checkout's `src/` and refuse a cached
foreign package. Each pytest interpreter executes the origin check through
`tests/conftest.py`. Ordinary Python subprocesses inherit the source path when
the caller preserves its environment; they do not automatically execute
conftest or an independent origin check. The explicit import-checker children
execute their own guards.

Installed pytest is explicit through `CM_TEST_IMPORT_MODE=installed`: conftest
requires both the executing interpreter's prefix and exact recursive package
files matching the `src/` tree beside that test suite. Prefix membership alone
does not qualify an older installed package. For the installed import checker,
`--installed-prefix` must equal its interpreter's prefix and requires
`--expected-source-root` naming the current build's `src/`. The wheel, installed
files and imported modules must agree with that source, rather than an
installation's or wheel's own inventory. Interpreter bytecode caches are
excluded; source/resource symlinks, missing, changed and extra files are refused.

`tools/test_all.py` and `tools/check_core_imports.py` are source-distribution
developer tools, not wheel modules. The source runner launches the checker
script against an isolated installed wheel. Installed pytest applies only to
selected package tests that do not require source-only tooling; it is not the
entire source/developer suite run from a wheel. Source and installed proof cannot
silently substitute for each other. Import guards derive excluded optional
modules from the declared extras and distribution/import aliases. They detect
accidental effects, rather than providing a sandbox for hostile Python code.

The runner prints the Python version, OS and numbered stages. Success includes
`PASS: all 5 software checks completed on this Python/OS.` and exits 0. A separate
final summary reports native-stub pytest coverage as `EXERCISED` or `UNAVAILABLE`,
based on the actual pytest report. Its last lines name the checked commit and
the scope. `Tree:` compares the checkout at the run's start and end: the commit
and `clean` or `with uncommitted changes`, or `changed during the run` with both
readings. The start reading is also printed first, as `Tree at start:`, so an
interrupted run retains its initial checkout identity. This does not certify
what later stages saw if files changed during execution. The comparison
fingerprints the Git index and the contents, modes and
symlink targets of tracked and nonignored untracked files, so another edit to an
already-modified file is detected too. Ignored inputs and changes made and undone
between readings are not covered. The readings are not atomic snapshots or an
isolated execution environment; an unreadable checkout is reported as unknown.
`Scope:` says `FULL-FORM gate` only with the archive, the contribution, both
substituted workers (supplied, or built by the native stage) and `--require-mat`, each of which fails the run if its
cases skip, and only when no other case skipped; otherwise it says `PARTIAL gate`
and lists the options not supplied or the skipped cases. A `Skipped:` line always
names every skipped case with its reason (`none` when nothing skipped), because a
skipped test is scope that did not run: without the `app` extra the native alert
and menu cases skip, and that run is partial however green it looks (the
renderer cases cannot skip: their dependencies are required before any stage).
The three facts are separate: what the tree was, what was supplied, and the
coverage lines above for what ran. Quote those lines, not a stage count,
when reporting acceptance. It stops
at the first failed stage and exits 1; subsequent stages have not passed.
Ctrl-C interrupts the full run with status 130. The pytest stage runs each test
module in its own pytest process and merges their reports. When selected, the
renderer client module runs alone first because it starts Node and a Python
peer. The remaining modules run `--jobs` at a time (default: the CPU count). Discovery includes nested modules and both default pytest
filename patterns; report paths retain the module path so duplicate basenames
cannot overwrite one another. It merges the reports for the coverage lines; every module therefore also passes
alone, as focused runs need. It prints one line per module with its skip reasons,
then names every failed module. The exclusive and parallel phases share one
1800-second deadline to allow native proof compilation on slower or shared hosts;
use a lower `--jobs` on a host
other runs are using. Other child commands retain a 300-second timeout; neither is
a single timeout for the whole run. A module timeout aborts pending modules and
interrupts active direct children. Cleanup waits are bounded even when a
descendant retains a captured output pipe. The runner preserves the caller's
process group; it does not create isolated groups for modules. Descendant
retirement requires an outer owner of the whole gate job. Standalone invocations
and CI do not establish immediate descendant retirement on timeout or abrupt
owner loss; CI's job timeout provides a separate eventual boundary. A failed or
interrupted stage is not a successful cleanup receipt. There are
no stage-skip flags. The full runner ignores ambient `PYTEST_ADDOPTS` filters
so a previously selected pytest subset cannot stand in for the suite. Use the
standalone focused commands below for a subset. Read the pytest summary too:
a skipped test was not run.
The POSIX subprocess SIGINT-delivery test is intentionally skipped on Windows;
portable cancellation-token tests still run. The optional manual Ctrl-C check
below covers the current terminal interaction but does not make the skipped
automated test a pass. Do not use a fixed total test count as the success rule;
counts change as tests are added.
With no supplied substituted worker, the native-stub integration pytest is
skipped; the final summary states that reason and how to enable it. The current
Python software CI matrix also runs without a supplied native worker. A passing default suite
therefore does not establish execution of that integration case.
The default run also prints that the optional native Argyll build was not
selected. That build requires the separate source archive and platform compiler
described [below](#optional-native-argyll-feasibility-check).

If a stage fails, keep its output, the current Git revision, OS and Python
version. Correct the reported failure and rerun the full command. An inability
to install dependencies or build a package is an incomplete check, not a
successful scientific test. Do not suppress a failure by deleting its test or
bypassing a source-excerpt check. No failure here calls for connecting a meter
or retrying a physical exposure.

## Focused component checks

Use [`tools/test_scoped.py`](../tools/test_scoped.py) during development to run
explicit component groups and their shared contract tests:

```sh
uv run --locked python tools/test_scoped.py --list
uv run --locked python tools/test_scoped.py science
uv run --locked --extra mat python tools/test_scoped.py records frontend --require-mat
```

`--list` prints the exact test modules without running tests. Add profile names
to list only those groups, for example `--list instrument native`. Multiple profiles
run the union of their modules once, each module in its own pytest process,
`--jobs` at a time (default: the CPU count), as the full runner does. Choose all affected groups using the code
and interfaces changed:

Profiles containing `test_desktop_client` (`application` and `frontend`) require
the same Node.js/locked-jsdom setup and executed bridge/DOM anchors. Non-UI
profiles such as `science`, `records` or `tooling` deliberately exclude the
renderer module and do not require those dependencies. A standalone partial
pytest run may report optional renderer skips; it is not full or UI-profile
acceptance.

| Changed component | Profile | Included shared behavior |
| --- | --- | --- |
| XYZ/xyY, CIELAB or reflectance conversion | `science` | Numerical methods, fraction conversion and workflow/record contracts |
| Observation metadata, records or file exports | `records` | Workflow, capture metadata, operation records, JSON/MAT and SPECT exports |
| Requests, polling, events or retained results | `frontend` | Shared Demo/instrument operations, retained-reference events, lifecycle and record/export contracts |
| Shared acquisition, including all i1Pro models | `instrument` | Acquisition/frontend, discovery, correction, records/exports, native transport and fraction conversion |
| Native protocol, source patches or build policy | `native` | Transport/description and source-backed no-termination, calibration, flash, scan and legacy-build controls |
| Developer runners, documentation checks or static inventory | `tooling` | Inventory, excerpt-checker and full/focused-runner regressions |

When changing test runners, discovery or module registration, run Ruff on the
changed Python files and the `tooling` profile alongside the affected component
checks. Register each new test module in its component profiles and add its row
to the coverage map before qualifying the new module alone.

For an acquisition/native change, combine `instrument native`. Source-backed
controls belong to `native` and are not implicitly selected by `instrument`.
Shared changes can also require `frontend` or `records`; the runner does not infer affected tests
from a Git diff. It refuses unknown/empty selections and stale module mappings,
and checks that every selected module appears in pytest's report. Register new
test modules in its `PROFILES` mapping.

Require optional integrations when they are part of the change:

```sh
uv run --locked --extra mat python tools/test_scoped.py instrument native --require-mat \
    --generic-stub /absolute/path/generic-session-stub-worker \
    --chart-stub /absolute/path/generic-chart-stub-worker \
    --argyll-archive /path/to/Argyll_V3.5.0_src.zip
```

The profiles are `science`, `records`, `application`, `frontend`, `instrument`,
`native` and `tooling`; `--list` prints each one's exact modules. `application`
checks the headless Demo batch plus shared frontend/record/publication and
import-effect boundaries. There is one acquisition
profile because there is one acquisition path.

`--require-mat` needs a profile containing operation-export tests, such as
`records`, `application`, `frontend` or `instrument`. Stub integrations run with `frontend`,
`instrument` or `native`; the separate runner summary uses anchors in
`test_instrument` when that module is selected. Use only an existing trusted
[dedicated substituted worker](#optional-native-stub-integration-pytest), with
`.exe` on Windows. The `COLOR_MEASUREMENT_GENERIC_STUB` and
`COLOR_MEASUREMENT_CHART_STUB` variables are also accepted; unrelated inherited
stub variables are cleared. Required MAT/native-stub coverage fails if its
anchor is absent or skipped. Unrequested optional skips are reported explicitly.

`--argyll-archive` requires `native`. The shared
[`argyll_archive` fixture](../tests/conftest.py) reads `CM_ARGYLL_ARCHIVE`, which
the option sets, or defaults to `local/argyll-audit/Argyll_V3.5.0_src.zip`.
The supplied ZIP may live elsewhere. It must match the pinned 3.5.0 SHA-256
`f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f`.
An absent default skips dependent cases; an explicitly supplied missing file or
an incorrect digest fails. Source-backed C proofs also need their compiler.
This runner does not download the archive, build the full selected worker or
operate hardware; the selected pytest modules can compile and run inert probes.

The focused runner executes pytest only, prints selected modules and skip
reasons, and removes its temporary report afterward. It does not run the full
lint, current-document excerpt, package-build or installed-wheel stages. Even
selecting all profiles is not full acceptance: use
[`tools/test_all.py`](#one-command) after the connected change is ready for
complete validation. A focused pass establishes only its selected scope.

The renderer can also join the authenticated substituted Argyll setup and
acquisition path through the existing Python owner peer. Supply the original
substituted-worker report and the current interpreter explicitly:

```sh
CM_RENDERER_NATIVE_REPORT=/path/to/substituted/report.json \
CM_RENDERER_PYTHON=/path/to/python \
node --test --test-concurrency=1 \
    --test-reporter=./tools/desktop_builder/renderer-test-reporter.mjs \
    tools/desktop_builder/renderer.test.mjs
```

This requires the locked desktop builder dependencies. Without the explicit
report, those native integration leaves are not registered in the ordinary DOM
cohort, so they leave no skip behind. A gate run with `--argyll-contribution`
exports its own native report to the tests stage. The renderer anchor requires
all five named CR/PR and i1Pro3 leaves exactly once in the existing structured
Node receipt; a duplicate or unrelated name cannot replace a required leaf.
The gate requires that five-case result and reports
`Joined Argyll C renderer coverage: EXERCISED (N cases …)`;
other runs report that coverage `UNAVAILABLE`. With the report, three cases cover CR-250, CR-100 and PR-655 typed initialization,
binding, C-driver readings over scripted serial, native facts, automatic
CSV/MAT/TI3 output and saved review. A fourth retains a genuine CR native error
without a false reading or automatic retry. The existing loader authenticates
the report, worker bytes and current consumed source. Provenance stays simulated;
no Demo frontend or physical instrument substitutes for that joined proof.
A fifth renderer case joins the i1Pro 3 description and chart actors: query the
configured mode, require a fresh context, create a new batch with the newly
offered M1 setting, measure, save companions and reopen the retained reading.
It also requires the new batch to reset the earlier configuration-query task
to measurement. These actors substitute instrument methods; this case exercises
the renderer and Python composition without running the upstream i1Pro 3
acquisition driver.
The [Python owner tests](../tests/test_desktop_session.py) separately join the
i1Pro 3 description actor to the existing chart acquisition actor, including
mode refresh, supported settings, native mode refusal, calibration responses,
saving and reopening. Both actors substitute instrument methods; this is not
execution of the physical i1Pro 3 driver. CR-250, CR-100 and PR-655 cancellation
cases instead run the contributed C drivers over scripted serial transport and
cancel at their armed callback, retaining the terminal outcome for saved review.
Their placed, programmatic spot route needs no operator prompt; the checks
require that none is invented. Each cohort retains its own exercised scope;
none establishes physical device behavior.

## Coverage and focused diagnosis

Direct pytest commands remain useful for a single test or `-k` expression.
They do not establish completion of a component profile or the full gate.

| File or tool | Existing coverage |
| --- | --- |
| [`tests/test_proof_inputs.py`](../tests/test_proof_inputs.py) | Authenticated frozen predecessor inventory, archive/blob/notice digests, path and symlink refusal, current production reads and owned temporary-source cleanup. |
| [`tests/test_repair_provenance.py`](../tests/test_repair_provenance.py) | Source-local identities for all expanded repair entries, named proof modules and declared preparation producers; authenticated imported-path coverage; the generic and CR/PR projections import the same postimage of each relocated repair; production preparation refuses historical transforms, and reference proofs read current C without replaying old repairs. No hardware. |
| [`tests/test_resource_checkout_bytes.py`](../tests/test_resource_checkout_bytes.py) | Actual scientific-resource Git bytes under three autocrlf settings, with a negative control demonstrating loss from generic LF normalization. No resource rewriting or hardware. |
| [`tests/test_source_records.py`](../tests/test_source_records.py) | Current standalone operation JSON/MAT and color observation JSON/MAT/CSV, exact original bytes and canonical-JSON identity, source loss/replacement refusal, read-only live-owner preservation, native export/retry, SPECT/TI3 component reasons and capture authentication. No hardware. |
| [`tests/test_application_chart.py`](../tests/test_application_chart.py) | Actual Demo preparation/intent/dispatch/result/chart coupling, UUID once-only behavior, rotation without retargeting, cancelled retry retaining the selected success, pause/stale revision before preparation, explicit incomplete Finish, original-context tamper and destination-owned continuation originals. |
| [`tests/test_chart_sources.py`](../tests/test_chart_sources.py) | Portable metadata-only source graph authentication through current canonical callbacks; exact original native results and failed/stopped recovery, inherited identity/history, hash tamper, missing files, cycles, duplicates and bounded segments/bytes. |
| [`tests/test_dataset_binding.py`](../tests/test_dataset_binding.py) | Exact captured-source authentication, live/saved binding and generation fences, missing/changed source refusal, read-only review and preserved live ownership. |
| [`tests/test_dataset_namespace.py`](../tests/test_dataset_namespace.py) | Original inherited reading identities across live, saved and MAT datasets; source-qualified annotation/chart queries, comparison persistence and canonical revision fences for chart colors. |
| [`tests/test_batch_archive.py`](../tests/test_batch_archive.py) | Whole-batch MAT closure, exact canonical/parent/diagnostic/recovery bytes, declared source identities, retained unavailable inputs, immutable reopening and independently available CSV. |
| [`tests/test_document_mat.py`](../tests/test_document_mat.py) | Declared JSON-bearing MAT documents, exact signed/null/Unicode data and raw JSON, digest/schema refusal and missing-dependency behavior. No arbitrary MATLAB session import. |
| [`tests/test_desktop_setup.py`](../tests/test_desktop_setup.py) | Deliberate report/listing/selected initialization through InstrumentBuild over inert clients; model/opening/budget policy, registration rollback, source retirement/replacement and candidate consumption. No hardware. |
| [`tests/test_mode_options.py`](../tests/test_mode_options.py) | Configured-mode high-resolution, averaging, modifier and filter offers from exact capability words, without a global union authorizing another mode. |
| [`tests/test_reading_dataset.py`](../tests/test_reading_dataset.py) | Mixed validated color/scalar/timing records, independent quantity absence, shared analysis/CSV and actual software live/saved bridge workflows. |
| [`tests/test_workflow_layout.py`](../tests/test_workflow_layout.py) | Complete generic primitive form metadata, layout/admission refusal before native workers, omission and geometry rules, and controlled-presentation stamp ownership. |
| [`tests/test_analysis_exports.py`](../tests/test_analysis_exports.py) | Source-bound selected/all JSON/CSV analysis, preserved native/derived roles and exclusive publication with retained prepared context. |
| [`tests/test_qualified_science.py`](../tests/test_qualified_science.py) | Explicit qualified adaptation and spectral consumers, independent native/derived quantities, finite Lab-white refusal and captured scientific context. |
| [`tests/test_chart_state.py`](../tests/test_chart_state.py) | Pure guided-chart geometry and canonical/native mappings, authenticated TI2 axis confirmation, immutable attempt targeting under rotation/inspection, selected-success retention after failed retries, material-pass separation, qualified placement Keep/Retry/Later receipts, explicit incomplete finish, exact accepted-source continuation and inherited reading identity, metadata-only bounded source graphs with linear continuation growth, compact view/one-attempt details, recomputed terminal digests retaining original native status/reason through unresolved chart classification, immutable same-pass references, and explicit measured-run-to-comparison pass transitions. The documented software-only example runs with the current API. Focus with `pytest tests/test_chart_state.py`; no acquisition, publication or hardware. |
| [`tests/test_science.py`](../tests/test_science.py) | Analytical XYZ/xyY and Lab examples; explicit white and common-scale behavior; mixed and boundary branches; signed inputs; undefined chromaticity; invalid/nonfinite inputs; overflow and finite-precision limits |
| [`tests/test_science_s1.py`](../tests/test_science_s1.py) | All 34 published CIEDE2000 pairs, unequal-chroma above-turn regression and wrong-branch rejection, symmetry, fixed unit weights and finite-domain limits; analytical CIE 1976 u′v′ with retained Y, scale invariance and undefined denominators; exact-grid signed spectral residual and robust equal-sample RMS with immutable native inputs |
| [`tests/test_xyz_comparison.py`](../tests/test_xyz_comparison.py) | Typed native XYZ comparison, independent rational coordinate-distance and signed Y-percentage examples, exact integer residual, distance symmetry/percentage asymmetry, four-field context and quantity fences, signed inputs, independent missing/domain/overflow reasons and immutable native records; no physical comparison qualification |
| [`tests/test_science_rgb.py`](../tests/test_science_rgb.py) | Explicit common-scale CSS-derived D65-target sRGB and Display P3 matrices/transfer functions, independent nominal-D65 and primary checks, signed linear channels, separate lower/upper channel masks and chromaticity eligibility/triangle states, cube-shell XYZ/explicit-white Lab geometry/connectivity, black/negative/over-scale examples, invalid inputs and finite-arithmetic limits |
| [`tests/test_core_imports.py`](../tests/test_core_imports.py) | Isolated import-effect guards: read-only imports, excluded `__main__`, write flags, mutations, optional dependencies, subprocess/network starts, direct and cached thread starts, and attempts caught by imported code. The installed-wheel stage separately runs the same guarded core package walk with no extras and verifies its installation origin. Guards detect accidental effects, not hostile Python code. |
| [`tests/test_application.py`](../tests/test_application.py) | Public Demo batches, calibration/cancelled outcomes, frozen requests, duplicate/concurrent/rebound admission, saved intent before start, publication/manifest fault recovery, authenticated rescue despite old-file edits, same-folder retry, separate batch/TI3 locks, raw invalid terminal retention, explicit abandonment and immediate cleanup-uncertainty gates, Python annotation drafts/versioned async status, no-op edits, metadata edits refresh TI3 without rereading native records; selection-only changes do not regenerate TI3, compact snapshots and 200 distinct Demo readings, explicit exports, crash readback and Finish/New batch. Software-only owner checks, without a host or physical instrument. |
| [`tests/test_desktop_session.py`](../tests/test_desktop_session.py) | Connected simulated owner bridge, generation/UUID reconciliation, exact notes, read-only analysis, view rebind, Finish/review/New batch and bounded requests; saved unplotted reference with independent native Y/coordinate metrics, Lab-white/preview-scale independence, availability summaries and refusal of unsaved/stale/externally changed context; no native UI or equipment. |
| [`tests/test_desktop_corrections.py`](../tests/test_desktop_corrections.py) | Native-held CCMX/CCSS tokens, exact chosen bytes and provenance, selected-source/settings and current-draft fences, configured-mode correction kinds, explicit Clear, renderer path refusal and retained command replay through existing owners. Inert validation and forwarding only; no native worker or hardware. |
| [`tests/test_desktop_science_materials.py`](../tests/test_desktop_science_materials.py) | Native chooser doubles and captured illuminant/characterization tokens through saved-dataset derivation and actual analysis export; exact source bytes/digests, role/binding/revision fences, malformed/nonfinite/oversized material refusal, changed bytes/inodes and forged role/token/identity refusal before export. No native transport, Qt event loop, hardware or physical-validity proof. |
| [`tests/test_desktop_client.py`](../tests/test_desktop_client.py) | Actual JavaScript bridge client and renderer with an inert message peer and locked jsdom DOM: lost-return identity, dialog errors, interleaved annotation target/text, sample drafts, save progress, view persistence and camera math. No visual layout, WebGL or native accessibility proof. Install the locked desktop builder dependencies for the renderer cases. |
| [`tests/test_desktop_direct.py`](../tests/test_desktop_direct.py) | Native-only selected direct port setup over scripted transports, UUID reconciliation, persistent host closure, cleanup-required binding, reported-identity reopen and registration rollback. No OS ports or hardware. |
| [`tests/test_serial_instruments.py`](../tests/test_serial_instruments.py) | Scripted direct identity and native acquisition for five CR/CS models, setting readback, source-bound chart facts, busy/error cleanup refusal, documentary finalization and aggregate byte bounds. PR acquisition uses the shared native tests. No hardware. |
| [`tests/test_serial_protocols.py`](../tests/test_serial_protocols.py) | Pure native CR/CS reply parsing, exact decimal fields, complete native grids, signed samples and unavailable spectral sentinels. No physical operation. |
| [`tests/test_serial_settings.py`](../tests/test_serial_settings.py) | Source-backed CR/CS current-setting interpretation and complete-target planning, native IDs, bounds, dependencies and setter ordering. No writes. |
| [`tests/test_serial_transport.py`](../tests/test_serial_transport.py) | Injected byte transport and clocks: fragmented framing, echo lines returned unchanged, partial writes, cancellation, response bounds, quarantine and confirmed or failed host close. No OS ports. |
| [`tests/test_activity.py`](../tests/test_activity.py) | Selected-source Activity rosters and exact details for live, saved, standalone and inherited outcomes, including failed/cancelled zero-reading results, excluded parsed records, raw terminal recovery, full annotations, bounded recent events, source-qualified identity collisions, stale-source/revision refusal and authenticated diagnostic head/tail bytes. Inert/scripted source composition only; no acquisition, file publication, native UI or hardware validation. |
| [`tests/test_docs_application_example.py`](../tests/test_docs_application_example.py) | The complete-route example in `docs/application.md` is extracted and executed as written against the Demo frontend in a fresh working directory; it must exit 0, answer every simulated instruction and review one saved reading. A stale payload in the example fails here instead of in a reader's terminal. |
| [`tests/test_desktop_host.py`](../tests/test_desktop_host.py) | Portable controller concurrency, exact close/discard and direct-setup cleanup; real Qt channel/world isolation, native choice defaults, UI-thread dispatch and reply-capture release, navigation and renderer recovery; frozen code/resource and bundled-worker authentication. Software-only native UI checks, without physical instrument or VoiceOver qualification. |
| [`tests/test_presentation.py`](../tests/test_presentation.py) | Shared-owner illustration compositions, original CIE bytes/attribution, pixel-coordinate recipe and relative output Y cap; no physical appearance proof. |
| [`tests/test_science_standard_white.py`](../tests/test_science_standard_white.py) | Nominal D50/D65 references, immutable finite construction, metadata consistency and domain refusals. |
| [`tests/test_science_uvy_inverse.py`](../tests/test_science_uvy_inverse.py) | CIE 1976 inverse, independent numerical examples, round trips and undefined-domain refusals. |
| [`tests/test_reflectance_fraction.py`](../tests/test_reflectance_fraction.py) | Typed M1 percent-to-fraction conversion; preserved grid, signed/above-one values and unchanged native record; flat 36-band scale checks; refusal of missing/inconsistent metadata, other quantities, units and modes |
| [`tests/test_mode_snapshot_refresh.py`](../tests/test_mode_snapshot_refresh.py) | Shared configured-mode queries without trigger/acquisition; final capability workflows, stale descriptors, independent setter/getter state, explicit invalidation, display-before-override-before-calibration ordering, native diagnostics and zero-patch JSON/MAT retention. Uses authenticated substitute workers, without hardware. |
| [`tests/test_refresh_frequency.py`](../tests/test_refresh_frequency.py) | Shared measured-Hz acquisition: capability and geometry gating, no colour trigger, native errors and invalid values without automatic repetition, cancellation and deadlines, late scalar retention, declared stimulus identity, spectrum-free JSON/MAT records, save retry and explicitly simulated frontend outcomes. Connected cases require authenticated substituted workers; no physical refresh accuracy is established |
| [`tests/test_display_timing_frontend.py`](../tests/test_display_timing_frontend.py) | Separate native delay/reaction records, zero delay and signed reaction, distinct clock domains, typed and bounded presentation responses, no ordinary acknowledgement substitution, missing/early/duplicate/wrong responses, native failure/default suppression, shared cancellation and JSON/MAT retention. Authenticated substituted sessions cover the same ladder across timing-capable families, including post-mode capabilities; no physical display response is established |
| [`tests/test_display_timing.py`](../tests/test_display_timing.py) | Exact source-bound i1d3/Klein delay methods over scripted clock and samples: per-call time limit, last sample retention, finite/range-checked spacing and integer conversion, positive transition and estimator checks, error/state restoration, frozen predecessor/current production comparison and compiled regressions under sanitizers. The proof does not link instrument transport or validate optics |
| [`tests/test_display_timing_spectral.py`](../tests/test_display_timing_spectral.py) | Frozen spectral-delay predecessors and actual production methods; source context, timing/finite-state guards, valid controls, C mutants and corrupt-baseline refusal. |
| [`tests/test_operation_records.py`](../tests/test_operation_records.py) | Complete and partial ordered results, zero-row calibration, request/observation identity, optional XYZ/spectra, unchanged nested observation serialization and strict schema refusal |
| [`tests/test_native_controls.py`](../tests/test_native_controls.py) | Immutable typed changes; original PR raw chunks and separate settings/capture envelopes; current PR model/grid/serial identity; complete requested/confirmed settings correspondence; early native failures; settings allowance and zero-patch persistence; recovery debt and ownership across frontend replacement; cleanup-wrapped attempted-only debt; PR actual XYZ/spectrum publication and independently retained refused quantities; matching native C and Python limits for line/reply bytes, traces, rows, spectral samples/chunks and outbound control-argument size, plus each driver's spectrum chunk ceiling. The control-argument pin uses its Python sender's `native_controls.CONTROL_ARGUMENT_LIMIT`; retained native text has its own bound. Scripted sessions perform no instrument I/O. |
| [`tests/test_import_contribution.py`](../tests/test_import_contribution.py) | Contribution manifest admission, exact source/patch digests, declared target paths and refusal of malformed or unsafe import inputs using temporary source fixtures. |
| [`tests/test_frontend.py`](../tests/test_frontend.py) | Synthetic descriptors, typed requests/responses, deep-request refusal before adaptation/ownership, preservation of pending presentation state after depth refusal, prepared identity before I/O, gated dispatch and cancellation, neutral before/post-spawn/interruption/lost-return controls, ownership retained for unknown cleanup, background polling/cancellation, bounded events, partial/calibration-only outcomes, and atomic terminal recovery after result-constructor, serializer or event-emitter failures |
| [`tests/test_workflow.py`](../tests/test_workflow.py) | Labelled simulation; operation ordering/ownership; cancellation before and during acquisition; missing/signed spectra; immutable typed metadata, schema and reference-white validation; JSON/CSV round trips; Unicode/CRLF; large CSV cells; save failure, cleanup warnings, no overwrite and recovery without reacquisition |
| [`tests/test_doc_snippets.py`](../tests/test_doc_snippets.py) | Actual symbol extraction without execution; missing/ambiguous symbols, changed text, malformed fences and external paths are refused. Link cases cover missing targets and headings, titled inline links, reference definitions with same-line or next-line destinations, same-page and cross-file Markdown fragments, encoded fragments, duplicate heading slugs, fenced code, HTML comments and checkout escapes. Footnotes are distinct from reference definitions. External URLs are not fetched; fragments in non-Markdown files are not interpreted. Top-level documents are checked with `docs/` |
| [`tests/test_test_all.py`](../tests/test_test_all.py) | Runner failure/timeout propagation, cancellation, required tooling, child-process isolation and missing-wheel refusal |
| [`tests/test_bench_session.py`](../tests/test_bench_session.py) | Bench runner checks over real Demo batches saved by the batch owner: Demo-only rehearsal, model, serial and mode identity, packaged app/current-source and bundled-report authentication, exact native report/version/both-worker attribution for physical records, changed-input refusals, preserved historical session schemas, accepted-reading counts, each model's spectrum rule and band count, physical native facts, calibration status, settings confirmation and restoration through the native settings seam, cancellation order, finished batch, hash-chained append-only records, step text that names the committed descriptors' mode and settings captions and every explicit spot condition, dependent steps skipped with their batch, no standard deviation from one reading, gated stop and an end-to-end rehearsal that resumes; no instrument, worker or port is used |
| [`tests/test_test_scoped.py`](../tests/test_test_scoped.py) | Profile inventory/union, invalid-scope refusal, required selected-module collection, optional-source authentication and explicit MAT/native-stub coverage; only pytest runs |
| [`tests/test_argyll_inventory.py`](../tests/test_argyll_inventory.py) | Static manual/catalog extraction, syntax preservation, provenance and explicit parsing gaps using synthetic input documents |
| [`tests/test_argyll_native.py`](../tests/test_argyll_native.py) | Synthetic native-result parsing, malformed JSON, quantities/validity, condition codes, event order, late output, timeout cleanup, matching source pins, dependency notices and source-patch boundaries. The shared process probe uses a host C compiler to check fragmented cancellation/EOF, exact UTF-8 arguments with a mutable argument vector, Unicode input filenames and complete bytes, plus Windows invalid-UTF-16 refusal and inert HID-library export availability. No upstream instrument runtime or equipment is required for that probe |
| [`tests/test_native_contribution.py`](../tests/test_native_contribution.py) | Authenticated contribution series: manifest shape, digest, path, symlink and base-archive refusals; exact-position diff application with no fuzz; post-application tree verification; unchanged stock numeric IDs and sentinel; native worker freshness includes contribution files; the repository's combined CR/PR series composes onto the pinned archive with the constructor guard before both drivers and passive-serial anchors intact; the enum pin extends only by the five declared model types; broad-build-only composition through `prepare`; GPL declarations checked for both production drivers and every added file |
| [`tests/test_argyll_description.py`](../tests/test_argyll_description.py) | Description factory/build binding, candidate and initialized identity, raw masks, nullable values, metadata event/terminal validation, static constructor/patch parsing and manifest separation of linked types from implemented measurement routes. Provenance cases extract each cited `init_inst` from the pinned archive by brace matching and hold the disclosure tables to it: no instrument called harmless touches its device on init, every disclosed one still does what it is disclosed for, and the two tables name the same instruments. The manifest parsers are checked against the C directly -- a case label separated from its return by a comment, a label reaching no reason, and the published catalog compared to `description.c` |
| [`tests/test_argyll_capability.py`](../tests/test_argyll_capability.py) | The capability decoder held to the pinned header: every axis, modifier, `inst2_`/`inst3_` bit, instrument name, display technology, measurement type and the whole calibration vocabulary re-derived from the digest-verified archive, so upstream drift fails rather than silently disagrees. Behaviour cases cover the eighteen named routes and their one-illumination/one-action decomposition, refresh-override precedence, adaptivity refused where the driver would drop it, total reproducible ranking, a colorimeter advertising more mode bits than it has modes, unknown bits reported rather than dropped, conditions kept out of the mode word, and that a calibration condition is a value and not a bitfield |
| [`tests/test_display_types.py`](../tests/test_display_types.py) | Named display calibrations: distinct duplicate names, omission, malformed catalogues, loaded CCMX/CCSS changes, stale selection and partial readback. The compiled production probe edits common metadata, all nine CCMX coefficients, and each grid field, normalization and sample in both synthetic CCSS sets; each edit must move the identity, as must instrument type, catalogue order and a change to the last entry, where installed files are listed. Field boundaries are held: index 1 named `2x` must not hash as index 12 named `x`. The load flag and each instrument-bank matrix coefficient must not move it (Spyder X/X2 selectors and `kleink10.c:k10_read_cal_matrix` write these). Mutation refusal requires an assertion failure without a sanitizer diagnostic, including the list-limit check. Invalid UTF-8 disables only its original entry, retains exact bytes and complete-list identity, and cannot authorize selection or be reported as applied. Connected cases exercise valid later indices, all-unavailable default operation, and stale invalid-byte changes. Native default markers are tested with zero, multiple, later and unavailable marked entries; omission never becomes a selection. The default flag participates in identity, while its JSON boolean is strict. The compiler/source proof is inert; connected cases require an authenticated substituted worker and establish no hardware calibration validity |
| [`tests/test_instrument.py`](../tests/test_instrument.py) | The device-agnostic client: selection by a name Argyll itself matches, a pinned instrument never searched for, and every Argyll route reaching an acquisition method. Chart geometry reconstructs rectangular and hexagonal grids the test laid out itself and refuses to navigate by points nobody sighted; sessions cover spot, strip, X-Y and stored-chart workflows, whole-or-nothing strip passes, click/filter/grey-ladder calibration, measurement conditions in upstream's order, deferred high resolution, matrix and spectral corrections with file lifetime, observation labelling and units, an unmeasured quantity never becoming a zero, a stored route refused before the read when nothing is stored, and option values typed against the descriptor -- a boolean that is not one, an integer given as text, a bool standing in for a count, and a choice outside its list -- each refused in the validator's own words |
| [`tests/test_instrument_replies.py`](../tests/test_instrument_replies.py) | Scripted spot and X-Y transports exercise acknowledge, skip, cancel, navigation and read-allowance writes. Closed pipes retain queued native diagnostics and timeout causes; completed workers cannot hide failed replies. Callback failures keep their exact identity when cancellation also fails, and unrelated I/O errors are not treated as worker exit. No-answer calibration notifications never send replies. |
| [`tests/test_instrument_lifecycle.py`](../tests/test_instrument_lifecycle.py) | Connected selection/description acceptance, same-process calibration, wrong sensor positions, cancellation/deadlines, signed XYZ, native condition refusal and strip order using real Python clients with substituted native workers |
| [`tests/test_native_serial_parity.py`](../tests/test_native_serial_parity.py) | Authenticated substituted description and session C workers: a valid UTF-8 serial passes through description, Python binding and acquisition unchanged; invalid driver serial bytes retain the session writer's byte-escaped JSON fallback. The paired case needs the broad native build; narrow builds skip it. No hardware or physical identity is tested |
| [`tests/test_instrument_frontend.py`](../tests/test_instrument_frontend.py) | One polled adapter over any instrument: the descriptor built from the instrument's own words rather than a table, capability-gated modes/options/workflows, a stale snapshot refused, `capability_id` moving with the words, answered and notified calibration prompts, the display level carried to the client, sheets driven and refused by capability, patch references, and a failed bind becoming a result that names its stage. A stored route's runtime status reaches the client on the result and is absent rather than zero when unasked; `started` echoes the event contract; a cancel answered to a prompt is honoured, and one arriving after the instrument returned keeps what was measured; with both substituted workers from the same build directory and a report containing the combined CR/PR contribution, every published fixture regenerated from that build and compared with the committed tree under the bounded quantity-roundoff policy below. Missing, extra or out-of-bound changed captures fail; diagnostics name the first differing JSON field per file, including embedded native JSON, with bounded before/after values. Publication preserves actual capture values |
| [`tests/test_demo.py`](../tests/test_demo.py) | Synthetic equipment demos: a bounded catalog of distinct personas, every one `synthetic`, not `live`, backend `argyll-demo`; every offered canonical route run through the generic operation with `simulated` provenance on the result and each observation; calibration, simulated device-button trigger, cancellation and repeated reading; setting forms validated and retained without claiming numerical effects; reverse strip order and JSON persistence through the common records; X-Y prompts keeping fiducial numbers; and a button prompt preserving the unacknowledged calibration reference. No worker, enumeration or hardware; and the packaged demo data equal to what the generator derives from the committed fixtures |
| [`tests/test_operator_setup.py`](../tests/test_operator_setup.py) | Manufacturer instructions and mode-specific calibration preparation: real instrument names, routes and conditions; short, attributed quotations; each live prompt carries its own condition's setup. Every native mode-preparation quote matches its cited file in the digest-verified pinned archive or the separately attributed bundled CR/PR contribution. Optional calibration stays optional and stored reads have no acquisition preparation. No worker or hardware |
| [`tests/test_discovery.py`](../tests/test_discovery.py) | One enumeration turned into a list a UI can offer: names from the build's own catalog, unidentified serial paths kept and marked, indices as positions in this enumeration, refused and initialization-unassessed devices listed but not attachable, and malformed enumeration/catalog refused rather than guessed. The composed entry point adds: a refresh that calls only the enumeration interface, both workers authenticated against one build report, a disclosed initialisation refused before the instrument is opened, a separate acknowledgement for an unassessed one, serial identification needing both an expected type and permission, two units of one model bound to records that differ, a refused enumeration never becoming an empty device list, the build report taken from `CM_ARGYLL_BUILD_REPORT` and never searched for, and the published detection captures. Every published native discovery scenario is replayed through the real build, answered as the worker answered the request actually made, so a dropped acknowledgement or a wrong expected type meets the captured refusal; an unidentified port is gated only by the type it is expected to be |
| [`tests/test_cgats.py`](../tests/test_cgats.py) | The CGATS reader against `cgats/cgats.c`: identifier fields, keywords and quoted values, wrapped rows, column typing and standard-field promotion, and refusal of empty files, wrong identifiers, disagreeing field/set counts, partial final sets, unterminated quotes and data before a format |
| [`tests/test_chart_targets.py`](../tests/test_chart_targets.py) | Bounded TI2 import; exact retained bytes and source-row identity; blank optional descriptions; supported channels, index orders and patch limits; malformed or incomplete layouts; nonregular-file refusal; retained-projection and digest tampering; explicit expected XYZ/Lab interpretation with analytical inverse values, immutable raw data, per-cell overflow and refusal of missing context or fallback to another representation; generated raw/interpreted/unavailable fixture currency and check-only stale-output refusals on temporary copies; semantic controls that fail when import guards are removed. Chart scientific consumers add original observation/context and canonical cell binding, native and expected common-white swatches, frozen measured-reference/policy rederivation, explicit scalar/cell limit refusals, CIE76 equality and nearest-region overlap, holes and unavailable material conditions. Source-bound context adapter cases use actual Demo/typed terminal records for native5, grids/norms, native settings, loss/enrichment, explicit continuation and original-reading fences. Synthetic target data only, without physical acquisition or hardware. |
| [`tests/test_colorimeter_correction.py`](../tests/test_colorimeter_correction.py) | Corrections against `xicc/ccmx.c` and `ccss.c`: matrix rows and XYZ application, required instrument keyword, display/technology targeting with unknown preserved, tristate refresh mode, base-id pass-through, and spectral sets with evenly spaced band centres, default normalisation and grid/sample bounds |
| [`tests/test_no_termination.py`](../tests/test_no_termination.py) | Shared USB/HID patch and source-extraction refusal checks; source-backed cases need the separately supplied pinned archive and a supported host compiler. Executed C probes substitute OS/device hooks; their source-flow results do not establish device access or Windows ABI behavior |
| [`tests/test_legacy_native_build.py`](../tests/test_legacy_native_build.py) | Broad production source safety, include ordering, EEPROM-write refusal, original i1Pro calibration behavior and retirement of narrow build switches. |
| [`tests/test_legacy_nonadaptive_calibration.py`](../tests/test_legacy_nonadaptive_calibration.py) | Exact four-site legacy correction, source/archive refusal, paired original/corrected full-function cases, corrected-only first-probe errors, paired exposure/dark banks, cleanup, historical dates and mode restoration; inert hooks, no instrument runtime |
| [`tests/test_event_frames.py`](../tests/test_event_frames.py) | Maximum-length Unicode transmission context survives patch notification under the unchanged UTF-8 byte cap; actual oversized frames are refused without publishing an event |
| [`tests/test_operation_exports.py`](../tests/test_operation_exports.py) | Terminal JSON/MAT persistence, canonical/view agreement, mixed grids, missing/zero/partial outcomes, metadata and Unicode, explicit format and exclusive-save recovery; original canonical identities across inherited chart history; invalidated-timing accounting without borrowed readings; typed deep-JSON/native-fact refusals that preserve the canonical source; retry/relocation decisions and CLI reporting. MAT requires its optional extra. |
| [`tests/test_spectral_exports.py`](../tests/test_spectral_exports.py) | M1/M2/M3 SPECT quantity/grid/condition limits, signed values, partial-row order, canonical companion/hash and save recovery; M0/unrepresentable projection refusal |
| [`tests/test_ti3_exports.py`](../tests/test_ti3_exports.py) | Retained-data TI3 quantities, units, material grouping, original identifiers and chart selections; ordered current ordinary selection separate from inherited chart history; complete retained-record validation even when unselected; typed retry/relocation decisions and deep identity/target refusals; retained cleartext-header fixture independent of Git history; owned first-save/update/retry, last-good/retirement and file/lock safety; inert pinned CGATS/spectral reader boundary proofs. No acquisition or hardware. |
| [`tests/test_nonadaptive_calibration.py`](../tests/test_nonadaptive_calibration.py) | Authenticated patch boundaries and source-extracted calibration-state plus shared emission buffer-ownership proof; acquisition checks remain separate |
| [`tests/test_calibration_repairs.py`](../tests/test_calibration_repairs.py) | Digest-bound DTP41 acknowledgement, DTP92 pending/needed calibration state, and Specbos averaging-error lock/state repairs; frozen predecessor/current extracted functions and actual-source mutants with inert commands/locks, not hardware or complete measurement execution |
| [`tests/test_calibration_string_reader.py`](../tests/test_calibration_string_reader.py) | Current bounded calibration-identifier reader and consumers; frozen predecessor failures, sanitized valid/refusal controls and actual C boundary mutants. |
| [`tests/test_native_mode_checks.py`](../tests/test_native_mode_checks.py) | Bounded query words cover request modes without certifying options; actual DTP41 capabilities/check functions reuse the initial cache even after a failed probe, with counted fake commands and no transport |
| [`tests/test_strip_layout.py`](../tests/test_strip_layout.py) | Compiles the pinned DTP20/41/51 formatters with the native operand policy; checks wire-count limits, real width formatting, required/unused operands, defaults versus omission, printable text, driver census, and the product request shape through frontend validation. Authenticated substituted sessions refuse invalid operands before calibration/reading and retain DTP51 feed-trigger guidance. No transport, target recognition or hardware proof |
| [`tests/test_mode_acceptance.py`](../tests/test_mode_acceptance.py) | Strict native baseline facts, exact geometry/output status, unknown versus rejected, descriptor identity and simulated capture consistency; acquisition does not treat a baseline as future readiness |
| [`tests/test_spyder_calibration_slots.py`](../tests/test_spyder_calibration_slots.py) | Pinned Spyder X2/2024 calibration-slot repair using the actual display tables, `set_disp_type` and `spydX2_getCalibration` with a scripted command reply: every advertised native index has a slot, OLED and Mini-LED are the original's only missing ones and are never executed there, each selection on both 2024 paths writes only its own slot and leaves the following state and a guard untouched, reselection keeps indices apart, the header diff is the slot count alone, and three regressed repairs are caught |
| [`tests/test_spyder_black_cache.py`](../tests/test_spyder_black_cache.py) | Actual Spyder X2 cache and read functions over inert cache and transport helpers: all six black offsets survive a simulated restart, old cache layouts are refused, save/restore regressions fail on actual-source mutants, and supplied source bytes including CRLF remain bound to the proof. No filesystem cache or hardware is exercised. |
| [`tests/test_munki_scan_margin.py`](../tests/test_munki_scan_margin.py) | Current zero-guarded margin predicate, finite-strip controls, caller refusal flags and curve lifetime; real C mutants and malformed-result rejection against frozen predecessors. |
| [`tests/test_spyder_selection_path.py`](../tests/test_spyder_selection_path.py) | Pinned Spyder 2024 selection-path repair using the actual `set_disp_type`, two public selection methods, two internal selection helpers and `spydX2_getCalibration` with a scripted command reply over the repaired seven-slot header: the original keeps the previous measurement path on cached reselection under a mixed support mask, past the display-number limit, under a CCMX list entry, under `spydX2_col_cor_mat` and after a failed load is retried; the repair leaves each selection its own path with the same command counts, the uniform high, forced-low and X2 controls and a refused CCMX are unchanged, only `set_disp_type` moves in the driver, and three regressed repairs are caught |
| [`tests/test_disptype_list_release.py`](../tests/test_disptype_list_release.py) | Pinned owned display-list release for `k10_del`, `spydX_del` and `spydX2_del` using the actual `inst_creat_disptype_list`, `inst_del_disptype_list`, display tables, selectors, getters and destructors over a tracked allocator and a scripted CCMX list: the original leaves every list it built allocated after delete, one block per list and eight with seven corrections, while the repaired destructors leave nothing with the same checks, commands and selection state; repeated requests allocate nothing, recreation frees the old list, the sanitized builds show no double free or use after free, only the three destructors change by one line each in the convention's place, and three regressed repairs are caught |
| [`tests/test_munki_nonadaptive_calibration.py`](../tests/test_munki_nonadaptive_calibration.py) | Scripted nonadaptive exposure/calibration outcomes, mode and validity state, warning/failure cleanup; current production C mutants and exact baseline failure census. |
| [`tests/test_xyz_only_conversion.py`](../tests/test_xyz_only_conversion.py) | Current three-family conversion functions and shared helpers; target/filter routing, full input/output preservation, scratch integration, ownership and source mutants. |
| [`tests/test_scan_finite.py`](../tests/test_scan_finite.py) | Current common strip recognizers, invalid averaging and caller refusal; independent C overflow-guard mutants, exact controls and authenticated archive extraction. |
| [`tests/test_i1pro3_wavelength_finite.py`](../tests/test_i1pro3_wavelength_finite.py) | Current scan/wavelength and consistency functions; 136 synthetic scan cases, six C guard mutants, exact source binding and malformed-result controls. |
| [`tests/test_scan_tolerance_option.py`](../tests/test_scan_tolerance_option.py) | Current option handlers, finite-ratio setter policies and exact nonfinite refusals; dropped-guard C mutant and wrong-error rejection. |
| [`tests/test_conversion_allocation.py`](../tests/test_conversion_allocation.py) | Current shared helpers and all declared caller references; allocation failure, independent converter lifetimes, source census and extra-caller refusals; current reflective calibration proof. |
| [`tests/test_sanitizers.py`](../tests/test_sanitizers.py) | Sanitizer runs of the source-bound proofs fail closed: every runner takes its flags and its refusal from one module, recovery is disabled, each diagnostic marker is refused, and a deliberate harness-only overflow in the ColorMunki margin recognizer and its separate predicate table, legacy scan, scan-normalization, display-list release, curve-release, white-buffer, display-list member-release and LED-slope runners is refused with an incomplete report rather than reported as a pass |
| [`tests/test_munki_curve_release.py`](../tests/test_munki_curve_release.py) | Current recognizer curve/heap ownership across successful, repeated, sparse and allocation-failed synthetic reads; source mutants, sanitizer and earlier-iteration controls. |
| [`tests/test_munki_white_buffer_release.py`](../tests/test_munki_white_buffer_release.py) | Current white-buffer ownership and matrix failures; failure-exit C mutants, retained state, repeated-read and sanitizer controls. |
| [`tests/test_disptype_list_member_release.py`](../tests/test_disptype_list_member_release.py) | Frozen constructor predecessor and current production constructor; member release, selector initialization, failure payloads and earlier-attempt/missing-suffix mutants. |
| [`tests/test_munki_ledtemp_slope.py`](../tests/test_munki_ledtemp_slope.py) | Current thermal-fit/compensation/read callers; finite per-band controls, NaN refusal and live-white validity; eleven named C mutants and malformed-result controls. |
| [`tests/test_proof_case_discovery.py`](../tests/test_proof_case_discovery.py) | Case discovery shared by the four probe-listing proof runners (display-list member release, ColorMunki curve release, LED-temperature slope, white-buffer release): a listing that exits unsuccessfully or names one case too many fails the proof as incomplete. Parametrized over the four runners; needs the pinned archive and a C compiler. |
| [`tests/test_munki_dark_copy.py`](../tests/test_munki_dark_copy.py) | Shared ColorMunki dark banks retain their exposure, selected modes keep their own calibration, and cold sharing compares hardware clock counts. Twelve inert state scenarios and five independent regressions; no transport or cache I/O. |
| [`tests/test_ss_xy_metadata.py`](../tests/test_ss_xy_metadata.py) | Pinned SpectroScan X-Y metadata/conversion and bounded retry repairs using the actual driver function and XRGA spectral math, fake commands and an explicit XYZ call spy; original failures and repaired cases, no table operation or XYZ accuracy claim |
| [`tests/test_ss_protocol.py`](../tests/test_ss_protocol.py) | Whole pinned SpectroScan/Spectrolino protocol unit over scripted serial replies: communication errors retain their codes/classes, four-byte words assemble unsigned, valid and malformed controls and negative zero remain checked; independent compiled regressions and receipt mutations, no instrument |
| [`tests/test_ieee754_special.py`](../tests/test_ieee754_special.py) | Pinned numsup `IEEE754todouble` and `IEEE754_64todouble` decode every all-ones-exponent word as the signed infinity or NaN it encodes; nine controls per width decode bit-for-bit as the native reinterpretation; the pristine results (finite single-precision values, an infinity for every 64-bit NaN word) are recorded; four compiled regressions, a strict C89 build without C99 special-value macros, a finite-`HUGE_VAL` directed-rounding probe, SpectroScan classification with inverted NaN signs, sanitizer canary and composition in every build scope. Both encoders re-encode every decoded word: specials give the canonical quiet NaN or signed infinity word and every finite control round-trips (`-0` encodes as `+0`), where the pristine log/floor path gave the smallest subnormal a normal exponent. A compiled UBSan case shows the pristine encoder's int cast is undefined for a decoded NaN or infinity (the i1Pro EEPROM checksum path), and a UBSan sweep round-trips every 4093rd single-precision word and a million 64-bit words. Five encoder regressions each fail on the word they get wrong. A bounded-`ldexp` probe round-trips sixteen signed subnormal/normal boundary words and requires scaling exponents no larger than 24/53, so raw-magnitude scaling cannot hide behind a permissive CRT. No physical caller, transport or device is exercised. |
| [`tests/test_ccss_cgats.py`](../tests/test_ccss_cgats.py) | The installed-CCSS reader (`read_ccss_cgats`, compiled pristine and repaired with its own CGATS, xspect and numsup sources) validates complete numeric keywords, integer counts of 2 to 601 bands, single-band refusal, positive ordered finite wavelengths within int range, a positive finite norm, sparse-grid interpolation bounds, and finite numeric columns read as their stored type. Crafted files go through `ccss.read_ccss`, with the installed-file search a stand-in that lists nothing. The pristine reader accepts zero or negative bands and a zero, negative or NaN norm; under the sanitizers it writes `spi[601]` out of bounds, overreads integer and string columns, and converts an out-of-range wavelength to int. Ten mutants each fail their own case, including suffixes, fractional counts, flat grids and nonfinite samples; the sanitizer canary is refused, and every build scope composes the repair once. Given the composed tree, the proof builds the repaired reader from that tree's support files, as the workers do. The composed CGATS parser then refuses an all-integer column holding 2147483648, which the same reader over pristine CGATS accepts as an incorrect native `int`; the overflowing conversion has no portable defined result. No installed file, directory or device is read. |
| [`tests/test_native_resync.py`](../tests/test_native_resync.py) | Frozen predecessor keys/digests and actual production-source behavior; direct C mutants, benign comment acceptance, cleanup and corrupted-binding refusal. |
| [`tests/test_native_resync_package.py`](../tests/test_native_resync_package.py) | Current imported C repair behavior with direct production-source mutants and broad preparation integrity; no reconstruction through retired transforms. |
| [`tests/test_filter_values.py`](../tests/test_filter_values.py) | Current filter validation and transactional conversion; valid three-sample linear fallback, nine C safety mutants, applied-zero sanitizer control and complete observation census. |
| [`tests/test_xcalstd_option.py`](../tests/test_xcalstd_option.py) | Current setter/getter options, accepted targets and exact refusal without state change; source C mutants and sanitized controls. |
| [`tests/test_flash_capture.py`](../tests/test_flash_capture.py) | Current flash extraction/caller behavior across 27 synthetic cases plus sibling endpoints/saturation; exact thresholds, cleanup, C mutants and result-validator controls. |
| [`tests/test_scan_normalization.py`](../tests/test_scan_normalization.py) | Exact calibration/flash/scan patch composition; zero/nonfinite recognition divisors, nonfinite quotients and position-slot overflow refused before recognition, invalid leader/trailer agreement, finite signed/subnormal controls and cleanup. Forty cases per variant comprise 31 extracted scan/recognizer cases and nine actual-predicate boundaries; source cases require the pinned archive and compiler, and do not execute a strip operation or hardware |
| [`tests/test_examples.py`](../tests/test_examples.py) | Every tracked example runs to completion as its own process, discovered by glob so a new one is covered without being listed |
| [`tests/test_documentation.py`](../tests/test_documentation.py) | This coverage map names exactly the test files that exist, in both directions; every flag written after a `tools/*.py` path, in the documents and the CI workflows, is one that tool actually accepts, and every substituted worker they name is one the builder writes; the counts the README and the frontend contract quote are the counts the code derives |
| [`tools/check_doc_snippets.py`](../tools/check_doc_snippets.py) | All marked documentation excerpts match current source; required public conversion excerpts exist. Repository-relative inline links and reference definitions name existing paths; Markdown fragments name ATX headings, excluding fenced code and HTML comments while preserving comment-like text in inline code. This is an offline check, not a complete Markdown renderer or an external-link availability check |

```sh
uv run --locked pytest tests/test_science.py
uv run --locked pytest tests/test_workflow.py
uv run --locked pytest tests/test_doc_snippets.py
uv run --locked pytest tests/test_test_all.py
uv run --locked pytest tests/test_instrument.py tests/test_instrument_frontend.py -k "sheet or strip"
```

The [science documentation](science.md) explains expected values and tolerances.
These are numerical tolerances, not acceptable instrument error or universal
perceptual thresholds. Signed spectra in record tests are synthetic data used
to check preservation. Every acquisition check in this suite runs against a
substituted worker rather than an instrument, and project spectral integration
is not implemented.

The workflow tests also check one-shot acknowledgment decisions and reject
JSON `null` fields replaced by empty arrays. The i1Pro3 tests verify that an
instruction reaches the host before its Python callback runs, and that missing,
wrong, consumed or stale acknowledgment IDs cannot reach a measurement result.
These portable regressions do not launch MATLAB.

The [equipment demos](equipment-demo.md) exercise the shared frontend without
instrument enumeration or a native worker. The polled-operation checks cover
instruction responses, cancellation, ownership and retained finalization using
substituted I/O. These Python checks do not establish MATLAB UI responsiveness,
physical cancellation latency or device readiness.

## MATLAB and other clients

MATLAB and other clients bind `InstrumentFrontend` and its shared polled
operation API. Hardware-free clients obtain those frontends from
`demo_catalog_json()` and `demo_frontends()` in `color_measurement.demo`.
The [generic frontend guide](generic-frontend.md) defines requests and descriptors;
[frontend operations](frontend.md) explains polling, retained results and saving.

Python tests check this producer contract without MATLAB installed. A client
checks its own binding and UI behavior in its own repository. No MATLAB client
code or MATLAB execution is part of these Python checks.

## Optional static Argyll documentation inventory

[`tools/argyll_inventory.py`](../tools/argyll_inventory.py) reads an explicitly
supplied, extracted ArgyllCMS 3.5.0 source tree and writes a JSON inventory of
command manuals, synopsis syntax, minor-tool entries and Windows helpers. It
does not download files, import upstream code, build or invoke Argyll, or access
instruments. Obtain source separately from the
[official source page](https://www.argyllcms.com/downloadsrc.html) and preserve
its identity and upstream terms.

Replace these example paths with an existing extracted source directory and
an output file whose parent directory exists:

```sh
uv run --locked python tools/argyll_inventory.py --source-root /path/to/Argyll_V3.5.0 --output /path/to/inventory.json
uv run --locked python tools/argyll_inventory.py --source-root /path/to/Argyll_V3.5.0 --check /path/to/inventory.json
```

`--output` writes or replaces the named JSON file. `--check` regenerates the
inventory in memory and compares its bytes with the existing file without
writing it. A mismatch, missing required input or invalid source structure
returns status 1; a match or successful write returns 0. A successful exit can
still report unparsed usage rows: those remain explicit diagnostics requiring
manual review, not a claim that all syntax or option semantics were understood.

The output includes document/source hashes, original HTML line references,
catalog membership and build-file references. Its archive digest is labelled
**expected**; the tool does not hash a ZIP or establish that the extracted tree
matches that archive. Source presence and build declarations likewise do not
prove installation, enabled build conditions or runtime device support.
The full test command includes the extractor's synthetic tests; it does not
require or automatically inspect an upstream source checkout.

## Optional native-stub integration pytest

With `--argyll-archive`, the native stage builds both substituted workers from
this checkout before the pytest stage, which uses any worker not supplied. To
exercise the polled integration cases without that stage, supply trusted,
previously built workers from the
[native checker](argyll-native-feasibility.md#inputs-and-invocation):

```sh
uv run --locked --extra mat python tools/test_all.py --require-mat \
    --generic-stub /absolute/path/generic-session-stub-worker \
    --chart-stub   /absolute/path/generic-chart-stub-worker
```

Both executables link the one `session.c`. `colorimeter_stub.c` supplies the
colorimeter persona with no spectrum and, in the composed build, the production
CR and PR drivers over scripted serial transports. `chart_stub.c` carries the
table, strip and stored-chart personas and links `driver_personas.c`, one row for
every other constructible Argyll type.

The description worker reuses the chart worker's i1Pro3 vtable with the same
USB type and serial identity. This exercises the description-to-binding contract;
it does not emulate USB replies or execute the real `i1pro3.c` driver. The real
CR-250, PR-655 and PR-670 driver checks also queue user cancellation immediately
after the host's read-deadline grant. Their armed callbacks refuse before M/M5,
publish no reading, and release the scripted connection. Ordinary placed CR/PR
spot reads require no operator instruction; the checks refuse invented prompts.

These workers use substituted OS transports. Each executable must be compatible
with the current machine. The runner
resolves the supplied path and verifies its dedicated name (or `.exe` on Windows),
executable digest, pinned archive and current native sources against its retained
build receipt. Direct connected pytest cases use the same verification, including
the description worker used for discovery. Use a trusted local build and its
provenance; a matching basename alone does not establish either. The runner
does not discover and execute binaries from previous build directories
automatically, and nothing named `*-never-run` is ever executed.

`COLOR_MEASUREMENT_GENERIC_STUB` and `COLOR_MEASUREMENT_CHART_STUB` supply the
same paths when the arguments are absent; the CLI arguments take precedence. The
resolved paths are passed to the pytest child. A worker the native stage built is
verified against its receipt in the same way before pytest receives it. Each
`EXERCISED` summary requires that the named integration test actually ran
successfully. A missing integration case, an unreadable coverage report, or an
explicitly requested case that skipped fails the gate. These options add no stage
and build no worker.

Without them, those cases skip and the summary says `UNAVAILABLE` rather than
passing quietly. Supplying workers can produce a five-stage pass with both
coverages `EXERCISED`. Only `--argyll-archive` adds the sixth stage, which builds
and checks native code and runs first. Stage count and exercised coverage are independent;
inspect both summaries when evaluating a contract change.

The optional native build below independently exercises the same polled bridge
with its newly built substituted workers. Its separate bridge summary does not
turn an earlier skipped pytest case into an executed test.

## Export interoperability checks

The suite checks writer/parser contracts and preservation of quantities, grids,
metadata, original bytes and save-recovery behavior. External consumers need
their own version-bound interoperability check. See the [format guide](data-format.md)
for accepted inputs, omissions and companion requirements. Missing measurements
or conditions are never inferred. Software/file interoperability is separate
from instrument operation.

## Optional native Argyll feasibility check

The [native guide](argyll-native-feasibility.md#inputs-and-invocation) documents
compiler/platform inputs, source preparation, substituted workers, proof scope
and output ownership. Select the complete combined application form
[above](#one-command) for native plus Python/package integration.

The native stage runs first; its authenticated substituted workers supply the
later pytest stage. Real production workers are built and linked without being
executed. Original i1Pro, i1Pro3 and all other linked families share the broad
recipe. Retired narrow build switches are refused.

## Tracked examples

The tracked examples are software-only. Each is discovered and run as a separate
process by [`tests/test_examples.py`](../tests/test_examples.py):

```sh
uv run --locked python examples/generic_frontend.py
uv run --locked python examples/simulated_reading.py
uv run --locked python examples/refresh_frequency_demo.py
```

`generic_frontend.py` selects the simulated i1Pro3, requests three strip patches,
polls the shared events and retains a completed outcome, without an instrument
or native worker. `simulated_reading.py` runs the tristimulus simulator through to
canonical JSON. `refresh_frequency_demo.py` retains a simulated scalar frequency
with external-stimulus context and optionally saves JSON or MAT to a new path.
Automatic instruction acceptance in these examples applies only to their
synthetic workflows. A physical client must obtain the operator's current decision.

The following CLI checks exercise the separate tristimulus simulator. They are
useful for checking terminal behavior and inspecting the files yourself, and
complement the automated suite. Run from the checkout in a terminal, using new
filenames in an existing writable directory.

1. List the available source:

   ```sh
   uv run --locked color-measurement devices
   ```

   Expect simulator capabilities (`relative_xyz`, XYZ, no spectrum or calibration
   actions) plus a static `instruments` name table with contribution markers.
   The table is not a discovery result or a native-build support check.
   No device discovery or connection should occur.

2. Acquire synthetic XYZ and compute Lab with an explicit synthetic white:

   ```sh
   uv run --locked color-measurement acquire --device simulator --sample-id manual-check --white 80 100 120 --output manual-reading.json
   ```

   Expect exit 0, simulation notices on stderr, complete JSON on stdout and a
   new file. Inspect `provenance="simulated"`, native XYZ `(20,25,10)`, null
   spectrum, `not_applicable` calibration and the supplied Lab white. Observer
   and illuminant remain unspecified; this white is not D50/D65.

3. Export the same reading in both directions:

   ```sh
   uv run --locked color-measurement export manual-reading.json --output manual-reading.csv --format csv
   uv run --locked color-measurement export manual-reading.csv --input-format csv --output manual-roundtrip.json
   ```

   Expect exit 0 and the same observation/operation IDs, native XYZ, white and
   calculation status. CSV's `record_json` preserves the complete record. This
   should neither recalculate the reading nor start acquisition.

4. Exercise save refusal and recovery using the already saved reading:

   ```sh
   uv run --locked color-measurement export manual-reading.json --output manual-reading.json
   uv run --locked color-measurement export manual-reading.json --output manual-retry.json
   ```

   The first command must exit 2, leave the original file unchanged and emit the
   complete record to stdout with a save-failure diagnostic. The second must
   succeed with the same reading. Run it as a separate command after inspecting
   the expected failure. No measurement is requested by either command.

5. Cancel a delayed simulation:

   ```sh
   uv run --locked color-measurement acquire --device simulator --delay 10
   ```

   Press Ctrl-C during the delay. Expect a cancellation message, status 130 and
   no completed JSON observation. This tests software in the current terminal,
   not whether any instrument stopped or is ready.

6. Refuse invalid input:

   ```sh
   uv run --locked color-measurement acquire --device simulator --white 0 100 120
   ```

   Expect status 1 and a reference-white error before acquisition, with no JSON
   observation. Argument syntax errors can instead return status 2; read the
   diagnostic to distinguish them from an output-file failure.

The manual files are deliberately retained for inspection. Keep useful ones or
remove only the files you created for this check. See [usage](usage.md) for
stdout recovery of a newly acquired reading and [formats](data-format.md) for
the serialization contract.

## Physical testing is separate

No software-only command establishes real instrument operation or optical
accuracy. Follow [testing with an instrument](testing-with-an-instrument.md) for
the device effects, prerequisites, operator actions, guided bench session and
privacy-aware result report. Connecting or reading a device requires explicit
operation scope. No test-suite failure requires a physical exposure.

## Retained worker identity

Fixture `--check` compares regenerated observations without rewriting the
published captures. Outside the existing shape-checked per-run fields, JSON
keys, list lengths, integer/boolean types, exact zeros and their signs, ordinary
strings and capability identities remain exact. Wavelength grids, normalization,
scale, reference whites and other domain metadata remain exact too.

Declared XYZ, spectral-sample and computed quantity arrays permit bounded native
floating-point roundoff. Both values must be finite, nonzero floats of the same
sign, with `abs(a - b) <= 4 * max(ulp(a), ulp(b))` in binary64. The actual shared
comparison helper is:

<!-- source: tools/generate_frontend_fixtures.py:_float_roundoff_matches -->
```python
def _float_roundoff_matches(before, after) -> bool:
    return (type(before) is float and type(after) is float
            and math.isfinite(before) and math.isfinite(after)
            and before != 0 and after != 0
            and math.copysign(1, before) == math.copysign(1, after)
            and abs(before - after) <= 4 * max(math.ulp(before), math.ulp(after)))
```

This uses the ULP at the larger magnitude; it is a software-reproduction
allowance, not an instrument-accuracy or uncertainty limit. The observed native
XYZ Z values `0.09214833994606052` and `0.09214833994606055` fit that bound. A changed
wavelength coordinate does not, even if it differs by only one ULP.

Three derived unit-check diagnostics have additional, source-specific rules:

- CR and PR `unit_check.y_ratio` may use the same four-ULP comparison only with
  valid computation and discrimination context. Whether the ratio is below
  `0.9`, within the inclusive interval `[0.9, 1.1]`, or above `1.1` must remain
  unchanged. See the actual [CR](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250.c)
  and [PR](../tools/native_argyll/contributions/cr_pr/files/spectro/pr655.c)
  ratio checks.
- A changed CR `unit_check.chroma_error` must reproduce each capture's own
  retained inputs using the [driver's calculation](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250.c).
  For native XYZ `n` and integrated XYZ `d` on the same scale, its dimensionless
  residual is

  $$
  r = \frac{\max(|d_X/4-n_X/4|,\ |d_Z/4-n_Z/4|)}
                 {n_X/4+n_Y/4+n_Z/4}.
  $$

  The diagnostic validator `_cr_chroma_reproduces` quarters the terms and
  evaluates the native total from left to right, as the C code does. Each stored
  residual must match its own recomputation within four result ULPs; the paired
  XYZ arrays must separately satisfy their ordinary four-ULP bound. This is not a pairwise ULP allowance
  on the two residuals. It requires finite triples, native luminance Y of at
  least `0.2 cd/m²`, positive total and valid luminance/chromaticity-check
  context. Missing or malformed context earns no tolerance. Whether the
  residual is at most `0.05` must remain unchanged, even when the overall
  verdict is unchanged.
- CR `unit_check.spectrum_mean_nm`, the sample-weighted mean wavelength
  `sum(sample * wavelength_nm) / sum(sample)`, may use the ordinary four-result-ULP
  bound only with a complete computed CR context, positive finite spectrum power
  and a valid strictly increasing native grid. When the axis comparison is
  discriminating, whether `abs(spectrum_mean_nm - mean_nm) <= 5.0` nm must remain
  unchanged. The native `mean_nm`, grid and status fields stay exact. This is the
  [driver's existing axis refusal](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250.c),
  not a wavelength-accuracy specification.

For example, actual synthetic CR captures have residuals
`1.1522174767313637e-08` and `1.1522174747012416e-08` when their retained integrated
Z differs by one ULP. Both reproduce their own inputs; comparing only the two
near-zero residuals in ULPs would reject this case. Exact zero/sign/type rules,
verdicts and computation/discrimination fields still apply. These driver refusal
thresholds and reproduction checks do not establish physical accuracy. The
observed CR mean-wavelength pair `538.6667581846416` and `538.6667581846415` nm
fits the separate scalar bound without changing the axis decision.

A further exception applies only to complete, explicitly simulated emitted
spectra. The [C stub's authoritative table and calculation](../tools/native_argyll/chart_stub.c)
define twelve illustrative profiles with centres `c` and widths `w` in nm:

$$
z_j(\lambda)=-\tfrac12\left(\frac{\lambda-c_j}{w_j}\right)^2,
\qquad v(\lambda)=1000\sum_j A_j e^{z_j(\lambda)}.
$$

The coefficients `A` are illustrative source markers, not measured sources or
standard illuminants. The comparator reads this table from the actual C source;
it retains no second table. Both entire vectors must fit the same profile on
the same exact uniform integer-nm grid, with 2–601 samples. The supported declared
pairs are `spectral_radiance` with `mW/(m^2 sr nm)` and `spectral_irradiance` with
`mW/(m^2 nm)`. Raw emitted packets must have the corresponding native measurement
type 1 or 2 and norm 1. Sharing a numerical curve does not convert radiance to
irradiance or establish angular response. Reflectance/transmittance, missing or
physical provenance, unsupported units and changed normalization receive no
additional allowance.

The validator takes four representable steps on each side of each exponent and
four steps on each side of its exponential result. It then rounds the positive
component products, source-order sums and final factor-of-1000 scaling outward.
The actual interval helper is:

<!-- source: tools/generate_frontend_fixtures.py:_four_ulp_interval -->
```python
def _four_ulp_interval(value: float) -> tuple[float, float]:
    lower = upper = value
    for _ in range(4):
        lower, upper = math.nextafter(lower, -math.inf), math.nextafter(upper, math.inf)
    return lower, upper
```

This is an explicit synthetic-software reproduction budget, not a universal
C-library error guarantee. It keeps the ordinary exact grid, numeric-type,
zero/sign, scale and metadata rules. For example, a single exponent ULP produces
emitted synthetic values `5.865411586436856e-19` and `5.865411586436814e-19`,
43 result ULPs apart; both fit the source-conditioned interval. A bin outside
every shared profile's interval fails; profiles cannot be chosen independently
by bin.
The default four-result-ULP rule still applies outside this exception.

The [comparison implementation](../tools/generate_frontend_fixtures.py) decodes
declared embedded JSON fields and configuration JSON pairs with the same rules;
duplicate keys and non-JSON numeric constants are refused. JSON-looking ordinary
strings remain exact text. The publisher uses its existing separate policy:
new numerical values are written as actually captured, without this tolerance
or rounding; a capture differing only in exempt per-run fields can retain its
original bytes when its authenticated build identity is unchanged.

Descriptor-only Python changes can be projected from retained native connection
facts with `tools/generate_frontend_fixtures.py --build <authenticated-build>
--refresh-descriptors`. Adding `--check` verifies that projection without starting
a worker. Missing native witnesses or mismatched descriptor/index identities
refuse; capability words are never reconstructed from a derived DTO.

A changed capability identity cannot make an old retained request satisfy a new
reserved request. When those current request/record invariants change, use
`--refresh-current-captures` with the already authenticated substituted build.
This runs the affected frontend captures through the existing generator and
shared operation owner, while retaining unchanged discovery envelopes and
native calibration-event-only inputs. It performs no native rebuild or physical
operation. Old records remain evidence of their original inputs through Git and
the retained gate receipts. Regenerate packaged Demo data through its existing
generator after changed source captures, then run its `--check`.
Use `--personas cr100 cr250` with `--refresh-current-captures` when only those
personas changed. Other persona files and index entries are retained. An output
that differs only in the existing per-run fields keeps its original bytes;
obsolete selected-persona outputs are removed after successful generation.
`--native-discovery <case>...` refreshes selected existing native discovery cases
through the authenticated description stub, preserving unrelated cases and
equivalent original envelopes. It can accompany a persona refresh or run alone.
Successful current discovery descriptions must all replay through actual binding;
missing identity availability cannot turn that check into a skip.
Every current capture retains its actual prepared descriptor. Missing descriptor
wrappers are not accepted or migrated. Variant descriptors remain their own
captured inputs. Descriptor projection and complete capture generation both
require the composed CR/PR series before worker authentication or fixture changes.
The current-record checks validate the declared request, descriptor and original
record together without replacing full capture reproduction.

The full/focused runners and fixture generator require a passing `report.json`
next to supplied substituted workers, with matching executable SHA-256 and the
pinned archive identity. Its `original_check_source_sha256` manifest must include
every current top-level non-hidden file under `tools/native_argyll` and the
builder `tools/argyll_native_check.py`, with matching hashes. A selected repository
contribution additionally binds its manifest and all files under that series;
missing, added, changed or removed inputs require a rebuild. Unselected series
are outside that build's currency check. This is a conservative build/check-input
policy, not a per-executable dependency closure: the substitutes do not link
patched upstream driver implementations.

Empty or malformed manifests cannot establish currency. Historical reports remain
evidence of their original runs, not current acceptance. Use a trusted local
build directory. Matching builder/source hashes cannot prove which program wrote
a receipt or that compilation occurred: an editable receipt is not a signature.
A renamed real helper or changed binary is refused against an unchanged trusted
receipt; arbitrary binaries accompanied by fabricated receipts are not safe.
The real `*-never-run` workers remain build/link only.
