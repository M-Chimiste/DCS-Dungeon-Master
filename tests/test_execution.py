from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.config import AirOpsConfig, MapAssetsConfig, TheaterAssetManifestConfig
from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import Coalition, ExecutionLifecycleState, ExecutionStatus, GroupPosture, RunLifecycleStatus
from dcs_dungeon_master.execution import ExecutionEngine, LiveCommandLoopRunner
from dcs_dungeon_master.integration.olympus import OlympusClient
from dcs_dungeon_master.map_assets import MapAssetService
from dcs_dungeon_master.model_adapter import build_model_registry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.terrain import TerrainService
from tests.support_map_assets import write_test_map_assets


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
name = "red_backend"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true

[[models]]
name = "blue_backend"
hosting_mode = "lan"
endpoint = "http://192.168.1.22:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true

[model_routing]
red_backend = "red_backend"
blue_backend = "blue_backend"

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


def _build_execution_services(tmp_path: Path):
    config = load_config(_write_temp_config(tmp_path))
    scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
    store = SQLiteStateStore(config.persistence.db_path)
    run_id = store.create_run_from_scenario(scenario)
    observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war))
    validator = ActionValidator(store, scenario)
    return config, scenario, store, run_id, observation_builder, validator


def _build_air_execution_services(tmp_path: Path):
    config = load_config(_write_temp_config(tmp_path))
    asset_root = tmp_path / "map-assets"
    write_test_map_assets(asset_root)
    scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
    store = SQLiteStateStore(config.persistence.db_path)
    run_id = store.create_run_from_scenario(scenario)
    map_asset_service = MapAssetService(
        MapAssetsConfig(
            asset_root=str(asset_root),
            theaters={
                "persian_gulf": TheaterAssetManifestConfig(
                    theater_name="Persian Gulf",
                    basemap_manifest="persian_gulf/basemap.json",
                    elevation_manifest="persian_gulf/elevation.json",
                    landmarks_manifest="persian_gulf/landmarks.json",
                )
            },
        )
    )
    terrain_service = TerrainService(AirOpsConfig(enabled=True, fixed_wing_clearance_ft=2000, terrain_sample_nm=2))
    validator = ActionValidator(
        store,
        scenario,
        map_asset_service=map_asset_service,
        terrain_service=terrain_service,
        air_ops=terrain_service.config,
    )
    return config, scenario, store, run_id, validator


def test_execution_engine_executes_stateful_actions_and_persists_updates(tmp_path: Path) -> None:
    _, scenario, store, run_id, _, validator = _build_execution_services(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"accepted": True, "path": request.url.path})

    engine = ExecutionEngine(
        store,
        scenario,
        OlympusClient(
            load_config(_write_temp_config(tmp_path)).dcs.olympus,
            transport=httpx.MockTransport(handler),
        ),
    )
    batch = validator.validate_payload(
        run_id,
        Coalition.RED,
        1,
        [
            {
                "action_id": "prio_1",
                "action_type": "set_sector_priority",
                "reason": "Prioritize the west corridor.",
                "sector_id": "central_west",
                "priority": "high",
            },
            {
                "action_id": "deploy_1",
                "action_type": "deploy_reserve_group",
                "reason": "Commit reserve SAM.",
                "reserve_group_id": "red_reserve_sam",
                "target_sector_id": "red_front",
                "role": "defensive",
            },
            {
                "action_id": "move_1",
                "action_type": "reposition_group",
                "reason": "Tighten AD coverage.",
                "group_id": "red_front_ad",
                "destination_type": "zone",
                "destination_id": "red_front_support_zone",
            },
            {
                "action_id": "posture_1",
                "action_type": "set_group_posture",
                "reason": "Keep fires on support.",
                "group_id": "red_fires_group",
                "posture": "support",
            },
            {
                "action_id": "reinforce_1",
                "action_type": "reinforce_control_point",
                "reason": "Protect forward support.",
                "control_point_id": "red_forward_support",
                "group_ids": ["red_ground_group"],
                "reinforcement_role": "defend",
            },
            {
                "action_id": "withdraw_1",
                "action_type": "withdraw_group",
                "reason": "Preserve the screen.",
                "group_id": "red_screen_group",
                "fallback_destination_type": "zone",
                "fallback_destination_id": "red_rear_fallback_zone",
            },
            {
                "action_id": "hold_1",
                "action_type": "hold_action",
                "reason": "No further changes.",
                "scope": "global",
            },
        ],
    )

    execution = engine.execute_validation_batch(
        run_id,
        Coalition.RED,
        1,
        batch,
        model_invocation_id=None,
        observation_id=None,
        now=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
    )

    coalition_state = next(item for item in store.get_coalition_states(run_id) if item.coalition is Coalition.RED)
    reserves = {item.id: item for item in store.get_reserve_groups(run_id, Coalition.RED)}
    groups = {item.id: item for item in store.get_active_groups(run_id)}
    standing_orders = engine.summarize_standing_orders(run_id, Coalition.RED)
    execution_records = store.list_execution_batches(run_id, Coalition.RED)
    execution_notes = store.list_current_execution_notes(run_id, Coalition.RED)
    standing_order_history = store.list_execution_standing_order_history(run_id)

    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.lifecycle_state is ExecutionLifecycleState.COMPLETED
    assert coalition_state.budget_remaining < 24
    assert reserves["red_reserve_sam"].available is False
    assert groups["deployed_red_reserve_sam"].sector_id == "red_front"
    assert groups["red_fires_group"].posture is GroupPosture.SUPPORT
    assert groups["red_ground_group"].control_point_id == "red_forward_support"
    assert groups["red_screen_group"].posture is GroupPosture.FALLBACK
    assert standing_orders["standing_order_count"] >= 5
    assert execution_records[-1].lifecycle_state is ExecutionLifecycleState.COMPLETED
    assert execution_notes
    assert standing_order_history


def test_execution_engine_partial_failure_isolated_to_applied_commands(tmp_path: Path) -> None:
    _, scenario, store, run_id, _, validator = _build_execution_services(tmp_path)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/control-points/reinforce"):
            calls["count"] += 1
            if calls["count"] == 2:
                return httpx.Response(500, json={"accepted": False, "detail": "second command failed"})
        return httpx.Response(200, json={"accepted": True})

    engine = ExecutionEngine(
        store,
        scenario,
        OlympusClient(
            load_config(_write_temp_config(tmp_path)).dcs.olympus,
            transport=httpx.MockTransport(handler),
        ),
    )
    batch = validator.validate_payload(
        run_id,
        Coalition.RED,
        2,
        [
            {
                "action_id": "reinforce_partial",
                "action_type": "reinforce_control_point",
                "reason": "Commit two groups.",
                "control_point_id": "red_forward_support",
                "group_ids": ["red_ground_group", "red_screen_group"],
                "reinforcement_role": "defend",
            }
        ],
    )

    execution = engine.execute_validation_batch(
        run_id,
        Coalition.RED,
        2,
        batch,
        model_invocation_id=None,
        observation_id=None,
    )

    groups = {item.id: item for item in store.get_active_groups(run_id)}

    assert execution.status is ExecutionStatus.PARTIALLY_SUCCEEDED
    assert execution.lifecycle_state is ExecutionLifecycleState.COMPLETED
    assert execution.results[0].status is ExecutionStatus.PARTIALLY_SUCCEEDED
    assert groups["red_ground_group"].control_point_id == "red_forward_support"
    assert groups["red_screen_group"].control_point_id != "red_forward_support"


def test_live_command_loop_persists_execution_links(tmp_path: Path) -> None:
    config, scenario, store, run_id, observation_builder, validator = _build_execution_services(tmp_path)

    def model_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            payload = json.loads(request.content.decode("utf-8"))
            is_red = '"coalition": "red"' in payload["messages"][1]["content"]
            response_content = (
                '{"actions":[{"action_id":"red_hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'
                if is_red
                else '{"actions":[{"action_id":"blue_hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'
            )
            return httpx.Response(200, json={"choices": [{"message": {"content": response_content}}]})
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    registry = build_model_registry(
        config,
        transports={
            "red_backend": httpx.MockTransport(model_handler),
            "blue_backend": httpx.MockTransport(model_handler),
        },
    )
    engine = ExecutionEngine(
        store,
        scenario,
        OlympusClient(config.dcs.olympus, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"accepted": True}))),
    )
    runner = LiveCommandLoopRunner(store, observation_builder, validator, registry, engine)

    cycle = runner.run_live_cycle(run_id, 1, now=datetime(2026, 4, 5, 12, 0, tzinfo=UTC))
    executions = store.list_execution_batches(run_id)

    assert cycle.id is not None
    assert cycle.red_execution_batch_id is not None
    assert cycle.blue_execution_batch_id is not None
    assert cycle.classification == "both_succeeded"
    assert len(executions) == 2
    assert all(batch.lifecycle_state is ExecutionLifecycleState.COMPLETED for batch in executions)
    assert store.get_run_summary(run_id)["execution_batch_count"] == 2
    registry.close()


def test_live_command_loop_stops_when_run_is_paused(tmp_path: Path) -> None:
    config, scenario, store, run_id, observation_builder, validator = _build_execution_services(tmp_path)

    def model_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"actions":[{"action_id":"hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'}}]},
            )
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    registry = build_model_registry(
        config,
        transports={"red_backend": httpx.MockTransport(model_handler), "blue_backend": httpx.MockTransport(model_handler)},
    )
    engine = ExecutionEngine(
        store,
        scenario,
        OlympusClient(config.dcs.olympus, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"accepted": True}))),
    )
    runner = LiveCommandLoopRunner(store, observation_builder, validator, registry, engine)
    store.update_run_status(run_id, RunLifecycleStatus.RUNNING, changed_at=datetime.now(UTC))
    store.update_run_status(run_id, RunLifecycleStatus.PAUSED, changed_at=datetime.now(UTC))

    loop = runner.run_live_loop(run_id, start_decision_cycle=1, cycles=2)

    registry.close()
    engine.olympus.close()

    assert loop.completed_cycles == 0
    assert loop.stopped_early is True
    assert loop.terminal_run_status is RunLifecycleStatus.PAUSED


def test_execution_engine_launches_air_package_and_updates_inventory(tmp_path: Path) -> None:
    config, scenario, store, run_id, validator = _build_air_execution_services(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"accepted": True, "path": request.url.path})

    engine = ExecutionEngine(
        store,
        scenario,
        OlympusClient(
            config.dcs.olympus,
            transport=httpx.MockTransport(handler),
        ),
    )
    batch = validator.validate_payload(
        run_id,
        Coalition.RED,
        4,
        [
            {
                "action_id": "launch_cap",
                "action_type": "launch_air_package",
                "reason": "Establish a terrain-safe CAP.",
                "inventory_id": "red_fixed_wing_inventory",
                "package_type": "cap",
                "aircraft_count": 2,
                "route_legs": [
                    {"reference_type": "landmark", "reference_id": "mountain_peak", "altitude_ft_msl": 3000},
                    {"reference_type": "sector", "reference_id": "central_air_corridor", "altitude_ft_msl": 5000},
                ],
                "target_reference_type": "sector",
                "target_reference_id": "central_air_corridor",
            }
        ],
    )

    execution = engine.execute_validation_batch(
        run_id,
        Coalition.RED,
        4,
        batch,
        model_invocation_id=None,
        observation_id=None,
        now=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
    )

    inventories = {item.id: item for item in store.get_air_package_inventories(run_id, Coalition.RED)}
    packages = {item.package_id: item for item in store.list_air_packages(run_id, Coalition.RED)}

    assert execution.status is ExecutionStatus.SUCCEEDED
    assert inventories["red_fixed_wing_inventory"].available_count == 6
    assert packages
    assert next(iter(packages.values())).normalized_route_legs[0].altitude_ft_msl == 6200
