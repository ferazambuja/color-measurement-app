"""Shared operation events, explicit acknowledgments, and simulated acquisition."""

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Lock
from typing import Iterator, Literal
from uuid import uuid4

from . import __version__
from .models import (
    XYZ,
    CalibrationContext,
    Capabilities,
    ComputedValues,
    DeviceIdentity,
    Observation,
    ReportedValues,
)


class OperationBusyError(RuntimeError):
    """The same device object already has an active operation."""


@dataclass(frozen=True)
class Cancellation:
    requested: bool
    software_stopped: bool
    instrument_state: Literal["not_applicable", "unknown", "confirmed_ready"]


@dataclass(frozen=True)
class OperationEvent:
    operation_id: str
    kind: Literal["ready", "measuring", "result", "cancelled"]
    message: str
    observation: Observation | None = None
    cancellation: Cancellation | None = None


class SimulatedDevice:
    """Deterministic synthetic tristimulus source. Never connects to hardware.

    One generator owns this instance until completion or explicit ``close()``.
    Pass a threading.Event to request cancellation; each call performs at most
    one simulated exposure. Events and completed observations remain caller-owned.
    """

    capabilities = Capabilities("simulator", ("relative_xyz",), True, False)

    def __init__(self) -> None:
        self._active = Lock()

    def measure(
        self,
        *,
        sample_id: str = "synthetic-sample",
        mode: str = "relative_xyz",
        delay: float = 0.0,
        cancel: Event | None = None,
    ) -> Iterator[OperationEvent]:
        """Emit ready, measuring, then result or a confirmed software cancellation.

        Unknown modes fail before acquisition. Delay is seconds, bounded to one
        hour; cancellation interrupts the wait. There is no retry behavior.
        """
        if mode not in self.capabilities.modes:
            raise ValueError(f"Unsupported simulator mode: {mode}")
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError("sample_id must be nonempty text")
        if type(delay) not in (float, int) or not 0 <= delay <= 3600 or not math.isfinite(delay):
            raise ValueError("delay must be a finite number from 0 to 3600 seconds")
        if not self._active.acquire(blocking=False):
            raise OperationBusyError("An operation already owns this simulator")
        token = cancel if cancel is not None else Event()
        operation_id = str(uuid4())
        try:
            yield OperationEvent(
                operation_id, "ready", "SIMULATED source ready; no physical device"
            )
            if not token.is_set():
                yield OperationEvent(operation_id, "measuring", "SIMULATED acquisition")
                token.wait(delay)
            if token.is_set():
                yield OperationEvent(
                    operation_id,
                    "cancelled",
                    "Simulation stopped; no physical instrument involved",
                    cancellation=Cancellation(True, True, "not_applicable"),
                )
                return
            # Fresh project-authored synthetic numbers, not an instrument fixture.
            native = (20.0, 25.0, 10.0)
            observation = Observation(
                schema_version=1,
                observation_id=str(uuid4()),
                operation_id=operation_id,
                sample_id=sample_id,
                acquired_at=datetime.now(timezone.utc).isoformat(),
                provenance="simulated",
                device=DeviceIdentity(
                    "simulator", "Synthetic XYZ source", "simulator", __version__
                ),
                requested_mode=mode,
                actual_mode=mode,
                configuration=(("delay_seconds", str(delay)), ("fixture", "synthetic-xyz-v1")),
                calibration=CalibrationContext(
                    "not_applicable", "Synthetic values; no calibration"
                ),
                backend_reported=ReportedValues(
                    xyz=XYZ(
                        native, "1", "relative; reference-white Y=100", "unspecified", "unspecified"
                    )
                ),
                project_computed=ComputedValues(),
                raw_backend_output=json.dumps({"fixture": "synthetic-xyz-v1", "XYZ": native}),
                warnings=("SIMULATED: synthetic numbers, not a physical observation",),
            )
            yield OperationEvent(
                operation_id, "result", "SIMULATED observation available", observation
            )
        finally:
            self._active.release()
