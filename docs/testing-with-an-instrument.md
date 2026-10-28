# Testing with an instrument

**Beta: physical instrument operation and accuracy have not been validated.**
Use equipment you are authorized to operate, one controlling application, known
accessories and an idle instrument. Software tests and Demo require no device.
Do not update firmware, change calibration storage or bypass a driver refusal
to make a test pass.

## Before connecting

Select your exact model and read the setup instructions and initialization effects
shown by the app before connecting it. A listed model or mode does not establish
that it works with physical equipment. For model-specific detail, use the
[Argyll instrument reference](instrument.md), [initialization information](argyll-description.md)
and, where applicable, [direct serial reference](direct-serial.md).

Initialization can reset a device, change calibration behavior, flash LEDs,
move a scanning table or perform startup measurements on some models. The app
discloses assessed effects and refuses firmware-loading initialization. Read
and accept only the effect for the device you selected. Unassessed behavior is
not a promise of no effects.

CR readings can change CMF, correction/exposure, averaging and synchronization.
The Argyll CR driver does not restore those settings; they can persist until
power-down. Record previous settings before a deliberate test. PR initialization
enters remote mode and disables its touchscreen; a clean close sends Q. An
uncertain exchange forbids further cleanup writes and can leave remote mode
active. PR measurements also turn the backlight off and request 100% afterwards;
they do not restore its previous brightness. Record it before testing and restore
it manually only after a confirmed synchronized idle state and clean close.
Direct serial settings have their own explicit read/apply/restore
procedure. Never assume universal restoration across these different routes.
The imported CR/PR contracts are available with the
[native source contribution](../tools/native_argyll/contributions/cr_pr/files/spectro).
Their manufacturer citations distinguish documented commands from host policy.

Stop on uncertainty and retain the exact error. Do not unplug during acquisition,
force a power-cycle during an uncertain exposure, send undocumented commands or
let another program take over the port. Resolve the state using the device's
manufacturer procedure and your authorized operator.

## Manual session

1. Verify release checksum or source/build identity and note model, firmware,
   connection, optics, accessory and intended mode. Try Demo first.
2. Open **Connect instrument…**. For a source app expand **Instrument software details**,
   use **Choose instrument software…** to select the retained successful report,
   then deliberately **List connected instruments**. Select the
   intended path/model, supply the expected serial if known, and review the
   initialization effect before **Connect selected instrument**. Do not identify
   an unknown serial port without deliberate permission to open that port.
3. Create a new batch in a new folder. Choose a mode/accessory the app
   actually offers. Confirm calibration and placement prompts; record physical
   conditions separately from the application's declared configuration.
4. Take one ordinary reading, inspect quantities, units and errors, then test a
   short batch. Missing spectra/XYZ remain unavailable, never zero or invented.
5. Cancel only through the application's control. Wait for its final outcome
   and confirm the device is idle before another action. Preserve acquisition,
   cleanup and save failures separately.
6. Finish/save, reopen the saved batch and compare retained native observations.
   A save retry must not acquire another reading. Keep original files unchanged.
7. Close cleanly and record actual device/settings state. Restore only settings
   the documented route can restore after a confirmed synchronized idle state.
   If cleanup or state is uncertain, stop and report it.

This checklist applies to CLI/API and source-host sessions too, using their
corresponding documented operations. It is not a calibration/accuracy protocol.
Do not compare instruments without declaring stimulus, geometry, observer,
units, timing and an acceptance criterion before the measurements.

## Guided runner

The reusable [bench runner](../tools/bench_session.py) guides i1Pro 3, CR-250,
CR-100 and PR-655 only. It authenticates files and checks saved results; it never
enumerates ports, starts workers or drives equipment. Other models use the manual
checklist until a model plan is implemented and reviewed.

A physical runner session requires a packaged macOS app and a checkout whose
`src/color_measurement` inventory exactly matches the bundled package. Use the
release's exact source commit/tag and `uv sync --locked`. Source-launched apps
and Windows/Linux sessions use the manual checklist instead.

```sh
uv run --locked python tools/bench_session.py start --name my-session --operator tester \
  --app '/path/to/Color Measurement.app'
uv run --locked python tools/bench_session.py run --session local/bench/my-session --device cr250
uv run --locked python tools/bench_session.py report --session local/bench/my-session
```

Choose a new session name. `--device` accepts `i1pro3`, `cr250`, `cr100` and
`pr655`. To practice without equipment, add `--rehearsal` to `start`, omit `--app`
and follow only explicit Demo steps. Rehearsal accepts simulated records only
and does not count as instrument evidence.

## Report safely

Include release/commit/checksum, OS/architecture, native report identity,
model/firmware, mode/accessory/context, steps, expected/actual outcome and exact
error. Identify whether the result is Demo, software-only or a physical session.
Do not claim accuracy or upstream acceptance from a successful session.

**Before attaching anything:** batches, transcripts, reports and bench folders
can contain serial numbers, operator names, notes, sample labels, absolute paths
and confidential measurements. `session.json` contains operator/host and app/report
paths. Share a minimal redacted copy, retain the original locally, and do not
claim a manually redacted hash-bound record still authenticates. Send serials
only through an agreed private channel when specifically needed.

Use the instrument-result issue template in this repository when hosting exists.
Report bundled-driver problems here, not to ArgyllCMS upstream. Security reports
follow [SECURITY.md](../SECURITY.md).
