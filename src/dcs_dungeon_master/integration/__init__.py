"""Simulation integration stubs."""

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class IntegrationStub:
    name: str
    status: str = "stub-ready"


def build_integration_stubs() -> tuple[IntegrationStub, IntegrationStub]:
    return (
        IntegrationStub(name="olympus_gateway"),
        IntegrationStub(name="dcs_grpc_gateway"),
    )
