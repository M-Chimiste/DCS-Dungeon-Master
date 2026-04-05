"""Application bootstrap for Milestone 0."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging

from dcs_dungeon_master.action_validation import ActionValidatorStub
from dcs_dungeon_master.core.config import AppConfig, load_config
from dcs_dungeon_master.core.logging import configure_logging
from dcs_dungeon_master.core.versions import ACTION_SCHEMA_VERSION, APP_VERSION, OBSERVATION_SCHEMA_VERSION
from dcs_dungeon_master.execution import ExecutionEngineStub
from dcs_dungeon_master.integration import build_integration_stubs
from dcs_dungeon_master.model_adapter import ModelAdapterRegistryStub
from dcs_dungeon_master.observation import ObservationBuilderStub
from dcs_dungeon_master.operator_control import OperatorControlStub
from dcs_dungeon_master.persistence import PersistenceStub
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionStub
from dcs_dungeon_master.world_state import WorldStateStub


DEFAULT_CONFIG_PATH = Path("config/milestone0.toml")


@dataclass(slots=True)
class Application:
    config: AppConfig
    scenario_id: str
    service_statuses: dict[str, str]

    def summary(self) -> dict[str, object]:
        return {
            "app_version": APP_VERSION,
            "scenario_id": self.scenario_id,
            "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
            "action_schema_version": ACTION_SCHEMA_VERSION,
            "service_statuses": self.service_statuses,
            "dry_run": self.config.runtime.dry_run and self.config.dry_run.enabled,
        }


def bootstrap_application(config_path: str | Path = DEFAULT_CONFIG_PATH) -> Application:
    config = load_config(config_path)
    configure_logging(config.logging)
    logger = logging.getLogger("dcs_dungeon_master")
    scenario = get_scenario_definition(config.scenario.id)

    olympus_gateway, grpc_gateway = build_integration_stubs()
    services = {
        olympus_gateway.name: olympus_gateway.status,
        grpc_gateway.name: grpc_gateway.status,
        "world_state": WorldStateStub().status,
        "sensor_fusion": SensorFusionStub().status,
        "observation_builder": ObservationBuilderStub().status,
        "model_adapters": ModelAdapterRegistryStub().status,
        "action_validator": ActionValidatorStub().status,
        "execution_engine": ExecutionEngineStub().status,
        "persistence": PersistenceStub().status,
        "operator_control": OperatorControlStub().status,
    }
    logger.info("Bootstrapped Milestone 0 dry-run application for scenario '%s'.", scenario.id)
    return Application(config=config, scenario_id=scenario.id, service_statuses=services)
