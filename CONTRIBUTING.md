# Contributing

Experimental instrument software: tests do not establish physical accuracy or
upstream acceptance. Report application and bundled-driver problems here, not
to ArgyllCMS upstream.

## Setup and check

Use Python 3.11–3.14, [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Node.js 22, 24 or 26:

```sh
uv sync --locked --extra app --extra mat
npm ci --prefix tools/desktop_builder --ignore-scripts --no-audit --no-fund
uv run --locked python tools/test_scoped.py --list
uv run --locked python tools/test_scoped.py science
```

Use the [component profiles](docs/testing.md#focused-component-checks) for affected
callers. The [platform report](docs/platforms.md#send-a-software-report) gives the
single full-input command, native prerequisites and honest skip interpretation.
A focused pass is not full acceptance. The default command without an archive
or substituted workers is partial. Tests never require an instrument or execute
production workers; use simulation, inert probes and verified substituted workers.
Physical operation is a separate authorized activity.

## Where code lives

| Owner | Files |
| --- | --- |
| Shared state, acquisition, science, persistence and formats | `src/color_measurement` |
| Edited workspace renderer | `tools/desktop_builder/renderer` |
| Generated workspace/reference assets | `src/color_measurement/desktop_assets` |
| Argyll workers, application policies and source-bound checks | `tools/native_argyll` |
| Authenticated generated imports | `tools/native_argyll/contributions`; never hand-edit |
| Regression and reader references | `tests`, `examples`, `docs` |

Renderer changes require regeneration with `tools/generate_desktop_assets.py`.
The current compiler receipt authenticates `@esbuild/darwin-arm64` only. On other
hosts, submit renderer source and report the stale-assets refusal; a maintainer
regenerates the exact source on Apple silicon in the review branch, reviews the
source/generated diff and runs the final checks. Do not bypass the manifest.
Python reference generation (`--kind reference`) has no esbuild platform restriction.
See [desktop build instructions](docs/desktop.md#build-a-standalone-application).

## Driver and application source

Argyll-facing repairs, CR/PR protocol engines, drivers and ordinary Argyll build
registration are driver source. This repository carries it as imported files and
patches under `tools/native_argyll/contributions`, complete for building; the
driver history they were imported from is not published with this release.
Application serialization, process supervision, passive discovery, stricter
admission and UI policy are application source. Do not create per-family
acquisition/frontend stacks.

Propose a driver change in an issue, or in a pull request against the imported
files. Their manifests authenticate them, so a maintainer applies an accepted
change to the driver source and re-imports a reviewed commit with
`tools/native_argyll/import_contribution.py`, its committed recipe and official
archive. See [regenerating a contribution](docs/argyll-native-feasibility.md#regenerating-a-contribution).
`--check` re-derives exact generated payloads. Both selected source commit and
tree are pinned; a moving branch tip is not a consumer lock. Commit recipes,
generated imports and affected policies/tests together. Do not apply a portable
C repair again as an application transform. Upstream submission is a distinct
maintainer decision, following Argyll's requested style, rights and review scope.

## Propose a change

Fork the hosting repository, create a focused branch and open a pull request.
Describe the concrete problem, resulting behavior, source/version and commands,
failures/skips and limits. Use a concise intent-based commit message. Include
generated assets when changed, or identify the required maintainer regeneration.
Keep source, tests and current reference docs together. Do not copy private
records, manuals, credentials or development narratives into a contribution.
See [CI scope](docs/platforms.md#continuous-integration); a workflow definition
or another platform's pass does not qualify your target.

Scientific implementations include equations, primary citations and checked real
source excerpts, units/reference conventions, an example, independent numerical
controls, justified tolerances, invalid-input behavior and limitations. Original
implementations retain attribution for established definitions. Translated
external code is attributed, not relabelled original.

Extract exact Python excerpts with:

```sh
uv run --locked python tools/check_doc_snippets.py --extract src/color_measurement/science.py:xyz_to_lab
uv run --locked python tools/check_doc_snippets.py --check docs
```

The checker parses rather than executes source and validates relative paths,
case and Markdown anchors. Keep every test module represented in the coverage
map. Preserve component licenses/notices and source rights; this project does
not impose a new CLA/DCO or grant upstream incorporation rights on your behalf.
