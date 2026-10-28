# Package a beta for testers

A prepared package is not a published release. Build and qualify each target
separately. Keep a passing macOS candidate independent of Windows/Linux work.
The portable host does not establish physical instrument operation or accuracy.

## Before building

Use the exact app revision intended for delivery, a clean source checkout, the
locked dependencies, and the software-only native build procedure in
[platforms](platforms.md#send-a-software-report). That procedure builds production
workers without running them and tests substituted workers instead. Keep its
original report, complete summaries, environment and command. A failed or
partial run must retain that label. Do not silently use a reduced replay as a
full target gate. Attach the hosted run URL and ID when a hosted run exists.

The Linux x64 package is built inside Debian 12, giving a glibc 2.36 baseline.
Windows x64 uses MSYS2 CLANG64 for production workers and native Windows Python
for freezing. macOS packages are built on macOS. A Mac cannot produce a qualified
Windows GUI executable by running PyInstaller against Mac libraries.

Use `tools/build_desktop_app.py` as described in the [desktop reference](desktop.md#build-a-standalone-application).
The builder uses the same host on every OS, checks application code/resources,
and authenticates both workers and their notices without executing them.
A real package launch and Demo session still need their own target result.
Check a normal user account without development paths or installed Python.
Keep this separate from testing physical equipment.

## Attach source and notices

Each binary delivery must include these files together:

- The whole application folder or `.app`, archived without AppleDouble `._` files.
- The app source distribution from that exact clean commit, including native
  worker sources, contribution files, lockfiles and build instructions.
- The pinned `Argyll_V3.5.0_src.zip`, matching the native report.
- The exact Qt and PySide/Shiboken source archives in
  [`QT-ORIGIN.json`](../tools/desktop_runtime_licenses/QT-ORIGIN.json), not only
  a link or a promise to upload them later.
- The complete runtime/application/native notices, a source manifest naming the
  commit/tree and worker report, and SHA-256 checksums for every asset.

Download the runtime source set once and reuse it for both target packages:

```sh
uv run --locked python tools/collect_runtime_sources.py /path/to/runtime-sources
uv run --locked python tools/collect_runtime_sources.py /path/to/runtime-sources --check
uv build --sdist --out-dir /path/to/assets
```

The Qt source archive is large. Keep one verified source set. Actions artifact
storage can incur charges, including in public projects; the manual workflow
retains candidate artifacts for one day. Public-repository artifacts are readable
to repository readers. They are not private staging. A binary without its
required source set is an incomplete candidate and must not be published.

Qt is distributed as replaceable shared libraries. Retain that layout, the
license/source notices and the right to debug modifications. Record changes to
Qt, PySide, Python or native code rather than presenting modified dependencies
as untouched upstream binaries. The imported Argyll and application components
retain their separate license terms.

## Inspect and release manually

Verify the checksum file after extracting. Confirm the app opens without the
checkout, that Demo can create, finish, reopen and export a batch, and that
Close handles pending changes and cleanup failures. Test renderer-crash
recovery through a controlled software test. This does not authorize running
production workers or using connected equipment.

Unsigned packages can produce Gatekeeper or SmartScreen prompts. Keep the
[platform instructions](platforms.md#desktop-runtime-requirements) accurate;
never instruct testers to disable protections or grant blanket device access.
Code signing/notarization remains a separate release task.

Publication is manual. Review the final payload, sources, checksums, beta label,
known limits, feedback links and current download statements before publishing.
After publication, update all statements that say a binary is unavailable,
including `SECURITY.md`, using the actual URL and qualified target. Never expose
private development history by changing an old repository's visibility.
