"""Action validation stubs."""

from dataclasses import dataclass


@dataclass(slots=True)
class ActionValidatorStub:
    status: str = "stub-ready"
