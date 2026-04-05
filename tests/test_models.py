from datetime import datetime

from dcs_dungeon_master.core.enums import ActionType, Coalition, GroupPosture, ValidationStatus
from dcs_dungeon_master.core.models import ActionRequest, ExecutionResult, ObservationSnapshot, ScenarioDefinition


def test_domain_models_construct_cleanly() -> None:
    scenario = ScenarioDefinition(
        id="phase1_baseline_caucasus_frontier",
        name="Phase 1 Baseline: Caucasus Frontier",
        theater="Caucasus",
        sector_ids=("red_rear", "central_west"),
        control_point_ids=("red_rear_airbase",),
        summary="Test summary",
    )
    observation = ObservationSnapshot(
        id="obs-1",
        coalition=Coalition.RED,
        schema_version="0.1",
        scenario_id=scenario.id,
        decision_cycle=1,
        generated_at=datetime(2026, 4, 5, 12, 0, 0),
        payload={"meta": {"schema_version": "0.1"}},
    )
    action = ActionRequest(
        id="act-1",
        coalition=Coalition.RED,
        action_type=ActionType.HOLD_ACTION,
        reason="Maintain current posture.",
        params={"scope": "global"},
    )
    result = ExecutionResult(
        action_id=action.id,
        status=ValidationStatus.ACCEPTED,
        execution_summary="No change.",
    )

    assert scenario.theater == "Caucasus"
    assert observation.coalition is Coalition.RED
    assert action.action_type is ActionType.HOLD_ACTION
    assert result.status is ValidationStatus.ACCEPTED
    assert GroupPosture.DEFENSIVE.value == "defensive"
