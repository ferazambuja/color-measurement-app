# Offline measurement application

The native host uses Qt dialogs and Qt WebEngine over the existing
`BatchApplication`. Python owns setup, command admission, sample and annotation
drafts, instructions, retained outcomes, selection, saving and shutdown. A
renderer reload binds a new generation to the same owner. The standalone app
includes its instrument software. It starts with no measurement source selected;
connecting an instrument and choosing Demo are separate, explicit actions.
Individual readings use the selected available ordinary spot mode. The
Measurement task disclosure also exposes the selected mode's available
X-Y sheet, stored-chart, refresh-frequency and configuration workflows. Live strip
acquisition is absent from the mode and task controls; retained strip data remains
available for inspection and export. Modes
and typed fields come from the frontend descriptor; the UI does not maintain
a second device-policy table. Demo acquisitions
are simulated. Saved review preserves each retained record's original physical,
replay or simulated provenance; displaying a record does not validate its accuracy. The host
starts no discovery or initialization when opened or when ordinary settings are
read. Deliberate equipment actions use **Connect instrument** as described below.

## Run an installed package

Open the standalone `.app` on macOS, the application executable on Windows, or the launcher in the Linux application folder. It includes its Python runtime and instrument
software. For a wheel installation or development checkout, install the `app`
extra and launch with Python:

```sh
uv sync --locked --extra app
uv run --locked --extra app color-measurement-app
```

The host loads only bundled offline HTML, CSS, JavaScript, reference data and
third-party notices. It starts no HTTP server. JavaScript modules are bundled
into a classic script because file-loaded modules do not initialize reliably
in the local Qt WebEngine route. User-selected HTTPS citations open in the
system browser; acquisition never uses that browser or an HTTP endpoint.
Startup refuses stale or missing checkout renderer inputs before creating the
batch owner or web view. When Qt is available, a native alert explains the
refusal; terminal launches retain the same error text and exit unsuccessfully.

## Connect an instrument

The standalone app authenticates its bundled instrument software on startup.
This prepares the software only: it does not list connections, initialize an
instrument, calibrate or take a reading. The workspace starts with no source
selected.

1. Power and connect your instrument, then choose **Connect instrument**.
2. Choose **List connected instruments**. Select the intended connection and
   review its model and initialization effects. Accept the required choices
   explicitly, then choose **Connect selected instrument**.
3. Choose **Use for new batch**. Select its measurement mode and settings, choose
   a new save folder, and create the batch.
4. Prepare the sample and choose **Measure**. Follow any calibration or placement
   instructions required by the driver.

These steps use the default Argyll route. For CS-2000/CS-2000A, choose
**Advanced connection options > Direct serial**, select the model and serial port,
review and accept its initialization effects, then initialize the instrument.

The shipped app supplies the instrument software; ordinary connection does not
require finding a build report. **Instrument software details** exposes software
information. If bundled software is missing or has changed, the app shows an
error and refuses instrument connection; reinstall a complete build.

**Try Demo** on the empty workspace, or **Use Demo** in batch setup, explicitly
chooses simulated sources. Demo readings do not come from a connected instrument.
Saved records can also be opened without choosing a measurement source.

Wheel and checkout launches keep manual software configuration under
**Instrument software details > Choose instrument software**. Select an
authenticated production build report before listing instruments. See the
[equipment setup API](desktop-equipment.md#select-a-build-report) for report
validation and software rehearsal.

## Measurement workspace

The header keeps save status beside the batch name, separate from actions. A
check marks Saved; pending work uses a clock, and save failures use an inverted
attention indicator with the error text. Recovery remains in the workspace.

Latest reading shows this batch's last retained result independently of the
inspected table row, in live and saved review alike; a reading inherited from a
chart source is never named latest. Color readings show an approximate preview,
native Y and its declared unit and scale, X/Z and available x/y and u′/v′
coordinates. Lab appears only when the current projection supplies it, beside the
declared white it is relative to. Reflective relative data, illuminance and
time-integrated quantities retain their own units; the UI does not label every Y
as luminance. Spectrum-only results show no native XYZ. Frequency and timing
results use their typed values without a color substitute. Narrow layouts retain
the compact last-reading summary; additional values remain in the table/details.

View settings groups layout, color previews and reference outlines. Detailed
reference diagnostics and rendering qualifications are available in disclosures.
Changing a preview scale does not change the native measurement. Panels retain
their named close controls and normal Escape behavior.

For an open batch, **Device & setup** opens Measurement settings for the next
reading. Batch naming and destination fields appear when creating a new batch.
Instrument preparation and unavailable-setting explanations have separate
disclosures. A finished batch keeps its results available for review and export
and offers **New batch** in place of the acquisition controls. **New batch** is
enabled only when the owner's `can_start_new_batch` admits it, the same rule
creation enforces; until saving or recovery is resolved it stays disabled with
that reason. If the batch finishes while **Device & setup** is open, the panel
becomes read-only Batch settings. When cleanup and saving both need attention,
the recovery details show both reasons.
Each new batch starts with Individual reading when the selected mode offers it;
an earlier configuration-query or sheet task does not become the new batch's
primary action. **Measurement task > Check instrument configuration** reports
the selected mode's available settings. After a successful check changes the
instrument context, finish that batch and create a new one with the refreshed
source settings.

Native instructions show the current calibration or placement guidance and the
response time limit. The limit begins when the instruction is requested, so some
time may have elapsed before the prompt appears. Calibration confirmation has no
default Return action. Each prompt exposes one Cancel operation action, preserving
the instrument's offered responses and the application's cancellation semantics.

Save recovery keeps Retry save and Another folder visible with a short summary
and destination name. **Save details** retains the full original error and path;
the measurement dock points to recovery without repeating the technical error.

## Inspect saved data and export

In **Batch**, choose **Open saved data** and select a batch folder or a declared
whole-batch MAT archive. Readings appear in the same plots and table. The header
names the read-only review and offers **Return to live**; your live batch,
annotation drafts and save recovery remain with their original owner.
Save-recovery details name the retained live batch and its destination, including
while an imported record is displayed. Reference and white changes made during
saved review apply only to that review and do not
write source files. The display reports excluded operations. If the source changes,
measured values are unavailable until you open it again; returning to live and
normal Close remain available.

**Batch > Export data** saves all displayed or checked readings as analysis JSON,
CSV or MAT. It retains native records, full applied annotations and the selected
comparison context. **Save whole batch MAT** archives batch files and declared
recovery inputs, regardless of plot checkboxes. MAT choices follow the current
runtime's dependency availability. Original-record export keeps unavailable-format
explanations in a disclosure, so a refused SPECT format does not appear as a warning
for an available JSON save. The desktop installation extra and standalone builder
include NumPy/SciPy for the existing MAT serializers. Operators do not
need a separate package installation. A standalone macOS ARM64 candidate saved
whole-batch and analysis MAT files for 160 simulated spectral readings and
reopened the whole-batch archive in its native workspace. This establishes the
packaged software route, not MATLAB numerical-reader or physical-device validation.

A failed export retains the exact prepared bytes. **Save retained export** chooses
a new file and preserves that earlier snapshot, including after comparison or
source changes. Close offers Keep working, Save to a new file, or explicit discard
for each pending export, including a saved-only session. If publication occurred
before synchronization failed, its earlier file and uncertain save status remain
explicit. Retrying does not recalculate the snapshot or overwrite that file.

## Build a standalone application

Build on each target operating system with Python 3.11–3.14 and the locked
`app` extra and `app-build` tools. PyInstaller creates a normal application
folder, including Qt WebEngine, Python, NumPy, SciPy and pySerial. Windows uses
the included Qt engine, without a separate WebView2 runtime. Linux uses Qt,
without WebKitGTK. See [platform requirements](platforms.md).

```sh
uv sync --locked --extra app --group app-build
uv run --locked --extra app --group app-build python tools/build_desktop_app.py \
  --instrument-build-report /path/to/production-build/report.json \
  --output /tmp/color-measurement-app --build /tmp/color-measurement-app-build
```

Use unused output/build paths. PowerShell uses backticks for multiline commands.
The report and workers must belong to that operating system. Build or obtain
those workers through the [software-only native procedure](platforms.md#send-a-software-report).
The builder authenticates and copies production worker bytes and native notices
after freezing; it never executes or re-signs them. Their report digest is
compiled into the application for all operating systems. A frozen runtime with
a missing identity fails closed rather than entering source-development mode.
On macOS, the digest is also recorded in `Info.plist`, and the outer application
receives an ad-hoc signature without recursively changing worker signatures.

`PACKAGE.json` records the target, Python version, application source inventory
and portable worker-report digest. The builder checks every application code
object and resource against its source without executing that code. Application
bytecode uses PyInstaller's relative filenames; old interpreter caches are not
copied. For the ad-hoc beta, the Qt renderer helper retains Qt's original non-hardened
ad-hoc signing policy; PyInstaller otherwise adds library validation without a
Team ID, which prevents that helper from loading the bundled Qt framework. This
affects only the packaged helper and its framework. Notarization requires the
separate hardened-runtime entitlement/signing route. Instrument signatures and
system protections are not changed.

The runtime notices come from the actual numerical/Python distributions
and authenticated Qt and native inventories. Qt remains in replaceable shared
libraries, rather than being hidden in a single-file executable.

Distribute the whole folder or `.app`. Moving only the executable breaks its
runtime. The source archive must accompany binary delivery, including all native
build scripts and contribution source, the pinned Argyll archive and matching
copyleft runtime source. Notices alone are insufficient. See the
[release packaging procedure](release-packaging.md). No hosted workflow creates
or publishes a GitHub release automatically.

The JavaScript compiler remains esbuild 0.28.2. Its authenticated generation
receipt currently covers macOS ARM64. Other targets use the committed renderer;
they check its inputs and output without regenerating it. A change to renderer
source requires explicit regeneration through the authenticated compiler.

## Operator route

The workspace is an application with persistent measurement controls and a
readings table below its plots. Its height is bounded by the window: the plot
region has one shared height budget and the table receives the remaining space,
with a sticky heading and internal row/column scrolling. Extra views replace the
active plot rather than extending the page. Desktop 2D plots share one row;
narrow views keep one plot visible. Each plot heading selects Spectrum,
Chromaticity, 3D, Chart guide or Spectral residuals according to available data.
The header's **View > Layout** selects **Fit window**, **One plot** or **Table
only**; Fit window restores the two selected positions when the window widens.
One plot retains its layout when its plot type changes. Residuals occupy one
plot and leave the previous pair available through Fit window. Analysis,
comparison, filtering and column choices sit beside the readings table. Empty
startup prioritizes New batch and Open saved batch, without disabled sample
fields or repeated empty-batch statuses. Device & setup stays in the app header.
Table only gives all of the workspace to data review. Expand gives the chosen plot more of the
workspace while retaining access to the table; Restore returns the previous
layout. Resizing changes presentation, not measured values, checked membership,
inspection or comparison settings. Chromaticity retains equal axis scale.
Classic and Digital SG guides fit the entire board to the plot's width and
height without internal scrolling. Chart mode gives the board the remaining
height while retaining a shorter readings table below. Compact cells retain
keyboard inspection; **Chart controls > Inspect patch** offers a full-size
picker for every cell and does not change the measurement target. **Inspected
patch**, below the board, opens the inspected cell's expected and measured
values side by side: preview with its channel clipping, XYZ, Lab, white XYZ with
its illuminant, and source. Expected values come from a TI2 with a declared
reference, and expected Lab is shown only when the selected source columns are
Lab. Measured values are the kept reading's native XYZ, with Lab computed
against the white of its recorded illuminant. An absent side states its
reason, such as no TI2 reference or no kept reading. Saved
XYZ-only readings default to 3D and chromaticity rather than an empty spectrum.

Table swatches and spectrum curves consume the same Python-owned reading previews.
Qualified reflective readings use their recorded illuminant and common relative
white Y, with the declared corresponding-color adaptation shown in Details and
swatch tooltips. They do not use the emissive cd/m² slider or claim native display
gamut membership. Absolute emissive readings retain their own luminance scale;
missing or incompatible quantities remain unavailable. The scientific definitions
and qualification are documented in [illustration methods](illustrations.md).

The primary controls are the resolved next sample name, Measure, Repeat inspected,
operation state and Cancel while an operation is active. Numbering and notes are
available through their sample disclosures. Measure names its target; Repeat's
accessible name and tooltip name its immutable source reading. Device and batch
setup has a persistent header control; compact windows retain its named icon.
Batch actions, calibration, destination and synchronization details are in Batch. One header
save state reports Saved, Saving, Save failed or pending edits. Failed saving
shows the destination, reason and recovery prominently without opening that
menu. Automatic files whose refresh is owned by the current reading or queued
publication show Files pending until their preparation starts; an unrelated
failed or unavailable file still shows its attention notice. Compare, Color and
Activity are in the toolbar's Analysis menu. Narrow
layouts omit the duplicate latest-reading block so controls leave room for
plots and readings. Short windows retain scroll access to the workspace rather
than clipping the table, and spectrum tick density follows the plot's height.
Setup, exact
annotations, saved-file checks and Help use ordinary labelled panels with a live
background workspace and no required sequence. Reading details is a drawer;
closing it does not consume table space.

The table's Find/filter, Columns, Reading details and Follow latest tools occupy
its compact heading. Columns offers individual X/Y/Z, both coordinate systems,
Lab components, reference metrics, spectrum range/band count, units, instrument,
mode, acquisition time, calibration, notes and save state. Column choices remain
unchanged across tools and survive renderer reloads. Y headers use retained
units; mixed units are named and each XYZ cell retains its unit/scale in its
title. Missing values never become zero. Spectrum-only records have no inferred
XYZ, coordinates, Lab or RGB preview. CCT, Planckian Duv, CRI and TM-30 are not
current metrics. Frequency and timing outcomes remain separate backend records.
Their native Hz, display-delay and signed instrument-reaction values appear in
the latest result, optional table columns and exact reading details. Missing
XYZ and spectra remain unavailable.

Task-specific fields retain optional omission, explicit zero and explicit false.
Multiple-reading workflows require distinct sample IDs in returned-position order;
Python validates coupled target geometry. Source updates preserve typed controls
and their entered values while the task and context remain the same.

Display timing draws the backend's requested black and white in a controlled
workspace surface. Two successive animation frames establish a browser software
draw-completion receipt using one monotonic page clock. Python receives the exact
operation/request, phase, stimulus and display identities through its existing
`presented` command. Black remains displayed between phase requests; there is no
second client wait after the backend's wait. Cancellation, page/focus loss, hidden
presentation or rejected/deadline receipts stop queued drawing and cancel the
operation. This establishes a software presentation exchange, not optical onset,
physical display identity, hardware timing accuracy or device support. Operators
must place the workspace on the declared display and sample the controlled area.

Native operator instructions use actual simulated/physical provenance and only
the current offered responses. They show preparation text and the backend's
original response time limit, with no Return default. Native Close waits at most
30 seconds for canonical metadata and optional-file work to settle before taking
a discard identity. A timeout leaves the application open and reports the cause. Demo shutdown also
waits off the UI thread for at most 30 seconds. An unsettled Demo worker offers
Keep application open or Retry stopping Demo; both executors and source ownership
remain available, and direct-source cleanup waits for that worker to finish.

A saved reference and declared white remain named beside the table; opening
Compare does not change them. Native comparison columns require the reference;
ΔE00 additionally names the declared white. Lab inspection does not require a
comparison reference. Numeric comparisons cover all retained rows. Plot
checkboxes control membership only. Spectra and residuals use table/curve
selection and a highlighted inspected curve instead of per-reading legends.
Hovering a curve, point or table row previews that reading across all visible
plots and outlines its table row. The table heading names the preview without
adding a legend. Hover never changes saved inspection, Follow latest, checked
membership or table scroll. A click selects the reading and scrolls only the
table to reveal its row; table filters remain in force, with Show selected
reading available when needed. Spectrum and residual picking use the nearest
line segment within eight screen pixels, independent of SVG stacking order.
Chromaticity uses the nearest marker within the same distance. Coincident
readings remain individually selectable from the table. A 2D plot has a keyboard
entry even when the inspected reading is unchecked: arrow keys, Home and End
preview its readings; Enter or Space commits inspection. 3D hover previews a
marker with an outline, while camera drags never select it. Touch selects on tap
without retaining hover. Redrawing or changing the source clears pointer preview.
Different spectral quantities or units are not overlaid on one numerical axis.
Plot only inspected and Restore checked set support crowded batches. Settings,
reference keys and diagram explanations open on demand in a contained popover;
no plot footer reserves space for them. Shared appearance controls are in
**View > Appearance**. Chromaticity zoom supports 256×.

The editor's explicit Edit or text-field focus pins its reading independently
of inspection. Its heading names that reading while inspection metadata can
follow another. Replies are fenced to their original reading and request.
Close/Escape keeps its draft; Cancel edit explicitly requests discard and cannot
erase newer text. Refused text blocks acquisition and Finish until corrected or
explicitly discarded. Panel closure returns focus to an available invoker,
reading action or the workspace.
1. Choose **New batch**, enter its name and notes, then choose a new directory
   in the native dialog. The source selection, authoritative descriptor, mode and options are frozen in manifest schema 5.
   Earlier development manifests are retained as historical evidence; this
   application does not migrate or resume them.
2. Edit the current sample name and notes. Text stays local while typing and is
   published after a 400 ms pause or valid blur. Measure, Calibrate, Repeat,
   Finish, Reload and Close flush pending text first. An unfinished input-method
   composition or a blank exact name must be resolved before continuing. A blank
   numbered name uses the default Sample sequence. Old queued
   text cannot cross a change of batch or sample identity. Python retains and
   publishes the draft. The Measure button names its target.
   Ctrl+Shift+M measures from the workspace, including its analysis tools. The
   shortcut ignores text entry, input-method composition, key repeats and active setup/edit
   panels; a blocked shortcut explains why without submitting acquisition.
   Measure freezes that exact identity/version after saving completes. The
   owner supplies its actual calibration and placement instructions; Qt
   presents native alerts independently of the renderer and preserves the
   HTML instruction dialog in its accessibility tree in that earlier check.
   Other app tasks now use ordinary panels; their native accessibility exposure requires its own check. Each
   response targets the current operation and request identity. Demo instruction
   buttons explicitly confirm simulated calibration or position. Return does
   not accept an instruction automatically; select the confirmation deliberately.
   Escape still cancels the current operation.
   **Number samples** starts with Sample #01. Entering White prepares White 01;
   Yellow has a separate series, and returning to White resumes its next unused
   number. A separated suffix such as White 03 chooses numbering and padding;
   already consumed numbers advance visibly to the next unused target. Turn
   off Number samples for exact labels such as D65 or Panel 2026. Python resolves
   the full name before acquisition. Successful individual readings prepare a
   fresh next sample and clear notes. Failures, cancellations and calibration
   keep the target; a successfully retained reading consumes its number once
   even if saving requires recovery. **Repeat inspected** uses the selected successful
   acquired name/settings/sample identity with blank notes and fresh operation/
   observation identities, leaving the prepared next sample unchanged.
   Normal publication
   shows Saving; failed saving shows recovery. Text limits appear when relevant
   rather than replacing the normal operation status.
3. Measure or Calibrate; Cancel targets the existing operation. Cancellation
   requested, terminal outcome, software stop and instrument state remain
   separate facts. Close is refused while an operation is active: the host
   shows an alert and keeps the window open, and the owner keeps the operation;
   cancel in the workspace, wait for the retained stop and saving facts, then
   close again. Calibration without a reading remains a non-sample outcome. Completed, failed
   and cancelled calibration outcomes remain visible until the next operation starts.
   Native instructions preserve the backend message and display its typed,
   condition-specific setup and available manual guidance. They show the actual
   response budget, starting when Python issues the request; this is not a live
   countdown. Calibration setup and sample placement remain separate steps.
4. Inspect a retained reading. Reading details and the row editor expose its annotations.
   Reading details list the observation's producer warnings, such as simulated
   provenance or an inferred spectral-unit basis, beside the values they qualify.
   Python retains exact editing drafts across reloads. Apply publishes metadata
   without changing native observations. Cancel edit discards only the current
   draft version. Snapshot previews remain bounded; detail queries return exact
   text. Notes are limited to 65,536 Unicode code points; commands separately
   have a 131,072-byte UTF-8 limit.
   An open annotation editor remains bound to its batch and reading independently
   of inspection selection and owner revisions. A successful Cancel does not erase text typed after it was requested. Refused local text blocks acquisition and Finish until corrected or explicitly discarded. Create, Apply and Cancel failures
   appear in the active panel or editor. Refused Cancel keeps local text.
   Unrelated background errors wait until the setup/edit panel closes, without replacing
   current owner status with an older error.
5. Failed saving keeps the result and shows Retry save/Another folder in every
   tool. Retry synchronizes authenticated retained bytes; Another folder uses
   a native chooser and the same owner recovery method. Neither reacquires.
   Per-file synchronization receipts describe observed calls and limitations,
   not storage-device or power-loss qualification.
6. Finish ends measurement and editing after saving and annotation drafts resolve.
   Inspection remains available. **Check saved files** authenticates disk records
   and shows the saved batch state (open, finished or abandoned), verified reading
   counts and per-operation file checks. Failed checks
   show the authentication reason prominently. Command identifiers and raw review
   states are available in File-check details. TI3 companion authentication remains
   a separate export check. This action does not reopen the plotted dataset or resume
   operations. New batch creates a distinct owner after durable Finish.
7. Close, the window close control and Cmd+Q flush pending renderer mutations,
   then obtain a current Python discard summary, including unsaved comparison
   choices. A stale confirmation is refused.
   When that summary has no unresolved work and the owner reports no failed save,
   the confirmation action is **Close**. Its short message confirms saved
   readings/edits and continued saved-file review. Nonzero unresolved work, or a
   save whose publication or synchronization did not complete, retains
   **Close and discard unresolved work** and names the save failure;
   unconfirmed cleanup retains its explicit abandonment or process-exit action.
   The native disclosure also lists unsaved chart state, diagnostic settings and
   captures, diagnostic failures, and automatic files whose preparation failed.
   Unprepared files have no reported payload size or digest. A canonical reading
   save may succeed while auxiliary diagnostic logging remains unresolved.
   A terminal outcome with unconfirmed cleanup offers explicit abandonment and
   process shutdown; it does not claim a device stop. Cmd+R flushes and rebinds.
   If saving the abandonment record is unconfirmed, the host explains that before
   quitting. Atomic publication may already have installed that record even
   when a later synchronization failed; the warning does not claim otherwise.
   A retired publication owner can instead offer **Discard unresolved work and
   quit**, after software stop is confirmed or this owner is explicitly abandoned,
   with publication inactive and no other uncertain acquisition owner. The
   native owner requires the exact current discard token and the admitted,
   completed `process_exit_acknowledged` effect. This memory-only acknowledgement
   does not publish shutdown, close the batch owner or confirm folder cleanup.
   Unconfirmed software stop and device-state warnings remain visible. Direct
   connection cleanup keeps its separate explicit quit confirmation.
   Keeping the application open after that cleanup fails leaves Close available
   for another explicit cleanup attempt, including after the batch is closed or
   process exit has already been acknowledged.

Close and reload refusals remain visible while setup or annotation panels are
open. Identical sample-name hints do not rewrite their live status region.
Interrupted text composition clears on blur, disabling or explicit discard.
Failure alerts use one standard Dismiss button with Return acknowledgement.
Displayed result quantities use six significant figures and chromaticity
coordinates use six decimal places; full retained values
remain available in the original record. Display precision is not an uncertainty
estimate.

The Edit menu supplies standard Undo, Redo, Cut, Copy, Paste and Select All with
platform shortcuts, including Cmd+Shift+Z for Redo on macOS. The View menu supplies page zoom
from 50% to 200%, plus Actual size.
It changes layout and text size, preserving observations and numerical scales.
Window close and Cmd+Q use the guarded discard flow. Process signals, force
quit, crashes and power loss are outside that interactive close contract;
unpublished in-memory work can be lost. Bridge callbacks report control
failures after owner bookkeeping, rather than propagating them through Objective-C.

The bridge accepts closed, size-bounded JSON requests from the top-level bundled
document. Mutation UUIDs permit identical retries after delayed/lost replies;
different content under a UUID is refused. Native file dialogs wait for the
operator without an owner-execution timeout. Rejected text remains local and
must be corrected or explicitly discarded before Apply, Reload or Close. Generation and batch identities
fence obsolete renderer calls. A serial host executor serves bridge requests off
the main UI thread; a Demo load runs on its own worker, so the snapshot poll and
every other owner request keep answering while it loads; acquisition draining and
metadata publication remain with their existing callbacks. HTML displays authoritative snapshots and sends commands,
without a second acquisition or persistence implementation.

Python also retains current view choices for renderer rebind: plotted membership,
preview scale, follow, tool, visibility and expansion. These are ephemeral
presentation choices for this process lifetime, not recovered after process loss.
Comparison starts without a reference. Inspect a reading and explicitly set it
as the reference in Compare; clearing it does not choose another reading. The
reference and explicit Lab-white declaration are saved with the batch, separate
from camera/layout choices. Retained choices use paired operation/observation
identities; assumed whites retain units, scale, observer and declared Y. Queries
bind the saved comparison version and authenticate its chosen source bytes.
Unsaved or unauthenticated context cannot supply quantitative comparison.
The batch folder is visible beside the batch controls. Save failures have a neutral
inverse banner and alert semantics, with recovery actions kept beside the reason.
Operation progress and the last attempt appear beside Measure. Cancelled and
failed attempts report the actual retained reading count; the latest readout
shows Y and its declared unit, with X/Z available in the table's Color/details view.
Named 3D camera presets restore on rebind. A manual orbit is labelled Custom and
keeps its direction when bounds change; free camera orientation is not retained
across a renderer reload and restarts as Oblique with an accurate selector.
[Scientific illustrations](illustrations.md) retain declared approximation and
display limits separately from native observations and numerical diagnostics.

## Verification scope

`tests/test_desktop_session.py` exercises the connected simulated route, duplicate
command/lost-return reconciliation, stale binding refusal, exact annotation
drafts, canonical Finish/review/New batch, analysis identity and bounded requests.
`tests/test_desktop_client.py` also executes the actual renderer and bridge with
the locked jsdom development dependency and an inert owner peer. It interleaves
selection, revisions and queued edits; checks visible dialog refusals, preserved
text, owner-resolved naming, Repeat, saved comparison choices, save progress and
current camera identity. A bounded JSON test actor also connects the actual
JavaScript client to the actual Python DesktopSession through mixed-series
measurements, Repeat, reference/white publication, analysis and Finish/review.
That actor confirms synthetic Demo instructions; it does not test native
instruction presentation or physical operation. Install the
locked desktop-builder dependencies with the npm command above before running
these checks. They do not establish visual layout, WebGL output or native AX.
`tests/test_presentation.py` checks the scientific compositions and original CIE
resource. The default package gate authenticates recursively packaged resources.
Regenerate reference data with `tools/generate_desktop_assets.py`; `--check`
compares generated scientific output and source/renderer manifests without acquisition.
`--kind renderer` regenerates only JavaScript after renderer edits;
`--kind reference` regenerates only scientific data. Host boundary helpers
and generated-source/output drift are exercised by `test_desktop_host.py` and
`test_desktop_client.py`; native alert wiring and accessibility remain separate checks.
An older pre-fix candidate lost Help/Review accessibility exposure with an active
batch. The current renderer replaces those HTML modals with ordinary labelled
panels and keeps the workspace live. Source and DOM checks establish reply
fencing, local error placement, dense table selection and retained column
choices. Native accessibility, keyboard and zoom/resize checks remain a separate
qualification; earlier AX snapshots describe only their original candidate.

Native interaction, file dialogs and keyboard checks are distinct from those
headless tests. A human VoiceOver/IME session, physical display/color management,
physical equipment, storage power loss, and cross-platform native hosting remain
unqualified. Software tests and accessibility-tree exposure do not establish
those forms of validation.

## Demo tools and Help

**Demo tools**, at the bottom of the workspace, toggles a compact toolbar with
**Guided chart demo…**, **Load 40**, **Load 160** and **Stop loading**. The chart
entry opens chart setup in an empty Demo spot batch. Without an open batch, it
first creates a named simulated batch through the normal save-folder chooser,
then opens the existing Classic/SG chart setup. Its source choices are Demo-only;
reflective spot is selected when the chosen descriptor offers it. Existing
readings are never replaced: finish a populated individual batch before starting
a chart Demo. Use **Measure patch** to retain simulated chart readings. Bulk
Load buttons are for individual-reading batches and remain disabled in a chart.
The toolbar stays open across data updates until explicitly hidden.

Loading appends simulated readings to an active packaged Demo
batch through the ordinary Python Measure, instruction-response and save owners.
Existing readings are retained. Loading runs on its own host worker, so the
snapshot poll and every other owner request keep answering during Load 40 and
Load 160. Each load captures its batch, generation and native load identity;
Stop sets that load's Event directly, without queuing behind either worker. The
final snapshot reports completed and retained counts.
Closing requests Stop before waiting for the load and reconciling saved state.
The native helper refuses stale stop identities and non-Demo acquisition routes.

Setup exposes ordinary spot modes and primitive settings from the selected
frontend's descriptor, including XYZ-only, high-resolution, averaging and observed
display-calibration choices when available. Mode-specific settings follow their
declared scope. A collapsed section retains preparation instructions and reasons
for unavailable settings. **Connect instrument**, also available from device
and batch setup, opens the connection panel. The standalone app has already
authenticated its shipped instrument software; development launches configure
their report in **Instrument software details**. Explicitly list connections,
review initialization effects, and initialize one selected device. Listing and
initialization are separate actions. An unidentified
connection requires an explicitly permitted typed model or acknowledged generic
serial identification. Models supporting neither route remain visibly ineligible.
Reinitialization uses
the retained model enum, reported serial and binding policy; a model absent from
the selected report cannot be reopened. **Remove from app** retires a logical
selection after its open batch is finished; it does not claim a persistent worker
was closed or an instrument state was restored. A delayed effect reply blocks
acquisition, Apply and Create until **Check setup result** reconciles its original
command UUID, including after leaving the equipment panel. Pending Argyll and
direct setup show their current stage and keep measurement disabled until the
recorded outcome is terminal. For Argyll, **Cancel connection**, when accepted
before the connection is registered, waits for native initialization to end or
time out and finish its helper shutdown, then prevents adding that connection.
Once a connection is registered, cancellation is refused as too late. A listing
shows **Cancel listing**; cancellation accepted before its candidates are
published discards them, and afterwards it is refused as too late.
It does not confirm that the
instrument is stopped or idle. Direct serial's **Cancel device setup** requests
cooperative cancellation of that exact initialization; cleanup remains separate.
Reload restores pending setup controls without selecting a port or initializing
again.
The existing workspace refresh reconciles the outcome even after the panel closes.
**Use for new batch** keeps the explicit source through setup and refuses visibly
if that selection disappears, instead of choosing another source.

For PR-655 and PR-670, use the Argyll connection workflow above: list instruments,
select the intended instrument and accept its disclosed initialization effects.
Acquisition and native settings use that selected Argyll source. The
[PR reference](argyll-native-controls.md#photo-research) explains settings,
native quantities and limitations.

The panel's **Advanced connection options** offers **Direct serial** for CR-100,
CR-250, CR-300, CS-2000 and CS-2000A. CS uses this direct route. For CR, record a
failure from the equivalent Argyll operation before using direct serial for
diagnosis; the application does not switch backends automatically. Choose a
model, use the native port chooser, inspect
the disclosed initialization effects and explicitly acknowledge them before
initializing. Optional serial assertions and response/measurement allowances
follow the direct owner's catalog. The renderer holds an opaque native port
token, never a port path. Listing ports occurs only through that explicit chooser.
See [direct serial instruments](direct-serial.md) for settings, native quantities
and model-specific initialization effects.

Direct connections retain their host port between operations. **Close connection**
releases that owner's resources after its batch is finished. Reinitialization
requires a confirmed closed binding and a deliberate new port choice; unavailable
connections cannot be selected for a new batch. Application Close first completes
batch/export handling, stops and drains any active Demo load, then attempts direct
cleanup on the existing owner executor. Admitted Argyll listing/initialization and
direct initialization/readback use the existing Demo worker pool, with one pending
setup UUID. The owner executor and session snapshots remain responsive for
reconciliation and exact cancellation. Shutdown requests cancellation through the
shared setup owner. A separate thread waits up to 30 seconds for its actual Future,
without instrument I/O or occupying the owner executor. After the setup runner
ends, direct cleanup uses the existing owner executor. Unsettled setup
keeps the application open for explicit retry; it does not confirm device stop.
Keeping the application open after failed
cleanup leaves both Demo loading and ordinary owner work available.
During cleanup and its decision dialog, new setup and measurement effects are
refused. Failed cleanup offers Keep application open, Retry connection cleanup
or explicit Quit with cleanup unconfirmed. Keeping the app open preserves the
resource owner and worker pools; quitting does not claim that the instrument was
restored or a physical exposure stopped.

For modes advertising correction files, **Choose correction** opens a native
single-file CCMX/CCSS chooser. Python captures and validates the selected material;
the renderer receives only an opaque token and documentary metadata. Create and
current **Apply settings** retain their source, mode, descriptor and draft checks.
Pending choice disables setup actions, while cancellation or refusal preserves
the existing selection and entered settings. **Clear correction** explicitly
returns to the driver's calibration where permitted. Correction types follow
the selected mode's exact descriptor, rather than aggregate device capabilities.

Renderer interleaving tests use an inert recording peer. Host tests exercise the
actual native-route handler with an inert chooser and owner executor. Backend
tests compose the existing `InstrumentBuild` policies and material validation.
These establish software routes; live native choosers, accessibility and physical
equipment operation remain unqualified.

Help presents eight short operator topics: setup, measurement, reading review,
comparison, plots, saved-data inspection and export, automatic saving, and save
recovery. Keyboard shortcuts and scientific sources
expand separately. Colored 3D wires use Python's supplied vertex colors and name
the ideal RGB reference; solid and dashed wires distinguish the two references.
Repeat uses the inspected native reading pair. Table tags name repeat lineage,
shared labels and, for guided-chart readings, the patch coordinate with any known
Classic name (for example **Patch A1 · Dark skin**), while the next sample remains
prepared. These tags come from the bound source, so saved review keeps them.

Automatic file details show TI3 file purpose, retained row counts and current
publication state. Unavailable XYZ, spectra or record kinds retain their owner's
reason, grouped by component and reason with distinct record counts. Ordinary
missing spectra stay in collapsed details; they do not create a save-recovery
alarm or an ineffective Retry. A partial representable TI3 export explicitly says
some values are unavailable while canonical JSON/MAT retain the original data.

Comparison offers optional signed ΔXYZ, Δxy, Δu′v′, ΔLab, chroma and hue columns
through the existing column picker. A collapsed signed summary retains native
XYZ units, per-metric counts and unavailable states supplied by Python. Pair
context refusals appear separately from a valid individual Lab projection.
**Copy full summary** copies current owner ranges, counts, grouped reasons,
declared white and source context to the local native clipboard. It excludes the
reference from compared counts and refuses unavailable or stale comparison input.
The renderer formats these quantities; it calculates no color differences.


## Guided chart controls

Create an empty spot-reading batch, then choose **Batch > Guided chart setup**.
Classic, Digital SG and custom grids share the same measurement, plotting,
annotation and automatic-file owners. A TI2 target is selected through a native
file chooser. Python retains its complete validated source; HTML receives a
bounded token, geometry and source identity. Confirm the actual row/column
mapping shown in the preview before choosing **Use this chart**.

The guide occupies either existing plot slot, preserving the readings table
below. Cell activation inspects; **Target** with the inspected patch's coordinate
(for example **Target B2**) deliberately changes
the next acquisition target. Arrow keys move focus in the displayed orientation.
Rotation preserves canonical cell identities. Target and inspected cells retain
distinct borders, labels and accessible names. Square patch faces, compact dark
gutters and a board surround preserve the physical grid proportions. Column
letters and row numbers rotate with the board without renaming canonical cells.
Classic names supplied by Python supplement the target/inspection coordinates;
unknown names remain absent. Boards up to 140 cells fit the plot without internal
scrolling; patch size follows the available width and height. Solid target,
dashed inspection and dashed black-and-white keyboard-focus frames remain distinct,
and the focus frame stays within the gutter between patches. **Key**
explains the frames and preview markers without occupying another workspace bar.
Axes underline the target column and row (solid)
and the inspected column and row (dashed). On boards up to 48 cells, full cell labels
also appear on the target and inspected patch. Denser boards such as SG keep those
faces clear, because a small face cannot hold a frame, marker and label; there,
cell labels and the inner inspection frame appear only for hover or keyboard focus.
The target keeps its outer border so locating the next physical patch stays immediate.

Measure names the physical target. Successful chart readings advance through the
existing chart owner; retries retain earlier attempts and coverage. Chart controls
provide pause/resume, another pass, per-cell history, original attempt details,
explicit attempt selection and placement/context decisions. Changing settings can
require another pass; **Review changed settings** exposes exact recorded differences.
Finishing incomplete coverage requires explicit confirmation. A paused chart remains
a chart, and ordinary free-sample actions remain refused by Python.
Both Finish controls open the active pass's coverage confirmation, even while
inspecting an earlier pass. Confirmation is bound to that pass and chart revision;
a changed chart requires a fresh review. Finished cells remain locally inspectable
without a mutation. Saved pass and cell inspection survives view refreshes.
History pages and original details are bound to the source, panel opening, pass
and cell. Only one More request runs at a time; superseded or unavailable-source
replies cannot add attempts or restore an old chart view.

**Choose saved chart batch** keeps the selected source folder in the native session.
The UI chooses a source pass and continuation or a new empty pass. It may explicitly
confirm only the earlier known material facts admitted by the shared context owner;
device identity and earlier unknowns cannot be declared. Commands retain exact UUIDs
for reply reconciliation. The same guide can inspect authenticated saved datasets;
saved controls never retarget or mutate acquisition.

Unmeasured Classic and SG cells show a **Nominal location guide** when no qualified
reference preview is available; its diamond marker appears with the coordinate on
hover or keyboard focus. These illustrative faces
use the most frequent RGB8 in a 5 by 5 pixel window at each declared canonical patch
center of the supplied layout images, displayed as nominal CSS sRGB. The SG image is
dithered, so a single center pixel can differ from its patch face. Image encoding, lighting and specimen colorimetry
are unqualified. They help locate patches; they supply no scientific expected
values, measured preview, reference white, placement qualification or export data.
Custom and TI2 charts without reference previews retain neutral faces. Tooltips,
accessible descriptions and **Key** identify each preview kind.

Measured and expected swatches come from the existing Python chart-science owners.
Unmeasured reference previews retain their original channels and use a hollow
marker. A kept reading replaces that face with its available measured preview
and a checkmark. A kept reading with unavailable or pending color uses an opaque
patterned face and its actual reason; it never substitutes nominal or expected color.
While a newer color reply is pending, a kept cell whose selected reading is unchanged
keeps its accepted measured preview, and unmeasured cells keep their reference
preview for the same definition, pass, color space and expected context. A newly
selected reading shows the patterned pending face until its own preview arrives.
A refused color query clears the carried preview; Retry cannot redisplay it while
the source remains unconfirmed.
An earlier selected reading remains visible when a later repeat fails, with a
separate latest-attempt marker. Failed, cancelled, measuring and placement-review
states have shape/text cues and accessible descriptions. The preview does not
detect the physical patch or establish that placement was correct.
Scientific previews retain common reference white Y=100, source identities, observer, native values
and clipping facts. Expected TI2 swatches require all eight explicit reference-context
fields. Missing or incompatible context leaves scientific expected values unavailable;
an unmeasured Classic or SG cell may still show the separate nominal location guide.
Preset geometry alone supplies no authenticated expected color values. Swatch labels
use a consistent background for contrast; they do not calculate new measurements.
These connected Demo and DOM checks establish software behavior only. Native layout,
accessibility, chooser interaction, installed-app lifetimes and hardware remain separate
verification scopes. The nominal resource is reproduced by
`tools/generate_chart_guides.py` from exact-hash supplied images. The renderer
manifest binds its bytes in both checkout and installed-package freshness checks.


## Retained record files

**Batch > Open record file** opens an original operation JSON/MAT or color
observation JSON/MAT/CSV. Choose the contents explicitly before the native file
chooser. Imported data are read-only; they keep their original device and native
identities. Failed, cancelled and zero-reading operations show their original
outcome independently of source authentication. **Return to live** restores the
retained batch without changing its readings or comparison settings.

**Export data > Original record** saves the imported original or the color
observation named in the panel. That target remains fixed while inspecting other
rows. JSON/MAT preserve native metadata; CSV stores one complete color observation.
Python supplies availability and refusal reasons. A qualified native SPECT export
requires two native-selected, distinct filenames in the same directory: SPECT and
its canonical JSON companion. Cancelling either chooser publishes neither file.
Publication itself is not an atomic pair; failed publication keeps explicit
recovery. MAT and other canonical retries publish exact prepared bytes without
re-serializing. Recovery and Close dispatch the returned native or analysis retry
method. Successful original publication reports optional TI3 file states and
reasons separately; missing spectra do not create a saving alarm.
Changing the target, format or source revision clears the panel's earlier file
details. Late receipts update that panel only while its captured selection still
matches; companion failures remain available in the global recovery disclosure.

Imports also use the source-aware analysis exporter described in
[the analysis workspace](analysis-workspace.md). They have no whole-batch archive
or writable chart/acquisition owner. Renderer, Session and inert native-handler
checks qualify these software routes; live chooser interaction, layout and native
accessibility still require a native desktop check.

## Scientific calculations and qualified references

**Analysis > Color > Scientific calculations** opens explicit forms for
corresponding XYZ using full linear Bradford adaptation, XYZ from a native
spectrum, reflectance from a measured reference, and reflectance under a different
illuminant. Choose checked or all displayed readings. Each calculation uses the
existing [qualified scientific owners](analysis-workspace.md#explicit-derived-science)
and preserves native observations. Results show derived XYZ, Lab or reflectance
separately, including independent unavailable reasons. Source/method details expand
on demand. Changed inputs, checked membership or scientific source revision
invalidate the displayed calculation; late replies cannot restore it.

Adaptation requires two explicit retained or assumed standard whites and compatible
native XYZ units, scale and observer. Spectral forms require the declared calculation
observer and standard-air wavelength convention. Reflectance and measured-reference
forms additionally require explicit nonfluorescent and matching-setup declarations;
an ideal reference is an unchecked opt-in assumption. Named D50/D65/A target SPDs
use the authenticated CIE owner. Supplied SPD or characterization files use the
native JSON chooser and the current four-field Spectrum document:
`wavelengths_nm`, `values`, `quantity` and `unit`. Illuminants declare
`relative_spectral_power` with unit `1`; characterizations declare `reflectance`
with unit `1`. The session captures and rechecks the selected bytes and source
identity through opaque tokens. It does not infer units, resample native readings
or interpret arbitrary CSV files. **Export data** can explicitly include the
current calculation, applied by Python to the exported readings. Original-record
and whole-batch exports preserve their own existing scope.

**Batch > Native diagnostics** sets the subsequent operation's separate capture
level and retained-byte limit while idle. Off is the default; unsupported sources
show their descriptor's reason and offer no positive level. Logs save automatically
with the batch; at the byte limit the beginning and end remain available. Optical
settings and chart material context do not acquire diagnostic options.

An optional diagnostic-log failure appears separately from reading-save
recovery. Its details distinguish capture from publication failures and retain
the owner's original reasons. The operation count uses the union of both
categories: one operation failing in both categories counts once. Moving primary
readings to another folder does not clear a persistent log-capture warning or
disable Measure after primary saving succeeds. The warning clears only when
the owner's corresponding failure maps clear, and names its live batch even
while another source is being reviewed.

**Chart controls > Use measured pass as reference** names the source pass and
requires specimen, edition, geometry, backing, lighting, declarer, matched
conditions and explicit CIE76 allowance/reference/measurement bounds. Bounds may
be a scalar or one value per canonical cell; no uncertainty limit is filled in.
When the current pass already has attempts or a frozen reference, **Start empty
comparison pass** explicitly preserves it and creates empty coverage. **Freeze
measured reference** then binds the qualified source to that empty pass. The
reference does not create another pass automatically. Missing source cells stay
unavailable; changed chart revisions refuse the old form.

Connected renderer and native-boundary checks use retained Demo inputs and inert
files. They establish software behavior, not visual layout, native accessibility,
physical calibration, specimen matching or instrument accuracy.

## Inspect activity and native outcomes

**Analysis > Activity** opens a source-bound outcome list. Search the original
target, intent-note preview or failure reason; filter completed, failed, cancelled,
in-progress or imported-observation entries. **Follow latest matching outcome**
tracks new work. Selecting an outcome turns Follow off and preserves that
inspection when later operations arrive. Outcome buttons retain keyboard focus
across roster updates. Long reasons have a three-line preview and complete text
in the selected details.

Each outcome retains native status separately from saving and source review.
Failed, cancelled, calibration and configuration operations remain visible even
when they have no readings. Saved batches and imported records show their own
original outcomes; a standalone observation has no invented terminal status or
command receipt. Source-qualified inherited outcomes retain their original segment
and manifest identity. A parsed result excluded by source review remains diagnostic
evidence; its reported reading count does not create available measurements.
Without a parsed result, preparing or in-progress work and unknown terminal
outcomes show the reported reading count as unavailable. Missing or unparsed
results also remain unavailable. Parsed results retain
their exact count, including zero for failed or cancelled results without readings.
Imported observations report one observation. The available table reading count
remains separate in details and copied summaries.

Details expose exact applied annotations, unapplied drafts, native failure stage
and code, original quantities and unavailable coordinate reasons, stop/instrument
facts and raw terminal recovery. **Show reading in table** is available only for
an exact reading in the displayed dataset and clears both table filters.
**Copy outcome summary** copies current source identity, outcome facts, available
reading identities and full applied notes through the native clipboard route.
Oversized summaries refuse explicitly rather than truncating notes.

Native output and diagnostic originals expand on demand. Diagnostic text previews
keep the declared original stream offsets; exact bytes, capture identity,
truncation and errors remain in their source details. Live application events are
limited to the selected operation within the latest 32 batch events. Saved/imported
records do not reconstruct a missing application timeline; their details and copied
summary say it is unavailable. Source/revision or panel
changes fence late replies and disable old jumps/copy during refresh. These
read-only routes do not acquire, recalibrate or change saved records.
