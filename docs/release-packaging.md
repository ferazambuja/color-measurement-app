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
for freezing. Windows ARM64 runs that x64 app through emulation. The current
native ARM64 PySide6 wheel omits Qt WebEngine, so it cannot package this UI.
Native ARM64 instrument workers passed substituted functional checks during the
investigation. MSYS2 also omits ARM64 sanitizer runtimes, and x64 ASan fails at
startup under Windows ARM64 emulation. Native ARM64 app qualification remains
open. macOS packages are built on macOS.

Use `tools/build_desktop_app.py` as described in the [desktop reference](desktop.md#build-a-standalone-application).
The builder uses the same host on every OS, checks application code/resources,
and authenticates both workers and their notices without executing them.
Build a delivered package without `tools/build_desktop_app.py --candidate`: that option
names a side-by-side candidate, and renaming its bundle afterwards keeps the
candidate executable name. A real package launch and Demo session still need
their own target result.
The shared vector icon supplies Qt windows and dialogs. The builder embeds its
multi-resolution ICO in Windows executables and its ICNS in macOS bundles.
Linux deliveries include **Add to Applications.sh**, which registers the extracted
folder and its icon in the current user's application menu. Run it again after
moving that folder.
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

Collect the four source attachments from the selected clean delivery commit in
one source directory.

```sh
uv run --locked python tools/collect_runtime_sources.py /path/to/sources
uv build --sdist --out-dir /path/to/sources
ln /path/to/Argyll_V3.5.0_src.zip /path/to/sources/
```

The Argyll ZIP is the pinned archive used by the native check. In a reused source
directory, `ln` preserves the existing file and reports that its name is already
taken. Each delivery gets independent copies of the Argyll ZIP and application
sdist. Reusing the input directory for a later build or download therefore
preserves completed deliveries. Reuse the large verified Qt/PySide archives by
hard link. Their collector checks existing files and downloads missing archives.

Actions artifact storage can incur charges, including in public projects; the
manual workflow retains candidate artifacts for one day. Public-repository
artifacts are readable to repository readers. They are not private staging. A
binary without its required source set is an incomplete candidate and must not
be published.

Qt is distributed as replaceable shared libraries. Retain that layout, the
license/source notices and the right to debug modifications. Record changes to
Qt, PySide, Python or native code rather than presenting modified dependencies
as untouched upstream binaries. The imported Argyll and application components
retain their separate license terms.

## Prepare the complete asset set

Use the same build interpreter, a clean committed checkout and an unused output
folder. Put the matching app sdist, pinned Argyll ZIP and verified Qt/PySide source
archives together in one source directory, then run:

```sh
uv run --locked --extra app --group app-build python tools/prepare_beta_assets.py \
  --application "/path/to/Color Measurement.app" \
  --native-report /path/to/production-build/report.json \
  --source-directory /path/to/sources --output /path/to/assets
```

On Windows/Linux, select the complete `Color Measurement` application folder.
The tool archives the selected application first and checks an extraction of the
archive, so every application check reads the delivered bytes. The extraction uses
`ditto` on macOS, `tar` on Linux and the ZIP extractor on Windows. It needs temporary
space beside the output folder for one extracted copy of the application.
The tool rejects Candidate builds, checks the frozen worker identity and compares
the sdist with all included tracked source bytes. It authenticates workers
without executing them. On macOS it requires an intact ad-hoc seal over the whole
bundle, the only post-build check of third-party runtime files. On Linux the
delivered **Add to Applications.sh** must be the committed executable script. It verifies the
pinned dependency archives and includes all four source attachments. Delivered
copies must match their verified hashes. Changes to the application sdist during
verification, the archive after its check or the original native report during
preparation cause a failure.
Qt/PySide archives share storage by hard link when supported, with a copy
fallback. Keep the verified Qt/PySide archives and completed deliveries immutable.

The tool writes `RELEASE.txt`, `SOURCE-MANIFEST.json` and `SHA256SUMS` from the
verified inputs. The manifest binds the app archive, source commit/tree, package
inventory, worker report and all source-archive digests. Use those generated files
for delivery; do not replace them with hand-written parallel records. This
command prepares local files and never publishes them. In Actions, the candidate
records and the `corresponding-source-<commit>` artifact are retained separately
to avoid storing the large source archives twice. Put the candidate's `assets/`
files beside the source archives and verify `SHA256SUMS` before delivery.

## Combine platform deliveries

Download the corresponding-source artifact and each successful target artifact.
Put the four source archives beside each target's candidate records, then verify
that target's SHA256SUMS. All targets must name the same source commit and tree.
Combine the candidate asset directories with:

```sh
uv run --locked python tools/assemble_beta_release.py \
  --candidates /path/to/macos/assets /path/to/windows-x64/assets \
  /path/to/linux/assets --output /path/to/release
```

The combined directory contains each app download, one copy of the common source
archives and one RELEASE.txt, SOURCE-MANIFEST.json and SHA256SUMS. Manifest format
2 retains every target's original metadata under its target name. Verify the
combined checksums before uploading the complete directory.

## Inspect and release manually

Verify the application archive against the checksum file before extracting.
Confirm the app opens without the checkout, that Demo can create, finish, reopen and export a batch, and that
Close handles pending changes and cleanup failures. Test renderer-crash
recovery through a controlled software test. This does not authorize running
production workers or using connected equipment.

Unsigned packages can produce Gatekeeper or SmartScreen prompts. Keep the
[platform instructions](platforms.md#desktop-runtime-requirements) accurate;
never instruct testers to disable protections or grant blanket device access.
Code signing/notarization remains a separate release task.

Publication is manual. Review the final payload, sources, checksums, beta label,
known limits, feedback links and current download statements before publishing.
Push the manifest's source commit and create the release tag on that commit;
the attachments and `RELEASE.txt` name it.
After publication, update all statements that say a binary is unavailable,
including `SECURITY.md`, using the actual URL and qualified target. Never expose
private development history by changing an old repository's visibility.
