"""Execution stubs."""

from dataclasses import dataclass


@dataclass(slots=True)
class ExecutionEngineStub:
    status: str = "stub-ready"
