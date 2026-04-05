"""Observation stubs."""

from dataclasses import dataclass


@dataclass(slots=True)
class ObservationBuilderStub:
    status: str = "stub-ready"
