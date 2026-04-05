"""Scenario registry for Milestone 0 validation."""

from __future__ import annotations

from dcs_dungeon_master.core.exceptions import ScenarioNotFoundError
from dcs_dungeon_master.core.models import ScenarioDefinition


PHASE1_BASELINE_CAUCASUS_FRONTIER = ScenarioDefinition(
    id="phase1_baseline_caucasus_frontier",
    name="Phase 1 Baseline: Caucasus Frontier",
    theater="Caucasus",
    sector_ids=(
        "red_rear",
        "red_front",
        "central_west",
        "central_east",
        "blue_front",
        "blue_rear",
        "central_air_corridor",
    ),
    control_point_ids=(
        "red_rear_airbase",
        "blue_rear_airbase",
        "red_forward_support",
        "blue_forward_support",
        "central_objective_west",
        "central_objective_east",
    ),
    summary="Compact Phase 1 corridor for dual strategic commanders with contested center sectors.",
)


_SCENARIOS = {
    PHASE1_BASELINE_CAUCASUS_FRONTIER.id: PHASE1_BASELINE_CAUCASUS_FRONTIER,
}


def list_scenarios() -> tuple[ScenarioDefinition, ...]:
    return tuple(_SCENARIOS.values())


def get_scenario_definition(scenario_id: str) -> ScenarioDefinition:
    try:
        return _SCENARIOS[scenario_id]
    except KeyError as exc:
        raise ScenarioNotFoundError(f"Unknown scenario id: {scenario_id}") from exc
