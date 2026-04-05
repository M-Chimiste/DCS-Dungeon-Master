"""World state stubs."""

from dataclasses import dataclass


@dataclass(slots=True)
class WorldStateStub:
    status: str = "stub-ready"
