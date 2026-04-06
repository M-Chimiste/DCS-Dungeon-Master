from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

import httpx

from dcs_dungeon_master.__main__ import main
from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import RunLifecycleStatus
from dcs_dungeon_master.evaluation import EvaluationService
from dcs_dungeon_master.integration.types import GrpcStreamEnvelope
from dcs_dungeon_master.model_adapter import build_model_registry
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

[[models]]
name = "local_default"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true
multimodal = true

[[models]]
name = "hosted_default"
hosting_mode = "hosted"
endpoint = "https://api.example.test/v1"
model = "gpt-5"
enabled = true

[model_routing]
red_backend = "local_default"
blue_backend = "local_default"
red_fallback_backend = "hosted_default"
blue_fallback_backend = "hosted_default"

[scenario]
id = "phase1_baseline_persian_gulf"
registry_path = "scenarios/index.toml"

[persistence]
db_path = "{db_path}"
enable_wal = true

[dry_run]
enabled = true
summary_output = "text"

[multimodal]
enabled = true
output_dir = ".cache/test-attachments"

[evaluation]
profile_name = "phase1_baseline"
decision_cadence_sec = 30
prompt_version = "phase1_default"
resource_settings_version = "phase1_default"
review_policy = "suspicious_only"
fairness_mode = "hybrid"
default_run_cycles = 4

[[evaluation.matrix_cases]]
id = "dry_case"
description = "Dry matrix case."
mode = "dry"
red_backend = "local_default"
blue_backend = "local_default"
decision_cadence_sec = 30
run_cycles = 2
repeat_count = 1

[[evaluation.matrix_cases]]
id = "visual_case"
description = "Unsupported visual case."
mode = "dry"
red_backend = "local_default"
blue_backend = "local_default"
decision_cadence_sec = 30
run_cycles = 1
repeat_count = 1
visual_attachment = true
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


def test_operator_control_cli_and_replay_export(tmp_path: Path, capsys) -> None:
    config_path = _write_temp_config(tmp_path)
    init_exit_code = main(["init-state", "--config", str(config_path)])
    init_payload = json.loads(capsys.readouterr().out)
    run_id = init_payload["run_id"]

    status_exit = main(["run-status", "--config", str(config_path), "--run-id", run_id])
    status_payload = json.loads(capsys.readouterr().out)
    start_exit = main(["run-start", "--config", str(config_path), "--run-id", run_id])
    start_payload = json.loads(capsys.readouterr().out)
    pause_exit = main(["run-pause", "--config", str(config_path), "--run-id", run_id])
    pause_payload = json.loads(capsys.readouterr().out)
    resume_exit = main(["run-resume", "--config", str(config_path), "--run-id", run_id])
    resume_payload = json.loads(capsys.readouterr().out)
    summary_exit = main(["run-summary", "--config", str(config_path), "--run-id", run_id])
    summary_payload = json.loads(capsys.readouterr().out)
    export_dir = tmp_path / "bundle"
    export_exit = main(
        ["replay-export", "--config", str(config_path), "--run-id", run_id, "--output", str(export_dir)]
    )
    export_payload = json.loads(capsys.readouterr().out)
    compare_exit = main(
        ["run-compare", "--config", str(config_path), "--left-run-id", run_id, "--right-run-id", run_id]
    )
    compare_payload = json.loads(capsys.readouterr().out)

    assert init_exit_code == 0
    assert status_exit == 0
    assert status_payload["status"] == "created"
    assert start_exit == 0
    assert start_payload["current_status"] == "running"
    assert pause_exit == 0
    assert pause_payload["current_status"] == "paused"
    assert resume_exit == 0
    assert resume_payload["current_status"] == "running"
    assert summary_exit == 0
    assert summary_payload["run"]["run_id"] == run_id
    assert export_exit == 0
    assert export_payload["run_id"] == run_id
    assert (export_dir / "manifest.json").exists()
    assert compare_exit == 0
    assert compare_payload["left_run_id"] == run_id


def test_evaluation_cli_and_matrix_commands(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(EvaluationService, "_run_dry_case", lambda self, *args, **kwargs: None)
    config_path = _write_temp_config(tmp_path)
    init_exit_code = main(["init-state", "--config", str(config_path)])
    init_payload = json.loads(capsys.readouterr().out)
    run_id = init_payload["run_id"]

    eval_run_exit = main(["eval-run", "--config", str(config_path), "--run-id", run_id])
    eval_run_payload = json.loads(capsys.readouterr().out)
    eval_summary_exit = main(["eval-summary", "--config", str(config_path), "--run-id", run_id])
    eval_summary_payload = json.loads(capsys.readouterr().out)
    eval_compare_exit = main(
        ["eval-compare", "--config", str(config_path), "--left-run-id", run_id, "--right-run-id", run_id]
    )
    eval_compare_payload = json.loads(capsys.readouterr().out)
    replay_export_exit = main(
        ["replay-export", "--config", str(config_path), "--run-id", run_id, "--output", str(tmp_path / "eval-bundle")]
    )
    replay_export_payload = json.loads(capsys.readouterr().out)
    review_queue_exit = main(["eval-review-queue", "--config", str(config_path)])
    review_queue_payload = json.loads(capsys.readouterr().out)
    matrix_exit = main(["eval-run-matrix", "--config", str(config_path), "--profile", "phase1_baseline"])
    matrix_payload = json.loads(capsys.readouterr().out)
    matrix_report_exit = main(["eval-matrix-report", "--config", str(config_path), "--profile", "phase1_baseline"])
    matrix_report_payload = json.loads(capsys.readouterr().out)
    closeout_exit = main(["eval-closeout-status", "--config", str(config_path), "--run-id", run_id, "--profile", "phase1_baseline"])
    closeout_payload = json.loads(capsys.readouterr().out)

    assert init_exit_code == 0
    assert eval_run_exit == 0
    assert eval_run_payload["run_id"] == run_id
    assert eval_summary_exit == 0
    assert eval_summary_payload["run_id"] == run_id
    assert eval_compare_exit == 0
    assert eval_compare_payload["left_run_id"] == run_id
    assert replay_export_exit == 0
    assert replay_export_payload["run_id"] == run_id
    assert review_queue_exit == 0
    assert "pending_findings" in review_queue_payload
    assert matrix_exit == 0
    assert matrix_payload["profile_name"] == "phase1_baseline"
    assert matrix_report_exit == 0
    assert matrix_report_payload["profile_name"] == "phase1_baseline"
    assert closeout_exit == 0
    assert closeout_payload["replay_export_present"] is True


def test_check_integration_cli_smoke(tmp_path: Path, capsys, monkeypatch) -> None:
    @dataclass(slots=True, frozen=True)
    class FakeHealth:
        service: str
        endpoint: str
        healthy: bool
        detail: str
        status: str = "healthy"
        attempt_count: int = 1

    class FakeService:
        def __init__(self, service: str, endpoint: str) -> None:
            self._health = FakeHealth(service=service, endpoint=endpoint, healthy=True, detail="ok")

        def check_health(self) -> FakeHealth:
            return self._health

    class FakeIntegrations:
        olympus = FakeService("olympus", "http://127.0.0.1:4512")
        dcs_grpc = FakeService("dcs_grpc", "127.0.0.1:50051")

        def close(self) -> None:
            return None

    monkeypatch.setattr("dcs_dungeon_master.__main__.build_integration_services", lambda config: FakeIntegrations())

    config_path = _write_temp_config(tmp_path)
    exit_code = main(["check-integration", "--config", str(config_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["olympus"]["healthy"] is True
    assert payload["dcs_grpc"]["detail"] == "ok"


def test_ingest_step_cli_smoke(tmp_path: Path, capsys, monkeypatch) -> None:
    class FakeIntegrations:
        olympus = type(
            "FakeOlympus",
            (),
            {
                "get_mission_snapshot": staticmethod(lambda: type("Mission", (), {"theater": "Persian Gulf", "mission_name": "Test", "mission_time": "12:00:00Z", "weather_summary": "clear"})()),
                "get_units_snapshot": staticmethod(lambda: ()),
                "get_airfields_snapshot": staticmethod(lambda: ()),
                "close": staticmethod(lambda: None),
            },
        )()
        dcs_grpc = type(
            "FakeGrpc",
            (),
            {
                "get_mission_metadata": staticmethod(lambda: type("GrpcMeta", (), {"theater": "Persian Gulf", "mission_name": "Test", "mission_time": "12:00:00Z"})()),
                "iter_unit_events": staticmethod(lambda **kwargs: iter(())),
                "iter_mission_events": staticmethod(lambda: iter(())),
            },
        )()

        def close(self) -> None:
            return None

    monkeypatch.setattr("dcs_dungeon_master.__main__.build_integration_services", lambda config: FakeIntegrations())

    config_path = _write_temp_config(tmp_path)
    init_exit = main(["init-state", "--config", str(config_path)])
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    ingest_exit = main(["ingest-step", "--config", str(config_path), "--run-id", run_id])
    ingest_payload = json.loads(capsys.readouterr().out)

    assert init_exit == 0
    assert ingest_exit == 0
    assert ingest_payload["normalized_batch"]["mission_snapshot_present"] is True


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
    assert summary_payload["attachment_count"] == 1
    assert render_json_exit == 0
    assert render_json_payload["meta"]["coalition"] == "red"
    assert len(render_json_payload["attachments"]) == 1
    assert render_narrative_exit == 0
    assert "RED commander picture" in render_narrative_payload


def test_action_validation_cli_smoke(tmp_path: Path, capsys) -> None:
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "state.sqlite3"
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    store = SQLiteStateStore(db_path)
    run_id = store.create_run_from_scenario(scenario)

    validate_exit = main(
        [
            "validate-actions",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
            "--decision-cycle",
            "1",
            "--input",
            json.dumps(
                [
                    {
                        "action_id": "act_001",
                        "action_type": "deploy_reserve_group",
                        "reason": "Commit frontline reserve.",
                        "reserve_group_id": "red_reserve_sam",
                        "target_sector_id": "red_front",
                        "role": "defensive",
                    }
                ]
            ),
        ]
    )
    validate_payload = json.loads(capsys.readouterr().out)
    summary_exit = main(
        [
            "action-validation-summary",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
        ]
    )
    summary_payload = json.loads(capsys.readouterr().out)

    assert validate_exit == 0
    assert validate_payload["coalition"] == "red"
    assert validate_payload["results"][0]["status"] == "accepted"
    assert summary_exit == 0
    assert summary_payload["accepted_count"] == 1
    assert summary_payload["results"][0]["action_id"] == "act_001"


def test_model_backend_and_decision_cycle_cli_smoke(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "state.sqlite3"
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    store = SQLiteStateStore(db_path)
    run_id = store.create_run_from_scenario(scenario)
    config = load_config(config_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gemma-4-26b-a4b-it"}]})
        if request.url.path.endswith("/chat/completions"):
            payload = json.loads(request.content.decode("utf-8"))
            is_red = '"coalition": "red"' in payload["messages"][1]["content"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"actions":[{"action_id":"red_hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'
                                    if is_red
                                    else '{"actions":[{"action_id":"blue_hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'
                                )
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    monkeypatch.setattr(
        "dcs_dungeon_master.__main__.build_model_registry",
        lambda config: build_model_registry(
            config,
            transports={backend.name: httpx.MockTransport(handler) for backend in config.models},
        ),
    )
    monkeypatch.setattr(
        "dcs_dungeon_master.__main__.build_integration_services",
        lambda config: type(
            "FakeIntegrations",
            (),
            {
                "olympus": type(
                    "FakeOlympus",
                    (),
                    {
                        "endpoint": "http://127.0.0.1:4512",
                        "check_health": staticmethod(lambda: type("Health", (), {"service": "olympus", "endpoint": "http://127.0.0.1:4512", "healthy": True, "detail": "ok", "status": "healthy", "attempt_count": 1})()),
                        "get_mission_snapshot": staticmethod(lambda: type("Mission", (), {"theater": "Persian Gulf", "mission_name": "Test", "mission_time": "12:00:00Z", "weather_summary": "clear"})()),
                        "get_units_snapshot": staticmethod(lambda: ()),
                        "get_airfields_snapshot": staticmethod(lambda: ()),
                        "build_write_request": staticmethod(lambda path, payload, method="POST": type("Req", (), {"method": method, "path": path, "payload": payload, "headers": {}})()),
                        "send_write_request": staticmethod(lambda request: {"accepted": True}),
                        "close": staticmethod(lambda: None),
                    },
                )(),
                "dcs_grpc": type(
                    "FakeGrpc",
                    (),
                    {
                        "endpoint": "127.0.0.1:50051",
                        "check_health": staticmethod(lambda: type("Health", (), {"service": "dcs_grpc", "endpoint": "127.0.0.1:50051", "healthy": True, "detail": "ok", "status": "healthy", "attempt_count": 1})()),
                        "get_mission_metadata": staticmethod(lambda: type("GrpcMeta", (), {"theater": "Persian Gulf", "mission_name": "Test", "mission_time": "12:00:00Z"})()),
                        "iter_unit_events": staticmethod(lambda **kwargs: iter(())),
                        "iter_mission_events": staticmethod(lambda: iter(())),
                    },
                )(),
                "check_health": staticmethod(
                    lambda: (
                        type("Health", (), {"service": "olympus", "endpoint": "http://127.0.0.1:4512", "healthy": True, "detail": "ok", "status": "healthy", "attempt_count": 1})(),
                        type("Health", (), {"service": "dcs_grpc", "endpoint": "127.0.0.1:50051", "healthy": True, "detail": "ok", "status": "healthy", "attempt_count": 1})(),
                    )
                ),
                "close": staticmethod(lambda: None),
            },
        )(),
    )

    health_exit = main(["check-model-backends", "--config", str(config_path)])
    health_payload = json.loads(capsys.readouterr().out)
    cycle_exit = main(
        [
            "run-decision-cycle",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--decision-cycle",
            "1",
        ]
    )
    cycle_payload = json.loads(capsys.readouterr().out)
    summary_exit = main(
        [
            "model-response-summary",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
        ]
    )
    summary_payload = json.loads(capsys.readouterr().out)
    live_cycle_exit = main(
        [
            "run-live-cycle",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--decision-cycle",
            "2",
        ]
    )
    live_cycle_payload = json.loads(capsys.readouterr().out)
    execution_summary_exit = main(
        [
            "execution-summary",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
        ]
    )
    execution_summary_payload = json.loads(capsys.readouterr().out)
    standing_orders_exit = main(
        [
            "standing-orders-summary",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--coalition",
            "red",
        ]
    )
    standing_orders_payload = json.loads(capsys.readouterr().out)
    execution_records_exit = main(
        [
            "execution-records",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
        ]
    )
    execution_records_payload = json.loads(capsys.readouterr().out)
    audit_exit = main(
        [
            "audit-milestones",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
        ]
    )
    audit_payload = json.loads(capsys.readouterr().out)

    assert health_exit == 0
    assert any(item["healthy"] is True for item in health_payload["health"])
    assert health_payload["capabilities"][0]["supports_structured_output"] is True
    assert health_payload["routing"]["red"]["fallback"] == "hosted_default"
    assert cycle_exit == 0
    assert cycle_payload["decision_cycle"] == 1
    assert cycle_payload["classification"] == "both_succeeded"
    assert summary_exit == 0
    assert summary_payload["status"] == "succeeded"
    assert summary_payload["parsed_action_count"] == 1
    assert live_cycle_exit == 0
    assert live_cycle_payload["decision_cycle"] == 2
    assert live_cycle_payload["red_execution_batch_id"] is not None
    assert execution_summary_exit == 0
    assert execution_summary_payload["status"] in {"succeeded", "no_change"}
    assert standing_orders_exit == 0
    assert "standing_order_count" in standing_orders_payload
    assert execution_records_exit == 0
    assert len(execution_records_payload) >= 2
    assert audit_exit == 0
    assert any(check["milestone"] == "Milestone 6" and check["passed"] is True for check in audit_payload["checks"])
    assert any(check["milestone"] == "Milestone 7" and check["passed"] is True for check in audit_payload["checks"])
    assert any(check["milestone"] == "Milestone 8" and check["passed"] is True for check in audit_payload["checks"])
    assert any(check["milestone"] == "Milestone 9" for check in audit_payload["checks"])


def test_run_decision_cycle_cli_marks_run_failed_on_runtime_exception(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "state.sqlite3"
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    store = SQLiteStateStore(db_path)
    run_id = store.create_run_from_scenario(scenario)

    monkeypatch.setattr(
        "dcs_dungeon_master.__main__.build_integration_services",
        lambda config: type(
            "FakeIntegrations",
            (),
            {
                "olympus": object(),
                "dcs_grpc": object(),
                "close": staticmethod(lambda: None),
            },
        )(),
    )
    monkeypatch.setattr(
        "dcs_dungeon_master.__main__.DryDecisionLoopRunner.run_decision_cycle",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic loop crash")),
    )

    exit_code = main(
        [
            "run-decision-cycle",
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--decision-cycle",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    final_state = store.get_run_control_state(run_id)

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert "synthetic loop crash" in payload["error"]
    assert final_state.status is RunLifecycleStatus.FAILED
    assert final_state.terminal_reason == "synthetic loop crash"
