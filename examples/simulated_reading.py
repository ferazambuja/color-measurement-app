"""Run with uv run python examples/simulated_reading.py; no hardware required."""

from color_measurement.exports import to_json
from color_measurement.operations import SimulatedDevice
from color_measurement.processing import compute

for event in SimulatedDevice().measure(sample_id="example"):
    if event.observation is not None:
        # This white is synthetic and is not D50, D65, or a calibrated reference.
        print(to_json(compute(event.observation, reference_white=(80.0, 100.0, 120.0))))
