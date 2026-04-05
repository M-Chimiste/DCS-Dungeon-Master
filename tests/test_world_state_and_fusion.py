from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from dcs_dungeon_master.core.config import FogOfWarConfig
from dcs_dungeon_master.core.enums import Coalition, ConfidenceBand, KnowledgeLevel
from dcs_dungeon_master.integration.types import GrpcStreamEnvelope, OlympusMissionSnapshot, OlympusUnitSnapshot
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.world_state import KnowledgeDebugView, WorldStateRepository, WorldStateUpdater


def _build_services(tmp_path: Path, *, fog_of_war: FogOfWarConfig | None = None):
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    run_id = store.create_run_from_scenario(scenario)
    repository = WorldStateRepository(store)
    updater = WorldStateUpdater(repository, scenario)
    fusion = SensorFusionService(store, scenario, fog_of_war or FogOfWarConfig())
    debug_view = KnowledgeDebugView(store, fusion)
    return store, scenario, run_id, repository, updater, fusion, debug_view


def test_world_state_ingest_creates_groups_and_evidence(tmp_path: Path) -> None:
    store, _, run_id, repository, updater, _, _ = _build_services(tmp_path)
    occurred_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

    snapshot = updater.apply_integration_updates(
        run_id,
        mission_snapshot=OlympusMissionSnapshot(theater="Persian Gulf", mission_time="12:00:00Z"),
        unit_snapshots=(
            OlympusUnitSnapshot(
                unit_id="blue_detected_sam",
                name="Blue Detected SAM",
                unit_type="mobile_sam_reserve",
                category="air_defense",
                coalition="blue",
                lat=25.96,
                lng=55.84,
                alt=100.0,
                heading=180.0,
                speed=0.0,
                health=1.0,
            ),
        ),
        occurred_at=occurred_at,
    )

    summary = repository.summarize(run_id)

    assert snapshot.mission_time == "12:00:00Z"
    assert any(group.id == "blue_detected_sam" for group in snapshot.groups)
    assert next(group for group in snapshot.groups if group.id == "blue_detected_sam").sector_id == "central_east"
    assert len(store.list_evidence_records(run_id)) == 2
    assert summary["group_count"] >= 11
    assert summary["evidence_count"] == 2


def test_sensor_fusion_hides_unobserved_enemy_and_marks_visible_tracks(tmp_path: Path) -> None:
    _, _, run_id, repository, updater, fusion, debug_view = _build_services(tmp_path)
    occurred_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

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

    result = fusion.preview(run_id, now=occurred_at + timedelta(seconds=30))
    red_state = next(state for state in result.knowledge_states if state.coalition is Coalition.RED)
    blue_state = next(state for state in result.knowledge_states if state.coalition is Coalition.BLUE)
    comparison = debug_view.compare_truth_vs_knowledge(run_id, Coalition.RED)

    assert "red_front_ad" in red_state.known_friendly_group_ids
    assert any(track.track_id == "blue_detected_sam" for track in red_state.contact_tracks)
    assert all(track.track_id != "blue_rear_ad" for track in red_state.contact_tracks)
    assert all(track.track_id != "blue_detected_sam" for track in blue_state.contact_tracks)
    track = next(track for track in red_state.contact_tracks if track.track_id == "blue_detected_sam")
    assert track.knowledge_level is KnowledgeLevel.CONFIRMED_TYPE
    assert track.confidence in {ConfidenceBand.HIGH, ConfidenceBand.MEDIUM}
    assert "blue_rear_ad" in comparison["hidden_enemy_group_ids"]


def test_sensor_fusion_marks_stale_and_archived_tracks(tmp_path: Path) -> None:
    _, _, run_id, _, updater, fusion, _ = _build_services(
        tmp_path,
        fog_of_war=FogOfWarConfig(freshness_window_sec=60, decay_window_sec=120, archive_window_sec=180),
    )
    occurred_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

    updater.apply_integration_updates(
        run_id,
        grpc_events=(
            GrpcStreamEnvelope(
                stream_name="stream_units",
                entity_id="blue_mobile_column",
                event_type="unit",
                coalition="red",
                payload={
                    "observed_by": "red",
                    "target_coalition": "blue",
                    "unit_type": "mechanized_reserve",
                    "category": "ground",
                    "lat": 26.30,
                    "lng": 55.30,
                    "speed": 18.0,
                },
            ),
        ),
        occurred_at=occurred_at,
    )

    stale_result = fusion.preview(run_id, now=occurred_at + timedelta(seconds=150))
    red_state = next(state for state in stale_result.knowledge_states if state.coalition is Coalition.RED)
    stale_track = next(track for track in red_state.contact_tracks if track.track_id == "blue_mobile_column")
    archived_result = fusion.preview(run_id, now=occurred_at + timedelta(seconds=220))
    archived_red_state = next(state for state in archived_result.knowledge_states if state.coalition is Coalition.RED)
    archived_track = next(track for track in archived_red_state.archived_tracks if track.track_id == "blue_mobile_column")

    assert stale_track.stale is True
    assert stale_track.archived is False
    assert stale_track.confidence is ConfidenceBand.LOW
    assert stale_track.estimated_sector_id is not None
    assert archived_track.archived is True
