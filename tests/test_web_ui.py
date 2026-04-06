from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil

import pytest

from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import RunLifecycleStatus
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.integration.types import GrpcStreamEnvelope
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.core.models import ScenarioDraftPatch, SetupCheckResult, SetupConfigWriteResult, SetupRecommendation, SetupWizardStatus
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.web_ui import PydcsReferenceService, ScenarioDraftService, WebUiService
from dcs_dungeon_master.world_state import WorldStateRepository, WorldStateUpdater


def _write_temp_config(tmp_path: Path, *, registry_path: Path = Path("scenarios/index.toml")) -> Path:
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
multimodal = false

[model_routing]
red_backend = "local_default"
blue_backend = "local_default"

[scenario]
id = "phase1_baseline_persian_gulf"
registry_path = "{registry_path}"

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


def _copy_registry(tmp_path: Path) -> Path:
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    shutil.copy2("scenarios/index.toml", scenario_dir / "index.toml")
    for file_name in (
        "phase1_baseline_persian_gulf.toml",
        "phase1_baseline_syria.toml",
        "phase1_baseline_afghanistan.toml",
        "phase1_baseline_iraq.toml",
        "phase1_baseline_fulda_gap.toml",
    ):
        shutil.copy2(Path("scenarios") / file_name, scenario_dir / file_name)
    return scenario_dir / "index.toml"


def _seed_run_with_observations(config_path: Path) -> tuple[SQLiteStateStore, str]:
    config = load_config(config_path)
    scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
    store = SQLiteStateStore(config.persistence.db_path)
    run_id = store.create_run_from_scenario(
        scenario,
        mode="dry",
        config_digest="web-ui-test",
        config_snapshot=config.to_dict(),
        red_backend_name=config.model_routing.red_backend,
        blue_backend_name=config.model_routing.blue_backend,
    )
    updater = WorldStateUpdater(WorldStateRepository(store), scenario)
    fusion = SensorFusionService(store, scenario, config.fog_of_war)
    builder = ObservationBuilder(store, scenario, fusion, config.multimodal)
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    updater.apply_integration_updates(
        run_id,
        grpc_events=(
            GrpcStreamEnvelope(
                stream_name="stream_units",
                entity_id="blue_detected_sam",
                event_type="unit",
                coalition="red",
                payload={
                    "observed_by": "red",
                    "target_coalition": "blue",
                    "unit_type": "mobile_sam_reserve",
                    "category": "air_defense",
                    "name": "Blue Detected SAM",
                    "lat": 25.96,
                    "lng": 55.84,
                    "alt": 100.0,
                    "speed": 12.0,
                },
            ),
        ),
        occurred_at=now,
    )
    builder.build_observation_pair(run_id, 1, now=now + timedelta(seconds=30), persist=True)
    return store, run_id


def test_scenario_draft_round_trip_validate_and_save_as(tmp_path: Path) -> None:
    registry_path = _copy_registry(tmp_path)
    store = SQLiteStateStore(tmp_path / "drafts.sqlite3")
    store.initialize_schema()
    service = ScenarioDraftService(store, index_path=registry_path, reference_service=PydcsReferenceService())

    draft = service.create_draft("phase1_baseline_persian_gulf")
    scenario_payload = {
        **draft.scenario,
        "name": "Edited Gulf Scenario",
        "sectors": [
            {**sector, "radius_nm": 42.0} if sector["id"] == draft.scenario["sectors"][0]["id"] else sector
            for sector in draft.scenario["sectors"]
        ],
    }
    updated = service.update_draft(draft.draft_id, patch=ScenarioDraftPatch(scenario=scenario_payload))
    validation = service.validate_draft(draft.draft_id)
    saved = service.save_as_scenario(draft.draft_id, scenario_id="ui_saved_gulf", name="UI Saved Gulf")

    assert updated.name == "Edited Gulf Scenario"
    assert validation["valid"] is True
    assert Path(saved["scenario_path"]).exists()
    assert "ui_saved_gulf" in registry_path.read_text(encoding="utf-8")


def test_scenario_draft_lifecycle_and_structured_validation(tmp_path: Path) -> None:
    registry_path = _copy_registry(tmp_path)
    store = SQLiteStateStore(tmp_path / "drafts.sqlite3")
    store.initialize_schema()
    service = ScenarioDraftService(store, index_path=registry_path, reference_service=PydcsReferenceService())

    draft = service.create_draft("phase1_baseline_persian_gulf")
    renamed = service.rename_draft(draft.draft_id, "Persian Gulf Focus Draft")
    duplicated = service.duplicate_draft(draft.draft_id)
    invalid_scenario = {
        **renamed.scenario,
        "sectors": [
            {**sector, "neighbor_ids": ["missing_sector"]} if sector["id"] == renamed.scenario["sectors"][0]["id"] else sector
            for sector in renamed.scenario["sectors"]
        ],
    }
    service.update_draft(renamed.draft_id, ScenarioDraftPatch(scenario=invalid_scenario))
    validation = service.validate_draft(renamed.draft_id)
    reverted = service.revert_draft(renamed.draft_id)
    deleted = service.delete_draft(duplicated.draft_id)

    assert renamed.display_name == "Persian Gulf Focus Draft"
    assert duplicated.display_name.endswith("Copy")
    assert validation["valid"] is False
    assert validation["issues"][0]["entity_type"] == "sector"
    assert validation["issues"][0]["entity_id"] == renamed.scenario["sectors"][0]["id"]
    assert reverted.scenario["sectors"][0]["neighbor_ids"] != ["missing_sector"]
    assert deleted["deleted"] is True


def test_blank_scenario_draft_creation_export_and_object_crud(tmp_path: Path) -> None:
    registry_path = _copy_registry(tmp_path)
    store = SQLiteStateStore(tmp_path / "drafts.sqlite3")
    store.initialize_schema()
    service = ScenarioDraftService(store, index_path=registry_path, reference_service=PydcsReferenceService())

    blank = service.create_blank_draft(
        scenario_id="blank_test",
        name="Blank Test",
        theater="Syria",
        summary="Blank testing scenario.",
    )
    validation = service.validate_draft(blank.draft_id)
    sector_create = service.create_object(blank.draft_id, "sector", {"center_lat": 35.0, "center_lng": 37.0})
    draft_after_sector = sector_create["draft"]
    control_point_create = service.create_object(
        blank.draft_id,
        "control_point",
        {"sector_id": draft_after_sector.scenario["sectors"][0]["id"]},
    )
    zone_create = service.create_object(
        blank.draft_id,
        "zone",
        {"sector_id": draft_after_sector.scenario["sectors"][0]["id"], "center_lat": 35.1, "center_lng": 37.1},
    )
    active_group_create = service.create_object(blank.draft_id, "active_group", {"coalition": "red"})
    reserve_group_create = service.create_object(blank.draft_id, "reserve_group", {"coalition": "blue"})
    restriction_create = service.create_object(blank.draft_id, "deployment_restriction", {})
    standing_order_create = service.create_object(blank.draft_id, "standing_order", {"coalition": "red"})
    export_result = service.export_draft_toml(blank.draft_id)

    assert blank.source_scenario_id.startswith("__blank__:")
    assert blank.map_reference is not None
    assert validation["valid"] is False
    assert validation["issues"][0]["suggestion"]
    assert sector_create["created_object"]["object"]["id"] == "sector_1"
    assert control_point_create["created_object"]["object"]["sector_id"] == "sector_1"
    assert zone_create["created_object"]["object"]["sector_id"] == "sector_1"
    assert active_group_create["created_object"]["object"]["id"] == "active_group_1"
    assert reserve_group_create["created_object"]["object"]["id"] == "reserve_group_1"
    assert restriction_create["created_object"]["object"]["id"] == "restriction_1"
    assert standing_order_create["created_object"]["object"]["coalition"] == "red"
    assert export_result.filename == "blank_test.toml"
    assert 'id = "blank_test"' in export_result.toml


def test_scenario_draft_delete_blocking_and_duplicate_paths(tmp_path: Path) -> None:
    registry_path = _copy_registry(tmp_path)
    store = SQLiteStateStore(tmp_path / "drafts.sqlite3")
    store.initialize_schema()
    service = ScenarioDraftService(store, index_path=registry_path, reference_service=PydcsReferenceService())

    draft = service.create_draft("phase1_baseline_persian_gulf")
    sector_id = draft.scenario["sectors"][0]["id"]
    control_point_id = draft.scenario["control_points"][0]["id"]
    standing_order = draft.scenario["coalitions"][0]["standing_orders"][0]

    with pytest.raises(PersistenceError):
        service.delete_object(draft.draft_id, "sector", sector_id)

    with pytest.raises(PersistenceError):
        service.delete_object(draft.draft_id, "control_point", control_point_id)

    duplicated_sector = service.duplicate_object(draft.draft_id, "sector", sector_id)
    duplicated_order = service.duplicate_object(
        draft.draft_id,
        "standing_order",
        standing_order["id"],
        {"coalition": draft.scenario["coalitions"][0]["coalition"]},
    )

    assert duplicated_sector["created_object"]["object"]["id"] != sector_id
    assert duplicated_sector["created_object"]["object"]["neighbor_ids"] == []
    assert duplicated_order["created_object"]["object"]["id"] != standing_order["id"]


def test_pydcs_reference_service_returns_authored_fallback() -> None:
    scenario = get_scenario_definition("phase1_baseline_fulda_gap", "scenarios/index.toml")
    reference = PydcsReferenceService().reference_for_scenario(scenario)

    assert reference.reference_status in {"partial", "unsupported"}
    assert reference.sectors
    assert reference.control_points
    assert reference.default_center_lat is not None


def test_web_ui_commander_preview_remains_coalition_safe(tmp_path: Path) -> None:
    config_path = _write_temp_config(tmp_path)
    _, run_id = _seed_run_with_observations(config_path)
    service = WebUiService.from_config_path(config_path)

    status, preview_payload = service.dispatch("GET", f"/api/runs/{run_id}/commander-preview/red")
    world_status, world_payload = service.dispatch("GET", f"/api/runs/{run_id}/world-state")

    assert status == 200
    assert world_status == 200
    assert any(group["coalition"] == "blue" for group in world_payload["world_state"]["groups"])
    assert all(contact["contact_id"] != "blue_rear_ad" for contact in preview_payload["preview"]["observation"]["enemy_contacts"])


def test_web_ui_ops_snapshot_and_scenario_routes(tmp_path: Path) -> None:
    config_path = _write_temp_config(tmp_path)
    _, run_id = _seed_run_with_observations(config_path)
    service = WebUiService.from_config_path(config_path)

    scenarios_status, scenarios_payload = service.dispatch("GET", "/api/scenarios")
    theaters_status, theaters_payload = service.dispatch("GET", "/api/theaters")
    scenario_status, scenario_payload = service.dispatch("GET", "/api/scenarios/phase1_baseline_persian_gulf")
    ops_status, ops_payload = service.dispatch("GET", f"/api/runs/{run_id}/ops")

    assert scenarios_status == 200
    assert theaters_status == 200
    assert scenario_status == 200
    assert ops_status == 200
    assert scenarios_payload["scenarios"]
    assert theaters_payload["theaters"]
    assert scenario_payload["scenario"]["id"] == "phase1_baseline_persian_gulf"
    assert ops_payload["ops"]["run"]["run_id"] == run_id
    assert ops_payload["ops"]["operator_map_layers"]["world_groups"]
    assert ops_payload["ops"]["red_inspection"]["latest_observation"] is not None


def test_web_ui_draft_management_routes(tmp_path: Path) -> None:
    registry_path = _copy_registry(tmp_path)
    config_path = _write_temp_config(tmp_path, registry_path=registry_path)
    service = WebUiService.from_config_path(config_path)

    create_status, create_payload = service.dispatch(
        "POST",
        "/api/scenarios/drafts",
        {"source_scenario_id": "phase1_baseline_persian_gulf"},
    )
    draft_id = create_payload["draft"]["draft_id"]
    list_status, list_payload = service.dispatch("GET", "/api/scenarios/drafts")
    rename_status, rename_payload = service.dispatch(
        "PATCH",
        f"/api/scenarios/drafts/{draft_id}/rename",
        {"display_name": "Ops Test Draft"},
    )
    duplicate_status, duplicate_payload = service.dispatch("POST", f"/api/scenarios/drafts/{draft_id}/duplicate", {})
    revert_status, revert_payload = service.dispatch("POST", f"/api/scenarios/drafts/{draft_id}/revert", {})
    delete_status, delete_payload = service.dispatch("DELETE", f"/api/scenarios/drafts/{duplicate_payload['draft']['draft_id']}")

    assert create_status == 200
    assert list_status == 200
    assert any(item["draft_id"] == draft_id for item in list_payload["drafts"])
    assert rename_status == 200
    assert rename_payload["draft"]["display_name"] == "Ops Test Draft"
    assert duplicate_status == 200
    assert revert_status == 200
    assert revert_payload["draft"]["draft_id"] == draft_id
    assert delete_status == 200
    assert delete_payload["deleted"] is True


def test_web_ui_blank_draft_export_and_object_routes(tmp_path: Path) -> None:
    registry_path = _copy_registry(tmp_path)
    config_path = _write_temp_config(tmp_path, registry_path=registry_path)
    service = WebUiService.from_config_path(config_path)

    create_status, create_payload = service.dispatch(
        "POST",
        "/api/scenarios/drafts",
        {
            "blank_scenario": {
                "scenario_id": "api_blank_test",
                "name": "API Blank Test",
                "theater": "Iraq",
                "summary": "API blank draft.",
            }
        },
    )
    draft_id = create_payload["draft"]["draft_id"]
    sector_status, sector_payload = service.dispatch(
        "POST",
        f"/api/scenarios/drafts/{draft_id}/objects",
        {"object_type": "sector", "center_lat": 34.5, "center_lng": 43.5},
    )
    control_point_status, control_point_payload = service.dispatch(
        "POST",
        f"/api/scenarios/drafts/{draft_id}/objects",
        {"object_type": "control_point", "sector_id": "sector_1"},
    )
    export_status, export_payload = service.dispatch("GET", f"/api/scenarios/drafts/{draft_id}/export.toml")

    assert create_status == 200
    assert create_payload["draft"]["source_scenario_id"].startswith("__blank__:")
    assert sector_status == 200
    assert sector_payload["created_object"]["object"]["id"] == "sector_1"
    assert control_point_status == 200
    assert control_point_payload["created_object"]["object"]["sector_id"] == "sector_1"
    assert export_status == 200
    assert export_payload["export"]["filename"] == "api_blank_test.toml"
    assert 'id = "api_blank_test"' in export_payload["export"]["toml"]


def test_web_ui_model_catalog_and_run_routing_routes(tmp_path: Path) -> None:
    config_path = _write_temp_config(tmp_path)
    service = WebUiService.from_config_path(config_path)

    catalog_status, catalog_payload = service.dispatch("GET", "/api/model-catalog")
    create_status, create_payload = service.dispatch(
        "POST",
        "/api/runs",
        {
            "scenario_id": "phase1_baseline_persian_gulf",
            "mode": "dry",
            "routing": {
                "same_for_both": False,
                "shared_primary_catalog_id": "local_default",
                "red_primary_catalog_id": "local_default",
                "blue_primary_adhoc": {
                    "display_name": "Hosted Blue Override",
                    "endpoint": "https://api.example.test/v1",
                    "model": "gpt-5",
                    "hosting_mode": "hosted",
                },
            },
        },
    )
    run_id = create_payload["run"]["run_id"]
    routing_status, routing_payload = service.dispatch("GET", f"/api/runs/{run_id}/routing")

    assert catalog_status == 200
    assert catalog_payload["catalog"]
    assert create_status == 200
    assert create_payload["run"]["routing"]["red"]["primary_backend_name"] == "local_default"
    assert create_payload["run"]["routing"]["blue"]["primary_source"] == "ad_hoc"
    assert routing_status == 200
    assert routing_payload["run_scoped_backends"][0]["model"] == "gpt-5"


def test_web_ui_routing_patch_requires_created_run_and_rejects_bad_adhoc_payload(tmp_path: Path) -> None:
    config_path = _write_temp_config(tmp_path)
    service = WebUiService.from_config_path(config_path)

    create_status, create_payload = service.dispatch(
        "POST",
        "/api/runs",
        {
            "scenario_id": "phase1_baseline_persian_gulf",
            "mode": "dry",
            "routing": {"shared_primary_catalog_id": "local_default"},
        },
    )
    run_id = create_payload["run"]["run_id"]

    bad_status, bad_payload = service.dispatch(
        "PATCH",
        f"/api/runs/{run_id}/routing",
        {
            "same_for_both": False,
            "shared_primary_catalog_id": "local_default",
            "blue_primary_adhoc": {
                "endpoint": "https://api.example.test/v1",
                "model": "gpt-5",
                "hosting_mode": "moon",
            },
        },
    )
    start_status, _ = service.dispatch("POST", f"/api/runs/{run_id}/start", {})
    locked_status, locked_payload = service.dispatch(
        "PATCH",
        f"/api/runs/{run_id}/routing",
        {"shared_primary_catalog_id": "local_default"},
    )

    assert create_status == 200
    assert bad_status == 400
    assert "hosting_mode must be one of" in bad_payload["error"]
    assert start_status == 200
    assert locked_status == 400
    assert "only be updated while the run is still created" in locked_payload["error"]


def test_web_ui_setup_routes_return_structured_status_and_write_result(tmp_path: Path) -> None:
    config_path = _write_temp_config(tmp_path)
    service = WebUiService.from_config_path(config_path)
    fake_status = SetupWizardStatus(
        config_path=str(config_path),
        saved_games_path=SetupCheckResult("saved_games_path", "ready", True, "Detected path.", "C:/Saved Games/DCS.openbeta"),
        autoexec_status=SetupCheckResult("autoexec_status", "ready", True, "Configured."),
        olympus_status=SetupCheckResult("olympus_status", "ready", True, "ok", "http://127.0.0.1:4512"),
        grpc_status=SetupCheckResult("grpc_status", "ready", True, "ok", "127.0.0.1:50051"),
        config_status=SetupCheckResult("config_status", "ready", True, "loaded", str(config_path)),
        overall_status="ready",
        recommended_actions=(SetupRecommendation("environment_ready", "Environment checks passed."),),
    )
    fake_write = SetupConfigWriteResult(
        output_path=str(tmp_path / "local.toml"),
        written=True,
        changed=True,
        overwritten=False,
        detail="Wrote generated local config.",
        saved_games_path="C:/Saved Games/DCS.openbeta",
    )
    service.setup_wizard.probe = lambda **kwargs: fake_status
    service.setup_wizard.write_local_config = lambda **kwargs: fake_write

    status_code, status_payload = service.dispatch("GET", "/api/setup/status")
    probe_code, probe_payload = service.dispatch("POST", "/api/setup/probe", {"saved_games_path": "C:/Saved Games/DCS.openbeta"})
    write_code, write_payload = service.dispatch("POST", "/api/setup/write-config", {"output": str(tmp_path / "local.toml")})

    assert status_code == 200
    assert probe_code == 200
    assert write_code == 200
    assert status_payload["setup"]["overall_status"] == "ready"
    assert probe_payload["setup"]["saved_games_path"]["detected_value"] == "C:/Saved Games/DCS.openbeta"
    assert write_payload["write_result"]["written"] is True


def test_web_ui_resumable_latest_and_continue_routes(tmp_path: Path) -> None:
    config_path = _write_temp_config(tmp_path)
    store, run_id = _seed_run_with_observations(config_path)
    store.update_run_status(run_id, RunLifecycleStatus.PAUSED, changed_at=datetime(2026, 4, 5, 12, 5, tzinfo=UTC))
    service = WebUiService.from_config_path(config_path)

    resumable_status, resumable_payload = service.dispatch("GET", "/api/runs/resumable")
    latest_status, latest_payload = service.dispatch("GET", "/api/runs/latest")
    continue_status, continue_payload = service.dispatch("POST", f"/api/runs/{run_id}/continue", {})

    assert resumable_status == 200
    assert latest_status == 200
    assert continue_status == 200
    assert resumable_payload["runs"][0]["run_id"] == run_id
    assert latest_payload["run"]["run"]["run_id"] == run_id
    assert continue_payload["run"]["run"]["status"] == "running"
