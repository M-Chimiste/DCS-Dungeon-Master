"""Model adapter stubs."""

from dataclasses import dataclass


@dataclass(slots=True)
class ModelAdapterRegistryStub:
    status: str = "stub-ready"
