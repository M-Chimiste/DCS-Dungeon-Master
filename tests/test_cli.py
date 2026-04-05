from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

from dcs_dungeon_master.__main__ import main
from dcs_dungeon_master.integration.types import GrpcStreamEnvelope
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.world_state import WorldStateRepository, WorldStateUpdater


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


def test_run_dry_cli_smoke(tmp_path: Path, capsys) -> None:
    config_path = _write_temp_config(tmp_path)
    exit_code = main(["run-dry", "--config", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["dry_run"] is True
    assert payload["scenario_id"] == "phase1_baseline_persian_gulf"
    assert payload["state_summary"]["event_count"] > 0


def test_check_scenario_cli_smoke(tmp_path: Path, capsys) -> None:
    config_path = _write_temp_config(tmp_path)
    exit_code = main(["check-scenario", "--config", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["scenario_id"] == "phase1_baseline_persian_gulf"
    assert payload["theater"] == "Persian Gulf"
    assert payload["sector_count"] == 7


def test_init_state_and_state_summary_cli_smoke(tmp_path: Path, capsys) -> None:
    config_path = _write_temp_config(tmp_path)
    init_exit_code = main(["init-state", "--config", str(config_path)])
    init_payload = json.loads(capsys.readouterr().out)

    assert init_exit_code == 0
    assert init_payload["scenario_id"] == "phase1_baseline_persian_gulf"
    run_id = init_payload["run_id"]

    summary_exit_code = main(["state-summary", "--config", str(config_path), "--run-id", run_id])
    summary_payload = json.loads(capsys.readouterr().out)

    assert summary_exit_code == 0
    assert summary_payload["run_id"] == run_id
    assert summary_payload["active_group_count"] == 10


def test_check_integration_cli_smoke(tmp_path: Path, capsys, monkeypatch) -> None:
    @dataclass(slots=True, frozen=True)
    class FakeHealth:
        service: str
        endpoint: str
        healthy: bool
        detail: str

    class FakeService:
        def __init__(self, service: str, endpoint: str) -> None:
            self._health = FakeHealth(service=service, endpoint=endpoint, healthy=True, detail="ok")

        def check_health(self) -> FakeHealth:
            return self._health

    class FakeIntegrations:
        olympus = FakeService("olympus", "http://127.0.0.1:4512")
        dcs_grpc = FakeService("dcs_grpc", "127.0.0.1:50051")

    monkeypatch.setattr("dcs_dungeon_master.__main__.build_integration_services", lambda config: FakeIntegrations())

    config_path = _write_temp_config(tmp_path)
    exit_code = main(["check-integration", "--config", str(config_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["olympus"]["healthy"] is True
    assert payload["dcs_grpc"]["detail"] == "ok"


def test_world_state_and_fusion_cli_smoke(tmp_path: Path, capsys) -> None:
    config_path = _write_temp_config(tmp_path)
    config_db_path = tmp_path / "state.sqlite3"
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    store = SQLiteStateStore(config_db_path)
    run_id = store.create_run_from_scenario(scenario)
    updater = WorldStateUpdater(WorldStateRepository(store), scenario)
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
                    "lat": 25.96,
                    "lng": 55.84,
                },
            ),
        ),
        occurred_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
    )

    world_exit = main(["world-state-summary", "--config", str(config_path), "--run-id", run_id])
    world_payload = json.loads(capsys.readouterr().out)
    fusion_exit = main(["fusion-summary", "--config", str(config_path), "--run-id", run_id, "--coalition", "red"])
    fusion_payload = json.loads(capsys.readouterr().out)
    track_exit = main(
        [
            "debug-contact-track",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
            "--track-id",
            "blue_detected_sam",
        ]
    )
    track_payload = json.loads(capsys.readouterr().out)
    compare_exit = main(
        [
            "compare-truth-vs-knowledge",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
        ]
    )
    compare_payload = json.loads(capsys.readouterr().out)

    assert world_exit == 0
    assert world_payload["evidence_count"] == 1
    assert fusion_exit == 0
    assert fusion_payload["coalition"] == "red"
    assert fusion_payload["contact_track_count"] == 1
    assert track_exit == 0
    assert track_payload["found"] is True
    assert track_payload["track"]["track_id"] == "blue_detected_sam"
    assert compare_exit == 0
    assert "blue_rear_ad" in compare_payload["hidden_enemy_group_ids"]


def test_observation_cli_smoke(tmp_path: Path, capsys) -> None:
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "state.sqlite3"
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    store = SQLiteStateStore(db_path)
    run_id = store.create_run_from_scenario(scenario)
    updater = WorldStateUpdater(WorldStateRepository(store), scenario)
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
                    "lat": 25.96,
                    "lng": 55.84,
                },
            ),
        ),
        occurred_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
    )

    build_exit = main(
        [
            "build-observation",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
            "--decision-cycle",
            "1",
        ]
    )
    build_payload = json.loads(capsys.readouterr().out)
    pair_exit = main(
        [
            "build-observation-pair",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--decision-cycle",
            "2",
        ]
    )
    pair_payload = json.loads(capsys.readouterr().out)
    summary_exit = main(
        [
            "observation-summary",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
        ]
    )
    summary_payload = json.loads(capsys.readouterr().out)
    render_json_exit = main(
        [
            "render-observation",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
            "--format",
            "json",
        ]
    )
    render_json_payload = json.loads(capsys.readouterr().out)
    render_narrative_exit = main(
        [
            "render-observation",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
            "--format",
            "narrative",
        ]
    )
    render_narrative_payload = capsys.readouterr().out.strip()

    assert build_exit == 0
    assert build_payload["canonical"]["meta"]["coalition"] == "red"
    assert pair_exit == 0
    assert pair_payload["blue"]["canonical"]["meta"]["coalition"] == "blue"
    assert summary_exit == 0
    assert summary_payload["enemy_contact_count"] >= 1
    assert render_json_exit == 0
    assert render_json_payload["meta"]["coalition"] == "red"
    assert render_narrative_exit == 0
    assert "RED commander picture" in render_narrative_payload
