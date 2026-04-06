from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import Coalition, RunLifecycleStatus
from dcs_dungeon_master.evaluation import EvaluationService, build_evaluation_metadata
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.execution import ExecutionEngine, LiveCommandLoopRunner
from dcs_dungeon_master.integration.olympus import OlympusClient
from dcs_dungeon_master.core.models import ResolvedCoalitionRouting, ResolvedRunRouting
from dcs_dungeon_master.model_adapter import build_model_registry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.operator_control import OperatorControlService
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.world_state import WorldStateRepository


def _write_temp_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state.sqlite3"
    config_path.write_text(
        f"""
[runtime]
app_name = "dcs_dungeon_master"
environment = "test"
dry_run = true

[logging]
level = "INFO"
format = "text"

[dcs]
remote_hosted = false

[dcs.olympus]
base_url = "http://127.0.0.1:4512"
timeout_sec = 5.0

[dcs.grpc]
host = "127.0.0.1"
port = 50051
timeout_sec = 5.0

[[models]]
name = "local_default"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true

[model_routing]
red_backend = "local_default"
blue_backend = "local_default"

[scenario]
id = "phase1_baseline_persian_gulf"
registry_path = "scenarios/index.toml"

[persistence]
db_path = "{db_path}"
enable_wal = true

[dry_run]
enabled = true
summary_output = "text"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _seed_services(tmp_path: Path):
    config = load_config(_write_temp_config(tmp_path))
    scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
    store = SQLiteStateStore(config.persistence.db_path)
    run_id = store.create_run_from_scenario(
        scenario,
        mode="dry",
        config_digest="test-digest",
        config_snapshot=config.to_dict(),
        red_backend_name=config.model_routing.red_backend,
        blue_backend_name=config.model_routing.blue_backend,
        evaluation_metadata=build_evaluation_metadata(config),
    )
    sensor_fusion = SensorFusionService(store, scenario, config.fog_of_war)
    operator = OperatorControlService(store, sensor_fusion=sensor_fusion, world_repository=WorldStateRepository(store))
    return config, scenario, store, run_id, sensor_fusion, operator


def test_operator_control_enforces_run_lifecycle_transitions(tmp_path: Path) -> None:
    _, _, _, run_id, _, operator = _seed_services(tmp_path)

    assert operator.get_status(run_id).status is RunLifecycleStatus.CREATED

    started = operator.start_run(run_id)
    assert started.current_status is RunLifecycleStatus.RUNNING

    paused = operator.pause_run(run_id)
    assert paused.current_status is RunLifecycleStatus.PAUSED

    with pytest.raises(PersistenceError):
        operator.ensure_cycle_allowed(run_id)

    resumed = operator.resume_run(run_id)
    assert resumed.current_status is RunLifecycleStatus.RUNNING

    stopped = operator.stop_run(run_id, reason="done")
    assert stopped.current_status is RunLifecycleStatus.STOPPED

    with pytest.raises(PersistenceError):
        operator.resume_run(run_id)


def test_operator_control_summarizes_and_exports_replay_bundle(tmp_path: Path) -> None:
    config, scenario, store, run_id, sensor_fusion, operator = _seed_services(tmp_path)
    observation_builder = ObservationBuilder(store, scenario, sensor_fusion)
    validator = ActionValidator(store, scenario)

    def model_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"actions":[{"action_id":"hold","action_type":"hold_action","reason":"Maintain posture","scope":"global"}]}'
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"data": [{"id": "gemma-4-26b-a4b-it"}]})

    registry = build_model_registry(config, transports={"local_default": httpx.MockTransport(model_handler)})
    olympus = OlympusClient(config.dcs.olympus, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"accepted": True})))
    execution_engine = ExecutionEngine(store, scenario, olympus)
    runner = LiveCommandLoopRunner(store, observation_builder, validator, registry, execution_engine)

    operator.ensure_cycle_allowed(run_id)
    runner.run_live_cycle(run_id, 1)
    EvaluationService(store, scenario).evaluate_run(run_id)

    summary = operator.summarize_run(run_id)
    timeline = operator.list_timeline(run_id)
    inspection = operator.inspect_coalition(run_id, summary.coalition_views[0].coalition)
    export_dir = tmp_path / "bundle"
    export = operator.export_replay_bundle(run_id, export_dir)
    comparison = operator.compare_runs(run_id, run_id)
    replay_exports = store.list_replay_export_results(run_id)

    registry.close()
    olympus.close()

    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    run_summary = json.loads((export_dir / "run_summary.json").read_text(encoding="utf-8"))

    assert summary.decision_cycle_count == 1
    assert summary.latest_decision_cycle == 1
    assert len(timeline) == 1
    assert inspection.latest_execution is not None
    assert export.file_count >= 10
    assert manifest["run_id"] == run_id
    assert "decision_timeline.jsonl" in manifest["file_inventory"]
    assert "standing_order_history.jsonl" in manifest["file_inventory"]
    assert "evaluation_summary.json" in manifest["file_inventory"]
    assert "fairness_findings.jsonl" in manifest["file_inventory"]
    assert replay_exports
    assert replay_exports[-1].manifest_path == export.manifest_path
    assert replay_exports[-1].includes_evaluation_summary is True
    assert replay_exports[-1].includes_cycle_evaluations is True
    assert replay_exports[-1].includes_fairness_findings is True
    assert run_summary["latest_decision_cycle"] == 1
    assert comparison.left_run_id == run_id
    assert timeline[0].red_execution_lifecycle_state == "completed"
    assert any(count >= 1 for count in run_summary["execution_status_counts"].values())


def test_replay_export_requires_empty_directory(tmp_path: Path) -> None:
    _, _, _, run_id, _, operator = _seed_services(tmp_path)
    export_dir = tmp_path / "bundle"
    export_dir.mkdir()
    (export_dir / "old.txt").write_text("stale", encoding="utf-8")

    with pytest.raises(PersistenceError):
        operator.export_replay_bundle(run_id, export_dir)


def test_operator_compare_and_replay_manifest_include_routing_metadata(tmp_path: Path) -> None:
    config, scenario, store, run_id, _, operator = _seed_services(tmp_path)
    symmetric_routing = ResolvedRunRouting(
        red=ResolvedCoalitionRouting(
            coalition=Coalition.RED,
            primary_backend_name="local_default",
            fallback_backend_name=None,
            primary_catalog_id="local_default",
            fallback_catalog_id=None,
            primary_source="catalog",
            fallback_source=None,
        ),
        blue=ResolvedCoalitionRouting(
            coalition=Coalition.BLUE,
            primary_backend_name="local_default",
            fallback_backend_name=None,
            primary_catalog_id="local_default",
            fallback_catalog_id=None,
            primary_source="catalog",
            fallback_source=None,
        ),
        shared_primary_catalog_id="local_default",
        shared_fallback_catalog_id=None,
        same_primary_for_both=True,
        same_fallback_for_both=True,
    )
    store.update_run_routing(run_id, symmetric_routing)
    routing = ResolvedRunRouting(
        red=ResolvedCoalitionRouting(
            coalition=Coalition.RED,
            primary_backend_name="local_default",
            fallback_backend_name="local_default",
            primary_catalog_id="local_default",
            fallback_catalog_id="local_default",
            primary_source="catalog",
            fallback_source="catalog",
        ),
        blue=ResolvedCoalitionRouting(
            coalition=Coalition.BLUE,
            primary_backend_name="blue_override",
            fallback_backend_name=None,
            primary_catalog_id="blue_override",
            fallback_catalog_id=None,
            primary_source="catalog",
            fallback_source=None,
        ),
        shared_primary_catalog_id="local_default",
        shared_fallback_catalog_id="local_default",
        same_primary_for_both=False,
        same_fallback_for_both=False,
    )
    other_run_id = store.create_run_from_scenario(
        scenario,
        mode="dry",
        config_digest="other-digest",
        config_snapshot=config.to_dict(),
        red_backend_name=routing.red.primary_backend_name,
        blue_backend_name=routing.blue.primary_backend_name,
        routing=routing,
        evaluation_metadata=build_evaluation_metadata(
            config,
            red_backend_name=routing.red.primary_backend_name,
            blue_backend_name=config.model_routing.blue_backend,
        ),
    )
    store.update_run_routing(other_run_id, routing)

    comparison = operator.compare_runs(run_id, other_run_id)
    export_dir = tmp_path / "routing-bundle"
    operator.export_replay_bundle(other_run_id, export_dir)
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))

    assert comparison.routing_modes == ("symmetric", "asymmetric")
    assert comparison.symmetric_routing == (True, False)
    assert comparison.backend_assignments["blue_primary_catalog_id"][1] == "blue_override"
    assert manifest["comparison_metadata"]["routing"]["blue"]["primary_backend_name"] == "blue_override"
    assert manifest["comparison_metadata"]["routing"]["red"]["primary_catalog_id"] == "local_default"
