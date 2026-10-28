# Direct serial instruments

The direct CR/CS producer uses one shared byte channel and the ordinary
polling, observation, operation-result and persistence owners. It is separate
from the Argyll instrument interface and does not advertise Argyll capabilities.
All current validation is software-only over scripted transports. No real
equipment, OS serial port or physical calibration has been exercised.
The host-close control also exercises pySerial's actual POSIX close method over
inert test operands with `os.close` substituted; it opens and closes no OS handle.

Python PR-655 and PR-670 acquisition uses the [shared Argyll API](instrument.md)
and [native settings adapter](argyll-native-controls.md#photo-research). Original
native replies, identity and capture context remain separate from the
[qualified XYZ and spectral publication model](science.md#pr-655pr-670-native-quantities-and-publication-model).
Geometry, observer and unit-policy refusals remain part of that acquisition path.
Saved observations keep their original backend, quantities, units and raw context.
A native PR binding cannot supply facts absent from an earlier saved reading;
[chart continuation](chart-context.md) still checks the original identity and
material conditions.

## Choosing a CR acquisition route

Use the shared Argyll route for ordinary CR acquisition. Direct CR remains
implemented for an explicitly selected diagnostic fallback after retaining a
failure from the equivalent Argyll operation. Keep that failure and the direct
result with their actual backend identities; a direct result does not establish
an Argyll pass. The application does not retry through another backend
automatically. Changing backends must not bypass a legitimate firmware, optics,
units or uncertain-stop refusal.

The implemented routes have different acquisition rules and retained quantities:

The [native CR driver](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250.c)
requires verified model, serial and instrument type, firmware 1.17 or later for
CR-100, and 1.26 or later for CR-250/300. These are this driver's command-policy
requirements. Before measurement, it requires a unique reported Radiance
accessory and distinct active filters with reported Radiance types. Its ordinary
Argyll mode recipe also controls observer, synchronization and correction intent;
explicit [native settings](argyll-native-controls.md#colorimetry-research) supply
supported overrides. A refused setup can stop before any exposure, so there may
be no native measurement to save.

The direct frontend can retain native CR-250/300 spectra reported as radiance,
irradiance, radiant intensity or radiant flux. The radiometric total and its
quantity type remain separate from the spectrum's unverified density units.
It gates individual settings by reported firmware, without imposing those Argyll
firmware floors. Unsupported settings remain unavailable. In its request
options, `automatic=false` preserves the existing acquisition recipe except for
explicit setting overrides; the default is `true`. Review retained capture
metadata for optics, correction and sync. Unavailable facts and an undeclared
observer remain unknown.
This route does not apply the native driver's Radiance-only qualification.

These broader direct capabilities remain available for diagnostic comparison;
they do not make the routes interchangeable. Compare the model, serial, settings
and calibration context with the retained Argyll failure. Raw
retention in either route does not establish calibrated units, fitted optics or
physical accuracy. The manufacturer defines the radiometric type and spectral
report in *Remote Communication* 1.36, §§4.3.1.36–4.3.1.37, available from its
[downloads page](https://www.colorimetryresearch.com/downloads).

## Explicit connection and native acquisition

[`DirectSerialInstrument`](../src/color_measurement/serial_instruments.py)
supports explicitly selected CR-100, CR-250, CR-300, CS-2000
and CS-2000A models. Construction and import are inert. `connect()` opens only
the selected path, identifies the expected model and checks an optional reported
serial assertion. Physical opening requires pySerial and separately authorized
hardware scope. Injected transports carry simulated provenance.

CR startup reads model, serial and firmware. CS startup enters remote mode,
checks standard model/variation/serial, reads the original backlight, turns it
off and disables the measurement switch. CS close can restore the backlight and
leave remote mode; it does not establish restoration of the measurement switch.
These effects remain disclosed in `direct_serial_catalog()` without port access.
`initialization_cancel` accepts a caller-owned event with `is_set()`. The same
event governs connection and the first setup settings readback through the shared
channel, then detaches; acquisition uses its ordinary operation event. A pre-open
cancel opens nothing. Incomplete writes or reads still quarantine the stream.
Cleanup detaches the cancellation event and restores only a synchronized, idle
connection. Optional metadata handling never swallows a cancellation.
All current direct routes use a fixed 115200 baud, 8 data bits, no parity and one
stop bit. The catalog's `binding_policy` declares that format. Selection does not
negotiate or search baud rates; this policy has no physical-device qualification.

`read_settings()` makes explicit independent current-setting queries.
CR reads aperture only for identified firmware 1.04 or later; an unsupported
reply remains unavailable. Reported numeric limits must be positive and ordered
before they can authorize a setting target.
After a complete RC bound frame, invalid, missing or unsupported numeric limits
disable their dependent numeric controls with null usable bounds and an explicit
reason; the original replies remain in exchange receipts. A zero/fractional
ExposureX bound follows this same rule. Exposure limits are queried once and
govern both fixed exposure and maximum automatic exposure. No bound is guessed
or clamped. A normal automatic recipe can still run using independently reported
choices when it needs no unavailable numeric control. Fixed-exposure and Manual
sync mode changes must also satisfy their numeric dependencies before any setter.
Complete optional RC/RS instrument errors and complete unparsed payloads remain
in `unavailable_metadata`, including their code and exchange. Their dependent
controls cannot authorize unavailable targets. A counted list must declare a valid
bounded count and deliver its complete body before payload interpretation.
Framing, identity, transport and acquisition errors remain fatal.
`apply_settings(changes, automatic=False)` validates the complete target through
the existing pure planners, runs any correction-name preflight, sends ordered
setters once and checks independent readback. Receipts keep requested recipe,
every coupled transmitted target,
before/after reports, acknowledgements and partial failure evidence separate.
CR's maximum Auto exposure remains an acknowledgement-only setting.
Failed setting application prevents acquisition until uncertain intent
is reapplied or the connection is explicitly replaced. No rollback is claimed.

Recovery admission uses complete transmitted targets. No Sync and Normal/Fast
commands can establish protocol-defined unavailable frequency or interval without
an invalid null scalar override. Unrelated partial targets cannot clear uncertain
groups. Deliberately requested groups retain their complete verification target
even when fresh readback already matches and no setter is needed.

The frontend uses CR M/RM or CS's two-phase MEAS followed by MEDR.
CR retains native XYZ with unverified scale/units; its radiometric total does not
qualify spectral density units. CS preserves native XYZ, the 401-bin spectral radiance and separate
MEDR capture conditions. No frontend color calculation or substitute spectrum is
added.

CR also retains independent last-capture RM metadata in `capture_settings` and
`capture_responses`: actual exposure and its mode/multiplier, range, sync,
instrument mode, warnings, optics and correction identities. Responses retain
native success/error codes and interpreted values with declared ms/Hz units.
`NA`, unsupported `-500`, other instrument errors and unparsed payloads stay
distinct, with null values. Sync frequency is queried only for a known active
sync mode; legacy matrix and Matrix/Match identities follow the reported
calibration mode. Spectral-only Speed/CMF queries do not run on CR-100.
Current RS settings and requested recipes remain separate from these capture
facts. Malformed optional scalar payloads retain the raw reply without losing a
reading. A later framing/transport failure fails the operation and preserves its
already complete native capture and confirmed original exchanges.

A completely framed nonbusy measurement error ends exposure ownership without
inventing a reading. The persistent connection remains available, and explicit
Close can perform its normal restoration. CS ER00/ER02 and MEAS ER17
still leave exposure state uncertain and forbid restoration writes. A malformed,
incomplete or surplus response never establishes idle state. Successful CR M and
CS MEAS completion end exposure before later native data queries.

CR spectral readback includes the Remote Communication 1.36 p.35 processing
guard: the producer waits 200 ms after the declared samples before completing
the exchange or sending another command. Late surplus samples remain a framing
failure rather than becoming a later command's response.

Response and measurement timeouts are explicit finite host allowances, capped
at 600 seconds. Response defaults are 10 seconds for CS and 5 for CR;
acquisition defaults to 300 seconds. Explicit response allowances override
the family default.
CS requires at least `max(35, response_timeout) + 242 + response_timeout` seconds
before opening (287 seconds with the CS default): 35 is the attributed direct-driver
initial-reply allowance policy;
242 is the vendor's maximum announced exposure. CR has no established
worst-case exposure floor; its catalogue says so. The default allowance does
not promise physical completion. Each transaction retains at most 256 exchanges
and 4 MiB of confirmed sent/received byte originals; a shrinking response cap
enforces that aggregate bound before another exchange.

## Shared frontend and chart facts

[`DirectSerialFrontend(instrument, settings)`](../src/color_measurement/serial_frontend.py)
binds an identified connection and its cached settings. `describe_json()` is
inert and exposes `direct_serial_instrument_v1`, ordinary `emissive_spot`,
`spot` and `mode_snapshot`, retained patch events and controls from
`serial_settings.direct_settings_options`. Null means no explicit setting
override; `automatic` defaults to true for acquisition. A settings query does
not apply that recipe or any overrides.

`prepare(request_json, response_timeout=120)` validates the exact shared request
envelope without I/O. `dispatch()` takes the shared ownership lease. A placement
prompt precedes settings and exposure. Polling, cancellation, patch retention,
terminal recovery and canonical records reuse `_PolledOperation`. Native reply
bytes, identity, setting receipts, capture metadata and cleanup facts survive
in ordinary records. Preparation and pre-dispatch cancellation send no bytes.

Acquisition keeps the original descriptor stable. An explicit `mode_snapshot`
refresh updates only changed material settings, excluding volatile exchange
timestamps. An active reading does not change the capability digest; the shared
operation lease refuses overlap. Closed or quarantined connections become
unavailable. An uncertain exposure forbids remote/backlight cleanup writes and
its terminal cleanup attempts host port closure.
A CS backlight change that cleanup cannot restore remains in the caller-owned
`unrestored_backlight` session ledger, keyed by verified model and reported serial.
The desktop setup owns one ledger for the application session and passes it to
every direct connection and reopen.
The ledger accepts only those CS model/serial keys and pairs of integer 0/1 flags.
Validation and any serial assertion precede recovery effects. A later connection
that still reads the measurement flag off keeps the retained original target and
the currently reported idle flag until a restoration acknowledgement confirms it.
Failed or repeated startup does not consume the debt; host release also works
before the public device identity has been constructed. A genuine manual switch-on
reported by BALR supersedes the previous change. If the application quits first,
restore the measurement backlight at the instrument; no disk journal is claimed.
Descriptor `current` values belong to the explicit binding/settings snapshot;
they do not claim live settings after each spot override. Each spot's settings
receipt retains its independent actual readback. Refresh with `mode_snapshot`
when a new descriptor snapshot is needed.
Host closure does not prove physical exposure cancellation. A failed host close
retains the quarantined instrument, host port and registered path. The operation
reports its worker's actual stopped state separately from `port_closed=false`.
No new acquisition is available on that failed channel. Once the worker has
stopped, an explicit public close can retry host release; an active worker keeps
its I/O lease and refuses close. Failed terminal observations remain unchanged.

Post-read documentary hook failures use the shared primitive JSON fallback before
retaining native data. Ordinary errors and controls produce a failed terminal
with their original cause and proven stop facts, rather than losing a complete
native quantity or publishing a completed outcome without its originals.
An acquired transport is retained before fallible channel construction. Failed
construction closes only that host port; an unconfirmed close remains a visible
cleanup-required binding even when no channel or frontend was registered.

The chart-context owner attributes direct identity to `direct_identity_json`,
independent settings to `direct_settings_json` and CS capture conditions to
`direct_capture_json`. Software recipe acknowledgement, hardware readback and
requested values remain distinct. Direct acquisition mode is acknowledged route
metadata. Unknown calibration, CR spectral units and CR XYZ scale stay unknown
comparison facts. No Argyll `native_facts_json` is invented. Context derivation
binds the exact descriptor, request, terminal result and original observation.

## Byte transport

[`serial_transport`](../src/color_measurement/serial_transport.py) owns bounded
byte writes and line reads for direct instrument protocols. It does not identify
an instrument, start an exposure, assign scientific units or register a frontend.
The caller supplies model commands and validates their complete responses.

`SerialChannel(transport, *, clock=time, cancel=None, limit_bytes=1048576)` is
inert. Its transport must already be owned by the caller. The separate
`open_serial_port(port, *, baudrate, write_timeout)` opens exactly the selected
8N1 path, without discovery or fallback selection. It imports pySerial only on
that explicit call, uses nonblocking reads, finite write timeouts and exclusive
access on POSIX. Physical operation requires a separately authorized session.

`begin(command, *, timeout)` sends the command once with a fixed CRLF terminator.
Each successful write must report its exact byte count. A partial write,
interruption or uncertain transport outcome quarantines the channel; no retry
or cleanup command follows it. Commands are bounded printable ASCII, with a
maximum of 128 characters.
Cancellation before any attempted transport write preserves the synchronized
stream. An attempted write with no confirmed bytes still quarantines it because
delivery is unknown.

`line(deadline)` uses the caller's single monotonic deadline.
CR, LF and CRLF replies may arrive in fragments. The channel returns each
line's content unchanged, including a command echo; it skips nothing. The CR and
CS reply policies accept no echo line, so CR replies require the instrument's
ECHO setting off. Temporary empty buffers do not finish replies. Lines must be
ASCII and at most 4096 bytes; the complete response has an independent byte cap
from 1 to 1048576 bytes. `complete()` releases the exchange only after the policy
has validated all its expected lines and no available surplus bytes remain.
`quarantine()` records a policy's malformed-response refusal. A failed exchange
cannot become ready by accepting a later response.

`facts()` returns the original command, confirmed transmitted bytes and received
bytes as hexadecimal, plus write, stream and port state. An overflow retains the
exact bounded received prefix. It does not claim that unconfirmed bytes were
transmitted or that the entire reply was retained. The protocol owner must retain
each exchange's facts before starting another exchange.

A validated command attempt starts separate facts before the idle check. A
cancelled or pending-input refusal therefore retains empty transmitted bytes and
`write_complete=false` for that attempt. Its observed late input remains separate
from the preceding exchange; original prior bytes are retained once.

Opening a port lets pySerial flush the host input buffer once. After that the
channel discards no received input: unexpected pending bytes are retained and
refuse dispatch, and a quarantined stream requires a new connection.

`close()` releases only the owned host port. A failed or unconfirmed close keeps
the transport available for an explicit cleanup retry. A retry is an attempt,
not a guarantee: pySerial leaves `is_open=true` when its host close raises. The
binding stays unavailable until a later close explicitly confirms release;
persistent errors remain owned and quarantined. Cancelling a host wait or
closing the serial handle does not prove that the physical exposure ended.
No quiet-buffer interval establishes hardware readiness or cancels a measurement.

## Native reply policies

[`serial_protocols`](../src/color_measurement/serial_protocols.py) supplies pure
parsers over the shared decimal owner. `numbers` preserves signed bins, signed
zero and representable subnormals; it refuses nonzero values that underflow to
zero, nonfinite values, Python digit separators and non-ASCII digits. Field
counts remain protocol-specific. CS's documented `-9.9999e9` calculation-error
sentinel is an unavailable result, never a spectral bin.

`cr_reply` verifies OK/ER status signs and command labels, preserving nonnegative
native success codes. `cr_grid` follows the returned start, end, spacing and
sample count, independently of optical bandwidth. `cr_spectrum` retains the
reported radiometric geometry and integrated quantities. The total's W-based
unit does not establish the spectrum's per-nm denominator; spectral density
units stay unverified.

`cs_reply` validates exact OK00/ERnn framing and acknowledgement-only replies.
`cs_identity` checks the explicitly selected standard model, its documented
variation and a seven-digit serial. `cs_duration` treats MEAS's 002–242 interval
as integer seconds, separately from its later completion reply. `cs_conditions`
retains the eight native MEDR fields; integration time stays in microseconds,
and an observer field is not invented from those conditions. `cs_spectrum`
requires four MEDR blocks with 100, 100, 100 and 101 samples, returning the native
401-point spectral radiance grid at 380–780 nm in W/(m² sr nm).
The producer uses `spectral_radiance`, the shared science owner's quantity name.
The operating manual pp.39 and 90–91 identifies radiance and this unit;
the communication specification's inconsistent English irradiance wording does
not change the reported geometry. Qualification still requires explicit observer,
wavelength and retained-reference context; raw samples remain unchanged.
CS ER17 is command-specific: it can imply an active exposure on MEAS, while
MEDR's ER17 is a parameter error. ER00/ER02 remain uncertain for any command.

These reply parsers and typed native values do not send commands or perform color
calculations. `DirectSerialInstrument` composes them into the explicitly bound
producer described above.

## CS settings plans

`cs_speed_values(payload, *, variation)` retains the documented speed layout
and its exact restoration command. A CS-2000 Manual reply alone cannot establish
its hardware generation. Older nonmanual replies have protocol-fixed Auto ND;
the CS-2000A variation and modern replies establish the newer layout. No firmware
version or undocumented VERR reply is inferred. `cs_sync_values` interprets
Internal sync frequency in centihertz and retains null frequency for No Sync or
External. It does not report a measured refresh rate.

`cs_settings_plan(changes, before, *, automatic=False)` validates coupled speed,
interval, ND, synchronization, accessory and correction-channel targets. The
optional normal recipe requests Multi-Normal, a 1-second averaging interval,
internal ND Auto and No Sync. Manual overrides cannot borrow that recipe's
interval or silently choose fixed ND. Manual times use whole microseconds;
Multi intervals use 1–16 whole seconds. Modern-only choices require readback
evidence. A staged accessory replacement disables incompatible compensation
before enabling its replacement, preserving unrequested physical settings.

Changing to a nonzero user correction channel produces an explicit UCPR name
preflight requirement before any setter. The plan does not claim a populated
channel, verified readback, rollback or calibration validity. Sources are the
attributed MATLAB `speedCommand`, `syncSettings`, `settingsPlan`,
`validateSettingsChanges` and `applyAutomaticSettings`, and CS Rev.7 pp.15–22,
24–25, 31–32, 36–37 and 75; operating manual pp.26,29,43,45.

## CR settings plans

`cr_settings_specs(model)` declares native command tokens and minimum firmware
versions for CR-100, CR-250 and CR-300. `cr_setting_list` validates the complete
counted capability list, preserving the actual IDs, labels and third-column
quantity-type text. It does not invent a filter-clearing ID. `cr_setting_value`
interprets current `RS` replies against reported choices; documented `NA` stays
null. Exposure and synchronization values require their native `msec` and `Hz`
suffixes. `RM` capture metadata is not current-setting readback, and maximum
automatic exposure has no documented `RS` query.
`cr_setting_limits` applies numeric capability validity after complete frame
validation, independently of strict `cr_setting_value` current-setting parsing.

`cr_settings_plan(model, changes, before, *, automatic=False)` uses a report with
`values` and controls keyed by setting ID. Each control retains `available`,
reported `choices`, `minimum` and `maximum`. The complete target is validated
before returning setters. CR-100 Fixed exposure requires Fixed range D;
CR-250/300 do not inherit that restriction. Manual synchronization, correction
profile selection and automatic exposure limits retain their dependencies.

The optional normal recipe requests reported Auto exposure and, for CR-100,
reported Colorimeter mode followed by Auto range. Missing or ambiguous options
refuse. The recipe leaves synchronization, speed, multiplier, optics, filters
and corrections unchanged unless explicitly overridden. Physical aperture and
CR-100 measurement mode are read-only public settings. An accessory change
cannot use the current accessory's Matrix catalog; explicitly disable Matrix,
change the accessory separately, then inspect its new capabilities.

Setters retain native order: disabling correction precedes an accessory change,
range and exposure values precede Fixed exposure, and profile IDs precede their
correction mode. A plan claims no successful write, readback, atomic transaction
or rollback. Sources are the attributed MATLAB `settingsSpecs`, `settingsChoices`,
`settingReadback`, `validateSettingsCombination` and `applySettingsTransaction`,
CR Remote 1.36 §§4.2/4.3, and the application manual 1.5.10 pp.20,23,78.

## Sources and attribution

The shared framing and uncertain-stream policies retain attribution to these
lawful source owners, including the Photo Research work from which shared
transport policies were derived. Current Python direct acquisition covers CR/CS;
the separate MATLAB and standalone Photo Research drivers keep their own APIs.
This is a Python port of attributed instrument work, not a claim to have invented
their protocol or scientific methods:

- Photo Research spectrum logger, revision
  [`25d4e8f43c0591b3d5ccfb818dc1660228931abb`](https://github.com/ferazambuja/photo-research-cli/blob/25d4e8f43c0591b3d5ccfb818dc1660228931abb/photo_research_cli/driver.py),
  with its [source attribution](https://github.com/ferazambuja/photo-research-cli/blob/25d4e8f43c0591b3d5ccfb818dc1660228931abb/THIRD_PARTY_NOTICES.md).
  Its PR implementation adapts the MATLAB driver and Psychtoolbox's remote-mode
  and pacing routines under their retained MIT notices.
- MATLAB color-measurement drivers: PR `PR655`, shared CR `Instrument`,
  CS-2000 `COMClass` and `CS2000`, and shared `LineReceiver`. Original contributors
  include Luke Hellwig, Tucker Downs and Vlad Simion; Fernando Voltolini de
  Azambuja maintained the current protocol and safety policies.
- Colorimetry Research, *Remote Communication* 1.36, §§2.2, 4.3 and 4.5.
  [Manufacturer downloads](https://www.colorimetryresearch.com/downloads).
- Konica Minolta, *CS-2000/CS-2000A Communication Specifications*, Rev. 7.00,
  §§1.1, 1.4, 1.6 and 5.1.
  [Manufacturer specification](https://www.konicaminolta.com.cn/instruments/download/software/driver/cs-20002000a_cs0700_eng.pdf).
- Konica Minolta, *CS-2000/CS-2000A Instruction Manual*, pp.39 and 90–91:
  [Manufacturer manual](https://www.konicaminolta.com/instruments/download/instruction_manual/display/pdf/cs-2000-2000a_instruction_eng.pdf).
  native spectral radiance and W/(sr m² nm). These operating definitions resolve
  the English communication specification's inconsistent irradiance wording.

The inherited MIT notice is retained in
[`LICENSES/MIT-direct-instruments.txt`](../LICENSES/MIT-direct-instruments.txt).
pySerial is an external BSD-licensed dependency; its installed distribution
retains its [upstream license](https://github.com/pyserial/pyserial/blob/v3.5/LICENSE.txt).

## Software proof and limits

[`test_serial_transport`](../tests/test_serial_transport.py) uses injected
transports and clocks only. It covers fragmented terminators, echo lines
returned unchanged for policy refusal, partial writes, pending input, byte and
line overflow, non-ASCII input, timeouts, cancellation and confirmed or failed
host close. These checks establish
host software behavior. No physical equipment or OS serial connection is tested.

[`test_serial_instruments`](../tests/test_serial_instruments.py) drives the five CR/CS
model identities and native captures through scripted bytes, including ordered
settings, independent readback, context attribution, busy/error cleanup refusals,
documentary encoding failure and aggregate byte bounds.
[`test_desktop_direct`](../tests/test_desktop_direct.py) exercises native setup,
command reconciliation, persistent host cleanup, selected reopen and registration
rollback. These are host-software and composition observations only.

A completed software reply cannot rule out later unsolicited bytes. Instrument
protocol, scientific quantities, settings readback, optical accuracy and physical
operation require their own source and validation scope.
