"""Operator control stubs."""

from dataclasses import dataclass


@dataclass(slots=True)
class OperatorControlStub:
    status: str = "stub-ready"
