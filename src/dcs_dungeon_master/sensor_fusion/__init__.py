"""Sensor fusion stubs."""

from dataclasses import dataclass


@dataclass(slots=True)
class SensorFusionStub:
    status: str = "stub-ready"
