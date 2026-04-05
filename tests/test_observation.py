from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dcs_dungeon_master.core.config import FogOfWarConfig
from dcs_dungeon_master.core.enums import Coalition
from dcs_dungeon_master.integration.types import GrpcStreamEnvelope, OlympusAirfieldSnapshot
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.world_state import WorldStateRepository, WorldStateUpdater


def _build_observation_services(tmp_path: Path):
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    run_id = store.create_run_from_scenario(scenario)
    updater = WorldStateUpdater(WorldStateRepository(store), scenario)
    fusion = SensorFusionService(store, scenario, FogOfWarConfig())
    builder = ObservationBuilder(store, scenario, fusion)
    return store, scenario, run_id, updater, fusion, builder


def _seed_detected_enemy(run_id: str, updater: WorldStateUpdater, occurred_at: datetime) -> None:
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
        occurred_at=occurred_at,
    )


def test_build_observation_pair_enforces_side_isolation(tmp_path: Path) -> None:
    _, _, run_id, updater, _, builder = _build_observation_services(tmp_path)
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    _seed_detected_enemy(run_id, updater, now)

    red_artifact, blue_artifact = builder.build_observation_pair(
        run_id,
        1,
        now=now + timedelta(seconds=30),
        persist=True,
    )

    red_payload = asdict(red_artifact.observation)
    blue_payload = asdict(blue_artifact.observation)

    assert set(red_payload) == {
        "meta",
        "commander_state",
        "scenario_state",
        "resource_state",
        "sector_summary",
        "friendly_forces",
        "enemy_contacts",
        "recent_changes",
        "standing_orders",
        "requests_for_decision",
    }
    assert len(red_artifact.observation.friendly_forces) == 5
    assert any(contact.contact_id == "blue_detected_sam" for contact in red_artifact.observation.enemy_contacts)
    assert all(contact.contact_id != "blue_detected_sam" for contact in blue_artifact.observation.enemy_contacts)
    assert "blue_rear_ad" not in red_artifact.narrative
    assert len(blue_artifact.observation.friendly_forces) == 5


def test_observation_recent_changes_capture_salient_deltas(tmp_path: Path) -> None:
    store, _, run_id, updater, _, builder = _build_observation_services(tmp_path)
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    _seed_detected_enemy(run_id, updater, now)

    first = builder.build_observation(run_id, Coalition.RED, 1, now=now + timedelta(seconds=30), persist=True)
    updater.apply_integration_updates(
        run_id,
        airfield_snapshots=(
            OlympusAirfieldSnapshot(
                airfield_id="central_objective_west",
                name="Greater Tunb Objective",
                owner="red",
            ),
        ),
        occurred_at=now + timedelta(seconds=60),
    )
    second = builder.build_observation(
        run_id,
        Coalition.RED,
        2,
        now=now + timedelta(seconds=1000),
        seconds_since_last_cycle=970,
        persist=True,
    )

    latest = store.get_latest_observation(run_id, Coalition.RED)

    assert first.observation.recent_changes == ()
    assert any("new_contact_blue_detected_sam" in change or "contact_blue_detected_sam_became_stale" in change for change in second.observation.recent_changes)
    assert any("control_point_central_objective_west_status" in change for change in second.observation.recent_changes)
    assert latest.decision_cycle == 2
    assert latest.previous_observation_id == first.id


def test_render_latest_supports_json_and_narrative(tmp_path: Path) -> None:
    _, _, run_id, updater, _, builder = _build_observation_services(tmp_path)
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    _seed_detected_enemy(run_id, updater, now)
    builder.build_observation(run_id, Coalition.RED, 1, now=now + timedelta(seconds=30), persist=True)

    rendered_json = builder.render_latest(run_id, Coalition.RED, "json")
    rendered_bundle = builder.render_latest(run_id, Coalition.RED, "bundle")
    rendered_narrative = builder.render_latest(run_id, Coalition.RED, "narrative")

    assert isinstance(rendered_json, dict)
    assert rendered_json["meta"]["coalition"] == "red"
    assert isinstance(rendered_bundle, dict)
    assert rendered_bundle["canonical"]["meta"]["coalition"] == "red"
    assert isinstance(rendered_narrative, str)
    assert "REDFOR" not in rendered_narrative  # narrative should use actual coalition casing from builder
    assert "RED commander picture" in rendered_narrative
