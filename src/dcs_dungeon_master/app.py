"""Application bootstrap for Phase 1 dry-run milestones."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import logging

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.config import AppConfig, load_config
from dcs_dungeon_master.core.logging import configure_logging
from dcs_dungeon_master.core.versions import ACTION_SCHEMA_VERSION, APP_VERSION, OBSERVATION_SCHEMA_VERSION
from dcs_dungeon_master.evaluation import EvaluationService, build_evaluation_metadata
from dcs_dungeon_master.execution import ExecutionEngine, LiveCommandLoopRunner
from dcs_dungeon_master.integration import IntegrationIngestCoordinator, build_integration_services
from dcs_dungeon_master.model_adapter import DryDecisionLoopRunner, build_model_registry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.operator_control import OperatorControlService
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.world_state import KnowledgeDebugView, WorldStateRepository, WorldStateUpdater


DEFAULT_CONFIG_PATH = Path("config/milestone0.toml")


@dataclass(slots=True)
class Application:
    config: AppConfig
    scenario_id: str
    scenario_name: str
    run_id: str
    db_path: str
    service_statuses: dict[str, str]
    state_summary: dict[str, object]
    integration_endpoints: dict[str, str]
    integration_health: dict[str, object] | None = None

    def summary(self) -> dict[str, object]:
        return {
            "app_version": APP_VERSION,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "run_id": self.run_id,
            "db_path": self.db_path,
            "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
            "action_schema_version": ACTION_SCHEMA_VERSION,
            "service_statuses": self.service_statuses,
            "integration_endpoints": self.integration_endpoints,
            "integration_health": self.integration_health,
            "dry_run": self.config.runtime.dry_run and self.config.dry_run.enabled,
            "state_summary": self.state_summary,
        }


def bootstrap_application(config_path: str | Path = DEFAULT_CONFIG_PATH) -> Application:
    config = load_config(config_path)
    configure_logging(config.logging)
    logger = logging.getLogger("dcs_dungeon_master")
    scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
    store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
    store.initialize_schema()
    config_snapshot = config.to_dict()
    config_digest = hashlib.sha256(json.dumps(config_snapshot, sort_keys=True).encode("utf-8")).hexdigest()
    run_id = store.create_run_from_scenario(
        scenario,
        mode="dry" if config.runtime.dry_run and config.dry_run.enabled else "live",
        config_digest=config_digest,
        config_snapshot=config_snapshot,
        red_backend_name=config.model_routing.red_backend,
        blue_backend_name=config.model_routing.blue_backend,
        evaluation_metadata=build_evaluation_metadata(config),
    )
    state_summary = store.get_run_summary(run_id)
    world_repository = WorldStateRepository(store)
    world_updater = WorldStateUpdater(world_repository, scenario)
    sensor_fusion = SensorFusionService(store, scenario, config.fog_of_war)
    observation_builder = ObservationBuilder(store, scenario, sensor_fusion, config.multimodal)
    action_validator = ActionValidator(store, scenario)
    model_registry = build_model_registry(config)
    debug_view = KnowledgeDebugView(store, sensor_fusion)
    operator_control = OperatorControlService(store, sensor_fusion=sensor_fusion, world_repository=world_repository)
    evaluation = EvaluationService(store, scenario)

    integrations = build_integration_services(config.dcs)
    ingest_coordinator = IntegrationIngestCoordinator(integrations, world_updater)
    dry_loop = DryDecisionLoopRunner(store, observation_builder, action_validator, model_registry, ingest_coordinator)
    execution_engine = ExecutionEngine(store, scenario, integrations.olympus)
    live_loop = LiveCommandLoopRunner(
        store,
        observation_builder,
        action_validator,
        model_registry,
        execution_engine,
        ingest_coordinator,
    )
    services = {
        "olympus_gateway": "client-ready",
        "dcs_grpc_gateway": "client-ready",
        "integration_ingest": ingest_coordinator.status,
        "world_state": world_updater.status,
        "sensor_fusion": sensor_fusion.status,
        "observation_builder": observation_builder.status,
        "model_adapters": model_registry.status,
        "action_validator": action_validator.status,
        "dry_decision_loop": dry_loop.status,
        "execution_engine": execution_engine.status,
        "live_command_loop": live_loop.status,
        "persistence": "sqlite-ready",
        "operator_control": operator_control.status,
        "evaluation": evaluation.status,
        "knowledge_debug": "debug-ready" if debug_view else "debug-unavailable",
    }
    logger.info("Bootstrapped dry-run application for scenario '%s' into run '%s'.", scenario.id, run_id)
    application = Application(
        config=config,
        scenario_id=scenario.id,
        scenario_name=scenario.name,
        run_id=run_id,
        db_path=config.persistence.db_path,
        service_statuses=services,
        state_summary=state_summary,
        integration_endpoints={
            "olympus": integrations.olympus.endpoint,
            "dcs_grpc": integrations.dcs_grpc.endpoint,
        },
        integration_health=None,
    )
    model_registry.close()
    integrations.close()
    return application
