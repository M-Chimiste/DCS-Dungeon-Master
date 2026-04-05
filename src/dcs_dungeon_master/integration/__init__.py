"""Simulation integration services."""

from dataclasses import dataclass

from dcs_dungeon_master.core.config import DcsConfig
from dcs_dungeon_master.integration.grpc_client import DcsGrpcClient
from dcs_dungeon_master.integration.olympus import OlympusClient
from dcs_dungeon_master.integration.types import IntegrationHealthStatus


@dataclass(slots=True)
class IntegrationServices:
    olympus: OlympusClient
    dcs_grpc: DcsGrpcClient

    def check_health(self) -> tuple[IntegrationHealthStatus, IntegrationHealthStatus]:
        return (self.olympus.check_health(), self.dcs_grpc.check_health())


def build_integration_services(config: DcsConfig) -> IntegrationServices:
    return IntegrationServices(
        olympus=OlympusClient(config.olympus),
        dcs_grpc=DcsGrpcClient(config.grpc),
    )
