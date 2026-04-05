from datetime import datetime

from dcs_dungeon_master.core.enums import ActionType, Coalition, ExecutionStatus, GroupPosture
from dcs_dungeon_master.core.models import (
    ActionRequest,
    CoalitionState,
    ControlPointState,
    DeploymentRestrictionState,
    ExecutionResult,
    ObservationSnapshot,
    ScenarioDefinition,
    SectorState,
    StandingOrderState,
)


def test_domain_models_construct_cleanly() -> None:
    sector = SectorState(
        id="red_rear",
        name="Red Rear",
        role="rear",
        neighbor_ids=("red_front",),
        tags=("rear", "staging"),
    )
    control_point = ControlPointState(
        id="red_rear_airbase",
        name="Bandar Abbas Airbase",
        sector_id=sector.id,
        kind="rear_airbase",
        owner=Coalition.RED,
        strategic_value="high",
    )
    standing_order = StandingOrderState(
        id="red_defend_front",
        coalition=Coalition.RED,
        text="Hold the forward sector.",
    )
    coalition_state = CoalitionState(
        coalition=Coalition.RED,
        objectives=("hold_red_front",),
        budget_remaining=24,
        reserve_ids=("red_reserve_sam",),
        standing_orders=(standing_order,),
    )
    restriction = DeploymentRestrictionState(
        id="frontline_adjacency_rule",
        coalition=None,
        restriction_type="adjacency_rule",
        description="Respect sector adjacency.",
        sector_ids=("red_front", "central_west"),
        adjacency_limited=True,
    )
    scenario = ScenarioDefinition(
        id="phase1_baseline_persian_gulf",
        version="1",
        name="Phase 1 Baseline: Persian Gulf Frontier",
        theater="Persian Gulf",
        summary="Test summary",
        sectors=(sector,),
        control_points=(control_point,),
        coalitions=(coalition_state,),
        active_groups=(),
        reserve_groups=(),
        deployment_restrictions=(restriction,),
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
        action_type=action.action_type,
        status=ExecutionStatus.NO_CHANGE,
        execution_summary="No change.",
    )

    assert scenario.theater == "Persian Gulf"
    assert scenario.sector_ids == ("red_rear",)
    assert observation.coalition is Coalition.RED
    assert action.action_type is ActionType.HOLD_ACTION
    assert result.status is ExecutionStatus.NO_CHANGE
    assert GroupPosture.DEFENSIVE.value == "defensive"
