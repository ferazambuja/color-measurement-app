# Platforms and reports

Package availability, implemented recipes, executed software checks and physical
instrument use are separate facts. None establishes optical accuracy.

| Platform | Application route | Native backend recipe | Physical qualification |
| --- | --- | --- | --- |
| Apple silicon/macOS 27+ | [Standalone beta](https://github.com/ferazambuja/color-measurement-app/releases/tag/v0.1.0-beta.2); source desktop host | macOS USB/HID/serial | None |
| Intel/older macOS | Attempt source setup; host/dependency compatibility unqualified | macOS recipe, target result must be reported | None |
| Linux x64 | [Experimental x64 beta](https://github.com/ferazambuja/color-measurement-app/releases/tag/v0.1.0-beta.2), desktop source, CLI and Python API | USB and serial, no separate HID enumerator | None |
| Windows x64 | [Experimental x64 beta](https://github.com/ferazambuja/color-measurement-app/releases/tag/v0.1.0-beta.2), desktop source, CLI and Python API | Windows-GNU/MinGW-w64 CLANG64, libusb0, HID, serial | None |
| Windows ARM64 | Windows x64 beta through emulation; native GUI unavailable with current Qt wheel | Native ARM64 backend compiled and functionally checked with substituted instruments; device drivers must support ARM64 | None |
| Other architectures/toolchains | Source investigation; no claimed backend qualification | Not established by another target's result | None |

The stock catalog has 38 names; with the combined CR/PR contribution, the catalog has 43
names. These are recognized names, not physical qualification.

The beta has Apple silicon Mac, Windows x64 and Linux x64 downloads. Native
Windows ARM64 packaging is blocked by the pinned PySide6 wheel's missing Qt
WebEngine module. No community physical-instrument report is published yet. CI configuration is a recipe, not proof of a successful run. Historical results apply to their
original source/target; they are not silently promoted to a new revision.

## Send a software report

No instrument is needed. Do not connect one or run ordinary Argyll tools for this
report. Use the source checkout, Python 3.11–3.14, uv and a compatible C toolchain
from [getting started](getting-started.md). Install Node.js 22, 24 or 26:

```sh
npm ci --prefix tools/desktop_builder --ignore-scripts --no-audit --no-fund
uv sync --locked --extra app --extra mat
uv run --locked --extra app --extra mat python tools/test_all.py --require-mat \
  --argyll-archive /path/to/Argyll_V3.5.0_src.zip \
  --argyll-contribution tools/native_argyll/contributions/cr_pr
```

Use PowerShell backticks instead of POSIX backslashes for multiline commands on
Windows. Set `CC=clang` and expose the native CLANG64 tools there. Record the
compiler version/target. This single run builds native workers, runs only
substituted/inert probes, then tests Python/renderer behavior and installed
packages. Do not repeat its native build separately unless isolating a failure.

Post the exact app commit/tree, imported contribution commits/trees from their
manifests, OS/version/architecture, Python, uv, Node/npm and compiler versions,
command, exit and complete final summaries. Include `Tree:`, `Skipped:`,
`Scope:` and native/MAT/renderer coverage. Redact personal paths from a copy of
logs; keep originals locally. A missing prerequisite or failure is useful too.

The Qt host helpers and event-loop tests run on all three platforms with the
`app` extra. Platform-specific limitations can still make a gate **PARTIAL**.
Windows skips POSIX signals/FIFOs/process groups,
directory synchronization/link and open-directory-rename cases, the pySerial
POSIX implementation, and probes requiring an eight-byte C `unsigned long` in
Windows Python. Record actual case identities, parameter values and reasons;
these are not blanket module exemptions. A directory-symlink permission limit
needs its actual reason recorded. Missing compilers/sanitizers/dependencies,
unexplained skips or missing inputs remain findings. Do not rename a partial
result "full form passed" or suppress skips to obtain a preferred label.

Submit through the
[platform-report issue template](https://github.com/ferazambuja/color-measurement-app/issues/new?template=platform-report.md).
Reports remain unverified until reviewed. With consent, a reviewed
entry can credit the reporter's chosen public handle and exact tested commit.
An instrument report uses the separate [instrument procedure](testing-with-an-instrument.md).

## Continuous integration

[Software CI](../.github/workflows/software.yml) checks Linux Python 3.11/3.14
on public main/PRs and Linux/Windows Python 3.11–3.14 weekly or manually.
Private repositories require explicit dispatch to protect their minute allowance. It lacks the
native-stage inputs and is labelled partial.
[Native CI](../.github/workflows/native.yml) checks Linux/Windows x64 for changes
to application/build/test inputs in public repositories, and on manual dispatch.
Private repositories require explicit dispatch. It builds the combined
CR/PR workers and joins their substitutes to application tests. Prose-only
changes do not select that workflow. Neither workflow executes production
instrument workers or establishes macOS desktop/hardware behavior.


## Desktop runtime requirements

The Windows/Linux package targets are Windows 11 x64 and Debian 12-class
Linux x64 (glibc 2.36 or newer), with a graphical desktop and working GPU drivers
or a supported software-rendering route. This is a build target, not a successful
user-test claim. Windows ARM64 uses the x64 app through emulation. Native ARM64
Windows and Linux GUI builds need their own results. In particular,
the pinned Qt ARM64 Linux wheel requires glibc 2.39; it cannot be used for the x64
package's older baseline. macOS qualification still concerns Apple silicon on
macOS 27. Other Mac hardware and OS versions remain unqualified.

Qt WebEngine is bundled. Windows does not need WebView2, and Linux does not need
WebKitGTK. Linux still needs its normal graphics, font, X11/Wayland, NSS, audio
and desktop libraries. On Debian 12, install the runtime prerequisites with:

```sh
sudo apt install libegl1 libopengl0 libxcb-cursor0 libxkbcommon-x11-0 libnss3 \
  libasound2 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxi6 libxss1 \
  libxshmfence1 libgl1 libglib2.0-0 libdbus-1-3 libfontconfig1 \
  libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libwayland-client0 \
  libwayland-server0 libwayland-cursor0 libwayland-egl1
```

Use your distribution's package names and administrator-approved procedure.
Do not run the application as root or disable the Chromium sandbox. These
libraries do not grant device access. Linux's separately reviewed Argyll udev
rules and Windows's model-specific libusb0/HID driver requirements are covered
in [getting started](getting-started.md#build-retained-instrument-software).

Unsigned Windows beta packages can trigger SmartScreen or be blocked by an
organization's policy. Compare SHA-256 with the published checksum before
opening. If Windows offers **More info > Run anyway**, use it only after checking
the source and hash; it may be unavailable under policy. Do not disable
SmartScreen, antivirus, driver-signature enforcement or system policy. Signing
is required before wider distribution. A checksum detects changed bytes; it
does not establish a trusted publisher.

The [manual package workflow](../.github/workflows/portable-beta.yml) runs only
on dispatch. It builds and checks a candidate; it does not publish a release.
Artifacts follow repository read permissions, so public-repository artifacts
are public to readers. Private hosted runs consume the account's current
Actions allowance; review the matrix and budget before dispatching.
