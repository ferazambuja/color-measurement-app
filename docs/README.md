# Documentation

New to the app? Follow [getting started](getting-started.md), then the
[user guide](user-guide.md). This is a beta looking for testers; Demo lets you
try it without equipment. Physical instrument operation and accuracy have not
been validated.

## User guides

- [Getting started](getting-started.md): installation, first launch and the source route.
- [User guide](user-guide.md): your first Demo reading, workspace controls, charts, comparisons, saving and common problems.
- [Beta feedback](user-guide.md#send-beta-feedback): what to try and how to send a useful, privacy-safe report.
- [Testing with an instrument](testing-with-an-instrument.md): preparation, device effects, a short session and reporting.
- [Platforms and reports](platforms.md): available application routes and optional build reports from other platforms.
- [Command-line usage](usage.md): acquisition, conversion, cancellation and save recovery.

## Detailed reference

These pages explain particular controls, file contents and calculations. You do
not need to read the API or build references to try the app or report a bug.

- [Desktop application](desktop.md): workspace controls, settings, data review and advanced workflows.
- [Data formats](data-format.md): original readings, batch files, JSON/MAT/CSV, target import and other exports.
- [Retained-data analysis](analysis-workspace.md): live and saved-data inspection, calculations and export choices.

## Python and application APIs

- [Batch application](application.md): durable admission, drafts, generic workflows, guided charts, annotations, cancellation, diagnostic artifacts and publication.
- [Desktop session](desktop-session.md): selected sources, live/saved data, settings and session lifecycle.
- [Equipment setup](desktop-equipment.md): instrument software selection, deliberate listing, initialization and reconnecting.
- [Synthetic equipment demos](equipment-demo.md): simulated instrument clients for scripts and application tests.
- [Frontend operations](frontend.md): polled Demo and instrument operations, instructions, cancellation, typed results and saving.
- [Generic instrument frontend](generic-frontend.md): descriptor modes/options/layouts, requests, native failure stages and opt-in diagnostic retention.
- [Chart state](chart-state.md): canonical cells, passes, attempts, selected readings and explicit decisions.
- [Portable chart sources](chart-sources.md): keeping original results when continuing a saved chart.

## Scientific methods

These pages give equations, original source excerpts, primary citations, examples
and limits. Native quantities remain separate from derived results.

- [Science](science.md): coordinates, CIELAB, color differences, Bradford adaptation, spectral XYZ, reflectance conventions and backend consistency checks.
- [Illumination and comparison](illumination.md): authenticated illuminant whites, signed color differences and explicit spectral model assumptions.
- [Chart context](chart-context.md): requested, acknowledged and readback facts, source identity and scientific qualification.
- [Chart colors and placement advisories](chart-science.md): common-white illustration, measured-reference qualification and declared limits; an advisory does not verify physical placement.
- [Display illustrations](illustrations.md): sRGB/Display P3 encoding, signed channels, clipping, reference geometry and display-model limits.

## Instrument reference

- [Generic instrument API](instrument.md): common Argyll acquisition, supported workflow definitions, corrections, serial selection and returned quantities.
- [Native catalog and description](argyll-description.md): catalog versus attachment, capability queries, initialization policies and source refusals.
- [Native CR and PR controls](argyll-native-controls.md): model-specific settings, returned facts and recovery limits.
- [i1Pro family](i1pro-family.md): generation-specific modes, accessories, calibration, quantities and manufacturer-versus-Argyll support.
- [i1Display family](i1display-family.md): model mapping, modes, calibration, corrections and XYZ-only results.

## Contributing and building

- [Contributing](../CONTRIBUTING.md): setup, where code lives and proposing changes.
- [Building the macOS app](desktop.md#build-a-standalone-application): standalone packaging requirements and commands.
- [Argyll integration](argyll-native-feasibility.md): build checks and maintaining the included driver changes.
- [Testing](testing.md): component checks and running the software suite without instruments.

The [architecture guide](architecture.md) explains how the application, interface
and instrument code fit together.

All numerical examples are synthetic. Software agreement does not establish
optical accuracy, a calibrated instrument or a complete appearance prediction.
