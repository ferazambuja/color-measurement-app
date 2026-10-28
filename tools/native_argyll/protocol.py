"""Synthetic exercises for the shared native process protocol."""

from pathlib import Path

from color_measurement.native_protocol import (
    HelperSession,
    NativeEvent,
    NativeReading,
    NativeSpectrum,
    ProtocolError,
    finite,
    invalid_constant,
)

__all__ = [
    "HelperSession",
    "NativeEvent",
    "NativeReading",
    "NativeSpectrum",
    "ProtocolError",
    "finite",
    "invalid_constant",
    "exercise_helper",
]


def exercise_helper(executable: Path) -> dict:
    """Actual owned-process checks; every value is synthetic and explicitly labelled."""
    outcomes = {}
    scenarios = (
        "reading",
        "invalid_xyz",
        "black",
        "no_spectrum",
        "empty",
        "m2",
        "m3",
        "custom",
        "unspecified",
        "nonfinite",
        "bad_grid",
        "bad_norm",
        "zero_spectrum",
        "calibration_only",
        "failure",
    )
    for scenario in scenarios:
        with HelperSession([str(executable)]) as session:
            session.begin(f"check-{scenario}", scenario)
            session.receive()
            session.receive()
            session.send("complete")
            result = session.receive()
            expected = (
                "error"
                if scenario in {"empty", "nonfinite", "bad_grid", "bad_norm", "failure"}
                else "calibration_complete"
                if scenario == "calibration_only"
                else "result"
            )
            if result.event != expected:
                raise ProtocolError(f"Wrong outcome for {scenario}")
            if result.reading is not None:
                native = result.reading
                xyz = (
                    None
                    if scenario == "invalid_xyz"
                    else (0.0, 0.0, 0.0)
                    if scenario == "black"
                    else (2.0, 3.0, 5.0)
                )
                condition = {"m2": 3, "m3": 4, "custom": 5, "unspecified": 0}.get(scenario, 1)
                if (
                    native.xyz != xyz
                    or native.mcond != condition
                    or native.duration_seconds != 0.25
                ):
                    raise ProtocolError(f"Lost native XYZ/condition/duration: {scenario}")
                if scenario == "no_spectrum":
                    if native.spectrum is not None:
                        raise ProtocolError("Invented absent spectrum")
                else:
                    samples = (
                        (0.0,) * 4
                        if scenario == "zero_spectrum"
                        else (-0.125, 1.2345678901234567, 120.25, 0.0)
                    )
                    if (
                        native.spectrum.samples != samples
                        or native.spectrum.norm != 100.0
                        or native.spectrum.wavelengths_nm != (402.5, 407.5, 412.5, 417.5)
                    ):
                        raise ProtocolError(
                            "Lost native spectral grid/norm/signed binary64 samples"
                        )
            session.finish()
            outcomes[scenario] = result.event
    with HelperSession([str(executable)]) as session:
        session.begin("cancel-check", "wait")
        session.receive()
        session.receive()
        session.send("cancel")
        if session.receive().event != "cancelled":
            raise ProtocolError("Cancellation was not acknowledged")
        session.finish()
        outcomes["cancel"] = "acknowledged_and_process_exited; instrument_readiness_unknown"
    with HelperSession([str(executable)], timeout=0.1) as session:
        session.begin("timeout-check", "wait")
        session.receive()
        session.receive()
        session.send("complete")
        try:
            session.receive()
        except TimeoutError:
            outcomes["timeout"] = "owned_process_stopped; instrument_readiness_unknown"
        else:
            raise ProtocolError("Expected timeout")
    if session.process.poll() is None:
        raise ProtocolError("Timed-out helper survived cleanup")
    return {"status": "passed", "provenance": "synthetic", "cases": outcomes}
