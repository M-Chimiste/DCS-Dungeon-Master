from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import FairnessReviewStatus, MatrixCaseStatus
from dcs_dungeon_master.evaluation import EvaluationService, build_evaluation_metadata
from dcs_dungeon_master.model_adapter import DryDecisionLoopRunner, build_model_registry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService


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
hosting_mode = "local"
endpoint = "http://127.0.0.1:1235/v1"
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
description = "Dry matrix case"
mode = "dry"
red_backend = "red_backend"
blue_backend = "blue_backend"
decision_cadence_sec = 30
run_cycles = 1
repeat_count = 1

[[evaluation.matrix_cases]]
id = "visual_case"
description = "Visual matrix case"
mode = "dry"
red_backend = "red_backend"
blue_backend = "blue_backend"
decision_cadence_sec = 30
run_cycles = 1
repeat_count = 1
visual_attachment = true
""".strip(),
        encoding="utf-8",
    )
    return config_path


def test_evaluation_service_persists_summary_and_review_flow(tmp_path: Path) -> None:
    config = load_config(_write_temp_config(tmp_path))
    scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
    store = SQLiteStateStore(config.persistence.db_path)
    run_id = store.create_run_from_scenario(
        scenario,
        config_digest="digest",
        config_snapshot=config.to_dict(),
        red_backend_name=config.model_routing.red_backend,
        blue_backend_name=config.model_routing.blue_backend,
        evaluation_metadata=build_evaluation_metadata(config),
    )
    observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war))
    validator = ActionValidator(store, scenario)

    def red_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"actions":[{"action_id":"red_hidden","action_type":"set_sector_priority","reason":"Shift focus","sector_id":"blue_front","priority":"high"}]}'
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    def blue_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"actions":[{"action_id":"blue_hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    registry = build_model_registry(
        config,
        transports={
            "red_backend": httpx.MockTransport(red_handler),
            "blue_backend": httpx.MockTransport(blue_handler),
        },
    )
    try:
        runner = DryDecisionLoopRunner(store, observation_builder, validator, registry)
        runner.run_decision_cycle(run_id, 1, now=datetime(2026, 4, 5, 12, 0, tzinfo=UTC))
    finally:
        registry.close()

    service = EvaluationService(store, scenario)
    summary = service.evaluate_run(run_id)
    findings = store.list_fairness_findings(run_id)

    hidden_finding = next(item for item in findings if item.finding_type == "hidden_targeting")
    reviewed = service.review_finding(
        hidden_finding.id or 0,
        status=FairnessReviewStatus.CLEARED,
        note="Validated as acceptable in this synthetic test.",
    )

    assert summary.fairness_score < 100
    assert summary.findings_pending_review >= 1
    assert service.summarize_run(run_id).run_id == run_id
    assert store.list_evaluation_cycle_metrics(run_id)
    assert hidden_finding.review_status is FairnessReviewStatus.PENDING_REVIEW
    assert reviewed["finding"].review_status is FairnessReviewStatus.CLEARED
    assert reviewed["summary"].fairness_score >= summary.fairness_score


def test_evaluation_matrix_skips_visual_case_and_persists_report(tmp_path: Path, monkeypatch) -> None:
    config = load_config(_write_temp_config(tmp_path))
    scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
    store = SQLiteStateStore(config.persistence.db_path)
    service = EvaluationService(store, scenario)

    monkeypatch.setattr(EvaluationService, "_run_dry_case", lambda self, *args, **kwargs: None)

    report = service.run_matrix(config)
    persisted = service.get_matrix_report(config.evaluation.profile_name)

    assert report.completed_case_count == 1
    assert report.skipped_case_count == 1
    assert any(item.status is MatrixCaseStatus.SKIPPED_UNSUPPORTED for item in report.case_results)
    assert persisted.profile_name == config.evaluation.profile_name
