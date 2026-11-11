# Color Measurement

A color-measurement app for collecting readings, comparing spectra and colors,
and keeping results for further analysis. The desktop app has a web-based interface
and runs offline, without MATLAB.

**Beta: testers wanted.** Help us check setup, everyday use, saved results and
instrument behavior. You can start with Demo without owning an instrument.
Physical instrument operation and measurement accuracy have not been validated;
use real equipment only after reading the [instrument guide](docs/testing-with-an-instrument.md).

![Demo workspace with a synthetic spectrum, chromaticity plot and saved reading](docs/images/portable-demo-workspace.png)

*The portable workspace on macOS with a simulated Demo reading. Preview colors are illustrative.*

## What you can do

- **Collect and organize readings.** Name samples, add notes and save them in batches.
- **See spectra and colors together.** Inspect linked plots, chromaticity and a reading table.
- **Compare samples.** Review XYZ, Lab, color differences and spectral differences with explicit reference conditions.
- **Measure guided charts.** Track patch positions, repeats and missing readings, then continue a saved chart in a new batch.
- **Review and export.** Reopen saved results and export JSON, CSV or MAT; other formats depend on the available quantities.
- **Recover from save errors.** Retry saving or choose another folder using the retained readings.
- **Use your own scripts.** Python and command-line interfaces are available alongside the desktop workspace.

## Try it

- **Apple silicon Mac, macOS 27 or later:** download the
  [macOS beta](https://github.com/ferazambuja/color-measurement-app/releases/tag/v0.1.0-beta.1).
  Follow [getting started](docs/getting-started.md) to verify the download and open it.
  This early beta is ad-hoc signed and is not notarized; macOS may require a
  one-time approval for this app.
- **Build and report:** Windows and Linux now have the same desktop workspace
  in source, alongside the CLI and Python API. These desktop targets are experimental;
  no Windows/Linux binary is published. Other Macs can attempt the source route; compatibility with Intel Macs or older
  macOS is not confirmed. See [platforms and reporting](docs/platforms.md).
- **Contribute:** use the [setup checklist and contribution workflow](CONTRIBUTING.md).

The [user guide](docs/user-guide.md) covers the first Demo reading, workspace
controls, saved data, charts, comparisons and common problems.

Try the command-line simulator from this checkout with
[uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.11–3.14:

```sh
uv sync --locked
uv run --locked color-measurement acquire --device simulator --output demo-reading.json
uv run --locked color-measurement export demo-reading.json --output demo-reading.csv --format csv
```

Choose unused filenames. The simulator creates labelled synthetic XYZ without a
spectrum; these commands do not discover or open an instrument. `devices` is a
name table, not discovery. Node.js is not needed to run the application or CLI.

## Help test the beta

Start with Demo: create a batch, take a few simulated readings, finish it and
reopen the saved folder. Tell us which instructions were unclear, whether the
plots and controls worked, and whether your saved results reopened correctly.
Setup failures and confusing behavior are useful reports too.

Use the [feedback checklist](docs/user-guide.md#send-beta-feedback) and choose an
[issue template](https://github.com/ferazambuja/color-measurement-app/issues/new/choose)
for a software bug, platform report or instrument test. Remove personal paths, names, notes, serial numbers and
confidential measurements from any screenshots or files you share.

### Test with an instrument

Read [testing with an instrument](docs/testing-with-an-instrument.md) before
connecting. It explains device effects, settings that may persist, cancellation,
clean close and privacy-safe reports. Use equipment only within your authorized
scope. Demo and software tests do not qualify physical operation.

The app uses ArgyllCMS instrument drivers, including additional CR-100/250/300 and
PR-655/670 support. A listed model is not a guarantee that its physical modes work.
Windows/Linux desktop packaging is under test; command-line and Python use remain available.
Direct serial CR/CS routes have their own documented settings and limitations.
See the [support and platform distinctions](docs/platforms.md),
[Argyll frontend](docs/generic-frontend.md) and [direct serial reference](docs/direct-serial.md).

## Documentation

Start with [getting started](docs/getting-started.md), the [user guide](docs/user-guide.md)
and [platform support](docs/platforms.md). The [documentation index](docs/README.md)
links to command-line help, data formats, scientific methods and contributor references.
Original readings stay separate from calculated values. Missing XYZ or spectra
remain unavailable, and display previews do not establish accuracy or calibration.

## Licenses and reporting

Original application code is [AGPL-3.0-only](LICENSE). Imported Argyll and driver
source retains its own GPL-2.0-or-later/MIT terms; Three.js and CIE resources have
separate licenses and attribution in [LICENSES](LICENSES). Standalone packages
also include Qt/PySide and Python/numerical runtimes with their own
[notices and source requirements](tools/desktop_runtime_licenses/NOTICE.md). Source distributions
and packaged runtimes have different component inventories. Retain their notices.

Report this application's problems in
[this repository](https://github.com/ferazambuja/color-measurement-app/issues).
That includes the bundled CR/PR drivers and Argyll repairs; do not report them to
ArgyllCMS upstream. Read [security reporting](SECURITY.md) before posting sensitive
information. Neither the included driver contributions nor an application release
implies ArgyllCMS endorsement or upstream acceptance.
