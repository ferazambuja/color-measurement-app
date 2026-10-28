"""Human diagnostics on stderr; complete records on stdout for save recovery."""

import argparse
import json
import math
import signal
import sys
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event

from .argyll_capability import CONTRIBUTED_INSTRUMENTS, INSTRUMENTS, instrument_display_name
from .exports import SaveError, from_csv, from_json, save, to_json
from .instrument import InstrumentError
from .operations import SimulatedDevice
from .processing import compute


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Color measurement: acquisition from the simulator or from an instrument"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "devices",
        help="List the simulated source and every instrument name this build accepts "
             "(a name table, NOT hardware discovery: nothing is enumerated or opened)",
    )
    acquire = commands.add_parser(
        "acquire", help="Acquire one observation from the simulator or from an instrument")
    # Canonical names and accepted aliases resolve through instrument_binding_name. Not a
    # choices= list: the point of the generic path is that a further instrument
    # costs no code, and a hardcoded list here would be exactly that cost.
    acquire.add_argument("--device", required=True,
                         help="`simulator`, or an ArgyllCMS instrument name "
                              "(see `devices`). A real instrument needs a retained build report")
    acquire.add_argument("--build-report", type=Path,
                         help="Retained native report.json, with both workers beside it; "
                              "defaults to CM_ARGYLL_BUILD_REPORT. Refused for the simulator")
    acquire.add_argument("--xyz-only", action="store_true",
                         help="Do not require a spectrum (available spectra are still retained)")
    acquire.add_argument("--serial", help="Serial the instrument must report, or empty to accept "
                                          "whichever one answers")
    acquire.add_argument("--usb-index", type=int, default=1,
                         help="1-based index into the helper's own enumeration, 1-64. "
                              "HID paths are enumerated first and shift USB indices down, "
                              "so take it from a fresh enumeration rather than assuming")
    acquire.add_argument("--allow-serial-identification", action="store_true",
                         help="Permit opening a SERIAL port to learn what is on it; a serial "
                              "port carries no identity until it is opened")
    acquire.add_argument("--sample-id", default="synthetic-sample")
    acquire.add_argument("--mode", default="relative_xyz",
                         help="`relative_xyz` for the simulator, otherwise an instrument "
                              "mode name such as reflective_spot_m1")
    acquire.add_argument(
        "--delay", type=float, default=0.0, help="Simulation delay in seconds, 0–3600"
    )
    acquire.add_argument(
        "--white",
        type=float,
        nargs=3,
        metavar=("Xn", "Yn", "Zn"),
        help="Optional Lab white; must share native XYZ reference conditions and scale",
    )
    acquire.add_argument(
        "--output", type=Path, help="Save to a new file; existing files are never replaced"
    )
    acquire.add_argument("--format", choices=("json", "csv"), default="json")
    export = commands.add_parser("export", help="Save an existing record again without acquiring")
    export.add_argument("input", type=Path)
    export.add_argument("--input-format", choices=("json", "csv"), default="json")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--format", choices=("json", "csv"), default="json")
    return parser


def _acquire(args: argparse.Namespace):
    if not math.isfinite(args.delay) or not 0 <= args.delay <= 3600:
        raise ValueError("delay must be a finite number from 0 to 3600 seconds")
    cancel = Event()
    previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
    try:
        for event in SimulatedDevice().measure(
            sample_id=args.sample_id,
            mode=args.mode,
            delay=args.delay,
            cancel=cancel,
        ):
            print(event.message, file=sys.stderr)
            if event.kind == "cancelled":
                return None
            if event.observation is not None:
                return compute(
                    event.observation, reference_white=tuple(args.white) if args.white else None
                )
    finally:
        signal.signal(signal.SIGINT, previous)
    return None


def _acquire_instrument(args: argparse.Namespace):
    """One reading from a real instrument, through the one generic path.

    Nothing here names an instrument. `--device` is resolved by the same
    `instrument_binding_name` table the library uses, and the mode is whatever that
    instrument's own capability words allow -- so a further instrument costs this
    command nothing, which is the whole claim.

    The retained report binds both workers to the pinned backend. Its path is
    explicit or comes from CM_ARGYLL_BUILD_REPORT; no helper search is performed.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    from .discovery import InstrumentBuild
    from .instrument import Instrument, observation_from_patch
    from .models import DeviceIdentity

    build = InstrumentBuild.from_report(args.build_report)
    meter = Instrument(
        helper=build._helper,
        expected_helper_sha256=build._helper_sha256,
        instrument=args.device,
        expected_serial=args.serial,
        path_index=args.usb_index,
        allow_serial_identification=args.allow_serial_identification,
    )
    print(f"Binding {meter.instrument} through {build._helper}", file=sys.stderr)
    def acknowledge(prompt):
        print(json.dumps(prompt, ensure_ascii=False), file=sys.stderr)
        if prompt.get("acknowledged") is False:
            return "acknowledge"
        choices = "accept/skip/cancel" if prompt.get("skippable") else "accept/cancel"
        while True:
            print(f"Instrument instruction ({choices}): ", end="", file=sys.stderr, flush=True)
            answer = sys.stdin.readline().strip().lower()
            if answer == "accept":
                return "acknowledge"
            if answer == "skip" and prompt.get("skippable"):
                return "skip"
            if answer in {"cancel", ""}:  # Closed input must never acknowledge hardware.
                return "cancel"
    failure = None
    try:
        measurement = meter.measure(args.mode, spectral=not args.xyz_only, positioned=None,
                                    calibrate=True, acknowledge=acknowledge)
    except InstrumentError as error:
        if error.measurement is None:
            raise
        measurement, failure = error.measurement, error
    if not measurement.patches:
        raise ValueError("The instrument returned no reading")
    print(f"Read one patch in {measurement.mode} ({measurement.provenance})", file=sys.stderr)
    observation = observation_from_patch(
        measurement.patches[0], measurement,
        device=DeviceIdentity(
            device_id=meter.instrument, model=instrument_display_name(meter.instrument),
            backend="argyll-native", backend_version="3.5.0",
            serial=measurement.serial,
            serial_encoding=measurement.serial_encoding,
        ),
        operation_id=str(uuid4()),
        sample_id=args.sample_id,
        acquired_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        raw_backend_output=json.dumps({
            "events": measurement.events,
            "build_report_sha256": build._client._report_sha256,
            "helper_sha256": build._helper_sha256,
        }),
        observation_id=str(uuid4()),
    )
    if failure is not None:
        warning = f"Acquisition failed after reading: {failure}; instrument state is unknown"
        print(warning, file=sys.stderr)
        observation = replace(observation, warnings=(*observation.warnings, warning))
    if args.white is not None:
        if observation.backend_reported.xyz is None:
            warning = (
                "Lab unavailable: the instrument returned no native XYZ; "
                "this acquisition does not infer missing native XYZ from a spectrum"
            )
            print(warning, file=sys.stderr)
            observation = replace(observation, warnings=(*observation.warnings, warning))
        else:
            observation = compute(observation, reference_white=tuple(args.white))
    return observation, 1 if failure is not None else 0


def main(argv: list[str] | None = None) -> int:
    """Exit 0 success, 1 invalid/load error, 2 save failure, or 130 cancellation.

    Successful acquisitions always print complete JSON before saving. On save
    failure this stdout record can be redirected to a recovery file, then passed
    to ``export``. No command silently reacquires, overwrites, or retries.

    Exit 0 is the acquisition's status, not the derivation's: an instrument
    reading with ``--white`` but no native XYZ still exits 0, with Lab absent and
    the reason in the record's ``warnings`` (see ``_acquire_instrument``). An
    acquisition that failed after its reading exits 1 and keeps the reading.
    """
    args = _parser().parse_args(argv)
    exit_code = 0
    try:
        if args.command == "devices":
            print(
                json.dumps(
                    {
                        "provenance": "simulated",
                        "capabilities": asdict(SimulatedDevice.capabilities),
                        # A NAME TABLE, not a discovery result. Nothing is
                        # enumerated, opened or probed to produce it. These names
                        # resolve in Python; native construction depends on the
                        # selected worker build and may still be unavailable.
                        "instruments": [
                            {"name": name, "instrument": instrument_display_name(name),
                             "requires_contribution": name in CONTRIBUTED_INSTRUMENTS}
                            for name in sorted(INSTRUMENTS.keys() | CONTRIBUTED_INSTRUMENTS.keys())
                        ],
                        "instruments_are": (
                            "static name table; native construction and attachment are not queried"
                        ),
                    }
                )
            )
            return 0
        if args.command == "acquire":
            if args.white is not None and not all(
                    math.isfinite(v) and v > 0 for v in args.white):
                raise ValueError("Reference white components must be finite and positive")
            if args.device == "simulator":
                if args.build_report is not None:
                    raise ValueError("The simulator takes no --build-report; it runs no instrument")
                if args.mode != "relative_xyz":
                    raise ValueError("The simulator measures relative_xyz only")
                observation = _acquire(args)
            else:
                observation, exit_code = _acquire_instrument(args)
            if observation is None:
                return 130
        else:
            # Preserve CRLF inside quoted CSV fields as part of native text.
            text = args.input.read_bytes().decode("utf-8")
            observation = from_json(text) if args.input_format == "json" else from_csv(text)
            print(
                f"Exporting existing {observation.provenance} observation; no acquisition",
                file=sys.stderr,
            )
        print(to_json(observation), end="", flush=True)
        if args.output is not None:
            saved = save(observation, args.output, format=args.format)
            print(
                f"Saved complete {observation.provenance} record to {args.output}", file=sys.stderr
            )
            retry = (
                f" (retryable={str(saved.ti3['retryable']).lower()}, "
                f"relocatable={str(saved.ti3['relocatable']).lower()})"
                if saved.ti3["status"] != "current" or saved.ti3["unavailable"] else ""
            )
            print(f"TI3 companions: {saved.ti3['status']}{retry}", file=sys.stderr)
            for item in saved.ti3["files"]:
                reason = f" {item['reason']}" if item["reason"] else ""
                print(f"{item['state']}: {item['path']}{reason}", file=sys.stderr)
            for item in saved.ti3["unavailable"]:
                print(f"TI3 {item['record_id']} {item['component']}: {item['reason']}",
                      file=sys.stderr)
        return exit_code
    except SaveError as error:
        print(
            f"Save failed; complete observation retained on stdout. Retry with export. {error}",
            file=sys.stderr,
        )
        for note in getattr(error, "__notes__", ()):
            print(note, file=sys.stderr)
        return 2
    except (ValueError, TypeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except InstrumentError as error:
        print(f"Acquisition failed: {error}; no automatic retry", file=sys.stderr)
        return 130 if error.stage == "cancelled" else 1
    except KeyboardInterrupt:
        print("Software interrupted; no automatic retry", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
