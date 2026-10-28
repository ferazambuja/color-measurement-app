# Native CR and PR control adapters

These C adapters operate on an already initialized Argyll instrument in its
existing owned lifetime. They use the contribution's protocol engine and driver
lock. They do not open a second serial link, create calibration data, issue
arbitrary commands or acquire a measurement as part of a settings operation.
The ordinary Argyll interface remains the owner for modes and stored calibration
selectors. The application overlay adds the manufacturer controls that interface
does not represent.

The implementations are `tools/native_argyll/cr250_control.c` and
`tools/native_argyll/pr655_control.c`. Their headers define the exact request and
report structures. The shared native session, Python client and WebUI use these
adapters through the existing instrument lifetime. This page describes the C
component contract; the [generic frontend](generic-frontend.md#native-settings-requests-and-retained-recovery)
describes the consumer contract. Software checks use simulated serial actors;
physical behavior remains unqualified.

## Calling contract

`cm_cr250_control_read` and `cm_pr655_control_read` obtain current settings and
supported controls. The corresponding `control_apply` functions validate a
complete proposed target before the first persistent setting command. Each takes
an initialized `inst *`, a caller-owned report and a positive finite allowance in
seconds. Apply also takes a typed request. A `present` bit distinguishes a
requested zero from an absent field. An unavailable current value remains absent;
it never becomes a zero-valued observation.

The caller must serialize operations and own the instrument lifetime. Apply
honors accepted native exposure and synchronization intent during later reads.
A subsequent ordinary Argyll mode request restores that mode's recipe. Apply
controls after negotiating the intended Argyll mode. A settings read does not
change that recipe.

The report distinguishes planned changes, attempted writes, successful
acknowledgements and independently confirmed readback. A multi-command request
is not a device transaction: a failure can leave earlier settings changed. There
is no automatic retry, measurement or device rollback. Retain the failure report
and inspect the physical setup and current settings before recovery.

## Colorimetry Research

The shared `cr250` family implementation accepts CR-100, CR-250 and CR-300. Its
17 request fields are `mode`, `exposureMode`, `exposureMs`, `rangeMode`, `range`,
`exposureMultiplier`, `syncMode`, `syncFrequencyHz`, `speed`, `accessory`,
`filter1`, `filter2`, `filter3`, `userCalibrationMode`, `matrixId`, `matchId` and
`maxAutoExposureMs`. Availability comes from verified model/firmware policy and
fresh counted inventories or positive, ordered numeric bounds.

Native acquisition requires the driver's verified firmware and Radiance setup.
The [CR route guide](direct-serial.md#choosing-a-cr-acquisition-route) explains
when the direct frontend is needed instead. A settings report does not establish
that a particular setup meets the native acquisition requirements.

Mode is an observed CR-100 fact. The internal acquisition recipe may request
only its reported Colorimeter choice. Range controls and Matrix profiles are
CR-100-specific; Matrix also requires the verified colorimeter instrument type.
Speed applies only to CR-250/300. Aperture is read-only native text.

Complete optional-setting errors, warnings, invalid values and fully consumed
malformed inventories leave the affected controls unavailable, with their
original replies retained. They do not prevent an unrelated scalar change.
An incomplete response, transport failure or exhausted allowance still fails the
operation. A dependent change still needs its required current values, choices
and bounds; unavailable data cannot authorize a target. An unrelated apply
preserves earlier explicit exposure, synchronization and correction intent when
its current readback is unavailable.

Fixed exposure requires an established time within reported limits. CR-100 also
requires Fixed Range D. A synchronization frequency requires Manual mode.
Accessory-specific Matrix selection must follow a separate accessory change
and fresh inventory read. Matrix/Match use the driver's actual Argyll selector
owner without releasing the instrument lock. Active filter IDs must be distinct.
The adapter does not invent a universal zero-valued filter-clearing choice.

Every requested argument must have an exact representation within the engine's
16-character unsigned decimal contract. Values are never silently rounded.
Unrepresentable values refuse before any setting write. The allowance reserves
planned setters, readbacks, profile queries and the final settings report before
the first write.

`maxAutoExposureMs` has no documented current-setting readback. Its setter may
be acknowledged, but its current and confirmed fields remain absent.
`RM MaxAutoExposure` is last-capture metadata and cannot confirm that setter.
After a failed settings application, measurement remains blocked for attempted
fields and any requested field whose final readback proves drift, even if that
field initially needed no setter. Recovery requires a successful apply that
explicitly covers and verifies every blocked field; maximum Auto exposure uses
its acknowledgement because no current readback exists. Read-only refresh, an
empty request and partial recovery cannot clear that block. A new identified
instrument lifetime starts fresh without undoing device settings.

`cm_cr250_control_report_event` emits one `native_control` summary plus bounded
`native_control_row`, `native_control_choice`, `native_control_reply` and
`native_raw` events.
The summary retains initial semantic values, current values, requested values,
all four write/readback masks and the pending recovery mask. Replies retain the
exact parsed status, code, context and value. Their phase is 0 for the initial
report, 1 for apply and selector queries, or 2 for the final report. A reply with
`complete=false` never supplies a fabricated status. The separately counted raw
exchanges preserve received bytes as hexadecimal, including malformed text,
non-ASCII bytes, incomplete table rows and late input. Command text, framing
completion and the returned error remain separate. A complete frame can still
contain an instrument error or unusable payload; it is not a successful setting.

The host admits 256 inventory rows, 4,096 printable ASCII bytes per line and
1 MiB of received bytes per exchange. IDs are exact nonnegative integers through
2^53−1. These are application resource limits, not manufacturer capacities.
Each control or capture trace is bounded to 256 exchanges and 4 MiB, with
1,024-byte chunks and explicit `trace_overflow` on retention failure. Overflow refuses the
operation; a bounded trace does not promise to contain every byte from the device.
On CR the retention failure becomes the operation's result even when the exchange it
could not keep carried an instrument error, so no optional-query rule can read it as
unavailable metadata. PR keeps such an instrument error primary instead (below).
Parsed choices require a complete valid inventory. A partially received table
is retained as bytes and never becomes a shorter successful choice list.

Zero-initialize a `cm_cr250_control_report` before its first call. Read/apply
releases its old choice allocations when reusing it. Each row owns its choice
snapshot; consume or serialize the report, then call
`cm_cr250_control_report_dispose` on both success and failure. Its raw exchanges
borrow instrument-owned storage until the next control operation, acquisition
or instrument destruction. Consume those bytes before that boundary. The
[PR report](#photo-research) has its own borrowed-inventory lifetime.

Acquisition uses the same engine and `native_raw` contract. Counted `native_record` events retain
matched and range records without placing all their text in one large event.
A verified capture-start identity can attribute already acquired originals after
a later transport failure invalidates the current link identity. It cannot
reopen that link or authorize successful publication.

Raw reports can retain a complete finite header and 1–10,000 signed samples.
A usable CR spectrum needs at least two samples and a valid wavelength axis.
Retention does not assign density units or resample the grid. Argyll spectral
publication still requires 4–601 samples, the driver's 200–1,100 nm domain, a step
of at least 0.1 nm and a consistent same-capture unit check. For example, a
complete 1,000-sample native spectrum can be saved with unverified units and a
failed requested outcome; it does not become an Argyll spectrum.

A derived wavelength axis must be positive and strictly increasing, with a
consistent reported grid. A single sample or unusable finite header geometry
leaves the header and sample vector in native facts with a mapping refusal.
Independently parsed XYZ can still be saved with unverified units; no replacement
wavelengths or successful spectrum are invented. Incomplete or unparseable bodies
remain original bytes. See the
[spectral consistency method](science.md#cr-250-spectral-consistency-check).

The primary definitions are in the manufacturer's
[Remote Communication manual 1.36](https://www.colorimetryresearch.com/downloads),
sections 4.2.1, 4.3.2 and 4.3.3, with native radiometric and spectral reports in
4.3.1.36–4.3.1.37. CR-100's Fixed Range D dependency comes from the
CRI App manual 1.5.10, section 5.8. None of these facts establishes which optics
are physically fitted or whether a stored calibration is suitable.

## Photo Research

Both PR-655 and PR-670 use the `pr655` family implementation. Exact D111 model
and D120 grid checks distinguish them. PR-655 returns 101 wavelengths from
380–780 nm at 4 nm increments; PR-670 returns 201 at 2 nm increments. No grid
is resampled.

Use emission spot/telephoto modes for luminance and spectral radiance, or
`ambient_spot` for illuminance and spectral irradiance. Ambient describes light
incident at a measurement plane. Its configured primary and every active add-on
must report Illuminance/Irradiance; emission requires Luminance/Radiance instead.
These reports do not verify the fitted receiver, its placement or calibration.
The manufacturer's illuminance discussion is on printed pages 47–49.

Published XYZ and spectra require the reported 2° observer. Settings can retain
or request 10°, but that setup refuses publication without an automatic reset.
Parsed originals can remain in the failed reading with unverified units.

Initialization sends Q and PHOTO on the explicitly selected serial port before
checking the exact model. PHOTO enters remote mode and disables the touch screen.
The shared description policy reports `initialization_enters_remote_mode` for
both models. It requires acceptance of that particular disclosed effect before
construction; neither model is classified as harmless initialization. A clean
close sends Q to leave remote mode. An uncertain exchange forbids cleanup writes
and can leave the touch screen disabled until deliberate device recovery.

During initialization only, the engine drains leftover input before and after
Q. It counts the discarded bytes under a combined 1 MiB limit; both drains share
the original opening deadline. PHOTO and the subsequent model query still need
their exact replies. Later exchanges retain unexpected bytes and refuse uncertain
framing rather than clearing it to continue.

The 15 request fields are `exposureMilliseconds`, `cycles`, `syncMode`,
`syncFrequencyHz`, `primaryAccessoryId`, `addon1Id`, `addon2Id`, `addon3Id`,
`apertureId`, `sensitivityMode`, `smartDark`, `photometricUnits`,
`instrumentObserver`, `shutterMode` and `speedModeCode`.

| Control | PR-655 | PR-670 |
| --- | --- | --- |
| Fixed exposure | 3–6,000 ms | 6–6,000 ms Standard; up to 30,000 ms Extended |
| Aperture, sensitivity and Smart Dark | Model-specific requests refused | Reported SF aperture IDs, SH0/1 and SD0/1 |
| Speed | Refused | SG0–3; request and ACK retained |

For Fixed exposure in emission spot/telephoto, select a nonadaptive mode.
An adaptive emission request with reported Fixed exposure refuses before any
setter or exposure. `ambient_spot` honors the reported Fixed or Adaptive exposure
under the same timing budget; it has no adaptive/nonadaptive modifier.
`exposureMilliseconds=0` explicitly requests Adaptive exposure; acquisition does
not silently apply it over an operator's Fixed setting. An unknown exposure mode
cannot supply the required capture budget.

PR-670 primary-accessory selection can automatically change aperture. The
manual's printed page 46 specifies this behavior for FP-670, selecting the
largest system-configured aperture. The adapter permits aperture collateral
change after SP only when the request explicitly includes `apertureId`. It
refreshes D117, requires both observed and requested IDs in that unchanged
inventory, and preserves all other material setup checks. It does not infer the
largest aperture from an ID or name. If the fresh setup already has the target
aperture, it confirms that observation without sending SF or inventing its ACK.
Otherwise SF follows, including restoration when the target matched the initial
aperture before SP changed it. The allowance reserves this refresh and any
additional SF before the first setter.

An observed auto change immediately invalidates earlier aperture confirmation.
If later verification fails, the aperture joins recovery debt even when no SF
was attempted. Aperture drift first observed during final verification creates
the same recovery debt. Without an explicit aperture target, collateral change
refuses and retains that debt. Later setup readbacks remove any earlier
confirmation that no longer matches the current values, including when they become
unavailable. An observed change of any explicitly confirmed field also creates
recovery debt for that field. An unavailable readback does not by itself prove
that every requested setting changed. Attempted and acknowledged writes retain
their separate meanings.

The manual's printed page 109 has conflicting SG prose and numeric labels.
`speedModeCode` therefore exposes the documented codes without claiming a
speed/Gain mapping. D601 Gain and its D602 description remain separate opaque
native facts in `native_control.setup`. SG acknowledgement does not establish a
current or confirmed speed code. SG may change Gain and its description; any
other unexpected material setup change refuses and quarantines the link.
Shutter likewise has no documented current report, so ACK is separate from
confirmation.

The native application control accepts integer User synchronization frequencies
of 20–400 Hz. The ordinary Argyll refresh method accepts only 20–150 Hz, the
intersection of Argyll's generic 5–150 Hz contract and the device's minimum.
Configured frequency and measured last-capture synchronization remain separate.

PR control events retain setup, accessory/aperture inventories and bounded raw
hex chunks, including malformed input. Input found before a request, late or
stale, belongs to that request's exchange, so a stale-input refusal retains the
bytes that caused it under the command it stopped. Exposure, native
XYZ/spectrum, metadata, unit checks and publication remain separate capture
facts.

The host admits up to 1,024 rows, 4,096 bytes per printable ASCII line or text
payload, 1 MiB per reply, and 4 MiB of raw trace across at most 256 exchanges.
These are resource limits rather than manufacturer limits. Optical IDs retain
exact integers through 2^53−1. Raw replies and inventory snapshots use bounded
heap ownership. Returned C control reports borrow their instrument's retained
inventory snapshot until its next control operation or destruction; the caller
owns only the report container. Inventory availability and native errors remain
separate from known empty lists. Optical setters require a known matching ID.

Trace exhaustion fails the operation. If its response was fully framed and the
link remains synchronized, deadline-eligible B100 recovery and Q closure remain
possible. An existing native error keeps its primary result code, with trace
overflow recorded separately. Optional metadata availability follows its normal
rules, but retention failure during an optional query cannot authorize a later
setter or turn a failed capture into success. Already complete, validated XYZ or
spectral samples remain available as failed originals after this host-only
failure; they do not become an accepted Argyll patch. Malformed, incomplete or
surplus input retains its framing refusal.

The spectral-density interpretation derives from the manual's radiance or
irradiance unit and
its definition of total as the sum of spectral data times wavelength increment.
Agreement with that total checks the reported samples' internal coherence;
only a discriminating D2 luminance or illuminance comparison can establish the
driver's
`consistent` verdict and support an inferred native unit. Power-only or dim
agreement remains `inconclusive`. That verdict does not erase the manual-defined
publication basis; gross disagreement still refuses. Native values, driver
mapping and optical accuracy remain separate claims.

Ambient publishes XYZ in lux and spectral density in mW/(m² nm). Metric native
XYZ needs no scaling; English footcandles use the driver's conversion to lux.
This is a different quantity from emission's cd/m² and mW/(m² sr nm), with no
assumed solid-angle conversion. See the [worked unit examples](science.md#pr-655pr-670-native-quantities-and-publication-model).
Operation JSON/MAT and observation CSV retain ambient readings. The
[TI3 projection](data-format.md#quantities-and-the-intended-reader) does not support
ambient XYZ or spectral irradiance; those values remain in the source record.

The primary definitions are in Photo Research's *PR-655/PR-670 User's Manual*
(2009), command reference D111/D120/D601/D602 and SF/SH/SD/SG. Instrument
observers and accessory settings are device facts; they do not replace the
application's independently declared scientific conventions.
