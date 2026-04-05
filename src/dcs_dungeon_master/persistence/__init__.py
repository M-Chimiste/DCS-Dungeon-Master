"""Persistence stubs."""

from dataclasses import dataclass


@dataclass(slots=True)
class PersistenceStub:
    status: str = "stub-ready"
