# Getting started

This is a beta looking for testers. Start with Demo before connecting equipment.
No binary release is published at present; the source route below is available now.

## Standalone macOS test release

The planned prebuilt target is Apple silicon on macOS 27 or later. It includes
Python, the workspace, numerical libraries and authenticated instrument workers.
It does not need a development checkout for ordinary use.

When a release exists, obtain its app ZIP and checksum from that release's
approved assets. Verify the downloaded bytes before extraction:

```sh
shasum -a 256 Color-Measurement-0.1.0-macos-arm64.zip
```

Compare the entire result with the published SHA-256 from a trusted release
page; the release's `SHA256SUMS` lists the value for each asset. A checksum
detects changed bytes relative to that value; it does not identify a publisher.
Extract the ZIP and put the app in a suitable local folder.

The bounded test release is ad-hoc signed, not Developer ID signed or notarized.
If macOS blocks launch, first try opening the app, then use System Settings >
Privacy & Security > Open Anyway and confirm the identified app. See
[Apple's instructions](https://support.apple.com/en-us/102445). Stop if its origin
or checksum is uncertain. Do not disable Gatekeeper, remove quarantine recursively
or run the app with `sudo`. Notarization is required before wider distribution.

Choose **Try Demo**, select the Demo instrument and a mode, then **Create batch**
in a new folder. Acquire a simulated reading,
finish/save the batch and use **Open saved batch…** to review it. Demo is
labelled simulated; it does not operate equipment. The [user guide](user-guide.md)
explains the workspace, charts, comparisons, saved data and
[beta feedback](user-guide.md#send-beta-feedback). Then read
[the instrument guide](testing-with-an-instrument.md) before using real hardware.
A failure loading bundled software is a reportable release problem, not a reason
to bypass authentication.

## Install a source checkout

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and select
Python 3.11–3.14. From the checkout:

```sh
uv sync --locked
uv run --locked color-measurement --help
uv run --locked color-measurement devices
uv run --locked color-measurement acquire --device simulator --output demo-reading.json
uv run --locked color-measurement export demo-reading.json --output demo-reading.csv --format csv
```

Use new output filenames. `devices` lists accepted names without enumeration.
The simulator produces XYZ but no spectrum. JSON goes to stdout and progress to
stderr, so retained output remains available when saving fails.

For the desktop workspace, install its optional dependencies and launch:

```sh
uv sync --locked --extra app
uv run --locked --extra app color-measurement-app
```

The same Qt host is implemented for macOS, Windows and Linux. Windows/Linux
packaging is under test, and no binary download is published. Check the
[platform requirements](platforms.md#desktop-runtime-requirements) first.
Node.js is needed for software tests
and renderer regeneration, not normal runtime.

## Build retained instrument software

Skip this for Demo or a standalone app with its bundled software. Obtain
[ArgyllCMS 3.5.0 source](https://www.argyllcms.com/Argyll_V3.5.0_src.zip).
If that official version becomes unavailable, use the identical archive supplied
with the matching release. The expected SHA-256 is:

```text
f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f
```

Verify it with `shasum -a 256` on macOS, `sha256sum` on Linux, or
`Get-FileHash -Algorithm SHA256` in PowerShell. The builder also refuses a mismatch.

Install the toolchain for your target:

| Target | Build tools | Runtime access |
| --- | --- | --- |
| macOS | Xcode Command Line Tools (`xcode-select --install`), C compiler and `ar` | USB/HID and serial metadata; device use remains explicit |
| Linux | C compiler, development headers, `ar`/binutils and pthreads, e.g. the distribution's build-essential toolchain | USB/serial permissions; this recipe has no separate Linux HID enumeration route |
| Windows x64 | MSYS2 CLANG64 Clang, compiler-rt and binutils; expose `clang64/bin` to Windows Python, set `CC=clang` | The selected libusb0/Windows driver must match the device; compiling does not install a device driver |

For Linux access consult the
[official installation guide](https://www.argyllcms.com/doc/Installing_Linux.html)
and archive's `usb/55-Argyll.rules`. Review any rules/groups with the machine's
administrator; do not grant blanket permissions. Windows toolchain setup is
shown in the [native CI recipe](../.github/workflows/native.yml); runtime driver
installation is a separate deliberate action.

From a POSIX shell, retain a new output directory:

```sh
uv run --locked --extra app python tools/argyll_native_check.py \
  --archive /path/to/Argyll_V3.5.0_src.zip \
  --contribution tools/native_argyll/contributions/cr_pr --output local/instrument-build
```

PowerShell uses the same arguments; backticks continue its lines:

```powershell
$env:CC = 'clang'
uv run --locked --extra app python tools/argyll_native_check.py `
  --archive C:/sources/Argyll_V3.5.0_src.zip `
  --contribution tools/native_argyll/contributions/cr_pr --output local/instrument-build
```

Output must not already exist. Inspect the complete report for success. Keep its
`report.json`, both workers and retained licenses together. The executables are
named `generic-worker-never-run` and `argyll-description-worker-never-run`
(with `.exe` on Windows): the name means software checks did not execute them.
During real use the description worker lists/initializes selected equipment and
the session worker performs selected instrument operations. They are distinct
from the substituted test workers.

In the source desktop app open **Connect instrument…**, expand **Instrument software details**
and use **Choose instrument software…** to select the retained report.
Selecting/authenticating a report hashes files; **List connected instruments**
is a separate explicit discovery action. Follow the instrument guide for model,
initialization effects, calibration, placement and saved readings. CLI users use
`--build-report` (or `CM_ARGYLL_BUILD_REPORT`) as described in
[usage](usage.md). Do not guess a USB index or run an acquisition example without
checking selection and the device's effects.

The [software report command](platforms.md#send-a-software-report) builds native
software in a temporary directory and removes it after its dependent tests.
That expired report cannot be selected for later instrument use. Keep a retained
build only while it has an active application/test consumer.
