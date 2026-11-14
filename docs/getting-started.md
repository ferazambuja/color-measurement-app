# Getting started

This is a beta looking for testers. Start with Demo before connecting equipment.
The [beta release](https://github.com/ferazambuja/color-measurement-app/releases/tag/v0.1.0-beta.2)
has downloads for Apple silicon Macs, Windows x64 and Linux x64. Windows ARM64
uses the Windows x64 package through emulation. The app includes Python, its
instrument software and runtime libraries. Ordinary users need the app archive
and SHA256SUMS. The source archives are optional downloads for rebuilding and
modification. An installed Argyll version is independent of the app's bundled
instrument software.

## Standalone macOS test release

The prebuilt Mac target is Apple silicon on macOS 27 or later.

Download the [app ZIP](https://github.com/ferazambuja/color-measurement-app/releases/download/v0.1.0-beta.2/Color-Measurement-0.1.0-macos-arm64.zip)
and [SHA256SUMS](https://github.com/ferazambuja/color-measurement-app/releases/download/v0.1.0-beta.2/SHA256SUMS)
from the beta release. Verify the ZIP before extracting it. Safari opens
downloaded ZIP files by default: it extracts the app and moves the ZIP to the
Trash. To verify first, turn off **Open “safe” files after downloading** in
Safari Settings > General before downloading, or drag the ZIP back from the
Trash. In the folder that holds both downloads, run:

```sh
shasum -a 256 -c --ignore-missing SHA256SUMS
```

It must print `Color-Measurement-0.1.0-macos-arm64.zip: OK`; it fails when no
listed file is present. You can also compare the output of
`shasum -a 256 Color-Measurement-0.1.0-macos-arm64.zip` with the SHA-256 shown
beside that asset on the release page. A checksum detects changed bytes relative
to the published value; it does not identify a publisher. Extract the ZIP and
move **Color Measurement.app** to your Applications folder before opening it.

This test release is ad-hoc signed, not Developer ID signed or notarized, so
macOS blocks the first launch of a downloaded copy. Open the app once. When
macOS says it could not verify **Color Measurement.app**, choose **Done**, not
**Move to Trash**. Then open System Settings > Privacy & Security, scroll down
and choose **Open Anyway** for Color Measurement. When the warning reappears,
choose **Open** only if the app came from this release and its checksum matched.
See [Apple's instructions](https://support.apple.com/en-us/102445). Stop if its
origin or checksum is uncertain. Do not disable Gatekeeper, remove quarantine
recursively or run the app with `sudo`. Notarization is required before wider
distribution.

## Standalone Windows beta

Download the [Windows x64 ZIP](https://github.com/ferazambuja/color-measurement-app/releases/download/v0.1.0-beta.2/Color-Measurement-0.1.0-windows-x86_64.zip)
and the release's SHA256SUMS. Windows 11 ARM64 runs this package through x64
emulation. A native ARM64 package is unavailable with the current Qt dependency.
In PowerShell, compute the ZIP's hash:

```powershell
Get-FileHash -Algorithm SHA256 Color-Measurement-0.1.0-windows-x86_64.zip
```

Compare it with that filename's line in SHA256SUMS. Use **Extract All**, keep the
whole extracted folder together and open **Color Measurement.exe** inside it.
Follow the [SmartScreen guidance](platforms.md#desktop-runtime-requirements)
if Windows blocks the app. Never disable SmartScreen, antivirus or system policy.
Python, Argyll and WebView2 installations are unnecessary for the bundled app.

If the app exits before its workspace appears in a virtual machine, try the
[Windows VM graphics launch option](platforms.md#windows-virtual-machine-graphics).

## Standalone Linux beta

Download the [Linux x64 archive](https://github.com/ferazambuja/color-measurement-app/releases/download/v0.1.0-beta.2/Color-Measurement-0.1.0-linux-x86_64.tar.gz)
and SHA256SUMS. Use a graphical desktop with glibc 2.36 or newer and the
[required system libraries](platforms.md#desktop-runtime-requirements).
From the download folder:

```sh
sha256sum --check --ignore-missing SHA256SUMS
tar -xzf Color-Measurement-0.1.0-linux-x86_64.tar.gz
"Color Measurement/Color Measurement"
```

The checksum command must report the application archive as `OK`. Keep the
extracted folder together. Run as your normal desktop user with the Chromium
sandbox enabled. Linux ARM64 requires its own build and is outside this download's
qualification.

## Try Demo

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
desktop builds are experimental. Check the
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
