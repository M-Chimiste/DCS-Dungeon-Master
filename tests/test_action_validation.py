from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.enums import Coalition, RejectionCode, ValidationStatus
from dcs_dungeon_master.core.models import (
    CommanderObservation,
    CommanderStateView,
    ObservationArtifact,
    ObservationMeta,
    ResourceStateView,
    ScenarioStateView,
)
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition


def _build_validator(tmp_path: Path) -> tuple[SQLiteStateStore, str, ActionValidator]:
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    run_id = store.create_run_from_scenario(scenario)
    return store, run_id, ActionValidator(store, scenario)


def test_validate_all_phase1_actions_and_persist_batch(tmp_path: Path) -> None:
    store, run_id, validator = _build_validator(tmp_path)

    batch = validator.validate_payload(
        run_id,
        Coalition.RED,
        1,
        [
            {
                "action_id": "act_001",
                "action_type": "set_sector_priority",
                "reason": "Western approach is heating up.",
                "sector_id": "central_west",
                "priority": "high",
            },
            {
                "action_id": "act_002",
                "action_type": "deploy_reserve_group",
                "reason": "Frontline needs another SAM umbrella.",
                "reserve_group_id": "red_reserve_sam",
                "target_sector_id": "red_front",
                "role": "defensive",
            },
            {
                "action_id": "act_003",
                "action_type": "reposition_group",
                "reason": "Tighten SAM support coverage.",
                "group_id": "red_front_ad",
                "destination_type": "zone",
                "destination_id": "red_front_support_zone",
            },
            {
                "action_id": "act_004",
                "action_type": "set_group_posture",
                "reason": "Keep fires on support tasking.",
                "group_id": "red_fires_group",
                "posture": "support",
            },
            {
                "action_id": "act_005",
                "action_type": "reinforce_control_point",
                "reason": "Forward support needs additional coverage.",
                "control_point_id": "red_forward_support",
                "group_ids": ["red_ground_group"],
                "reinforcement_role": "defend",
            },
            {
                "action_id": "act_006",
                "action_type": "withdraw_group",
                "reason": "Preserve the screening group if the front collapses.",
                "group_id": "red_screen_group",
                "fallback_destination_type": "zone",
                "fallback_destination_id": "red_rear_fallback_zone",
            },
            {
                "action_id": "act_007",
                "action_type": "hold_action",
                "reason": "No further changes this cycle.",
                "scope": "global",
            },
        ],
    )

    assert batch.id is not None
    assert batch.soft_cap_exceeded is True
    assert batch.non_hold_action_count == 6
    assert all(result.status != ValidationStatus.REJECTED for result in batch.results)

    summary = validator.summarize_latest(run_id, Coalition.RED)

    assert summary["accepted_count"] + summary["partially_accepted_count"] == 7
    assert summary["soft_cap_exceeded"] is True
    assert summary["batch_messages"][0]["code"] == "soft_batch_cap_exceeded"
    assert store.get_run_summary(run_id)["action_validation_batch_count"] == 1


def test_schema_and_zone_validation_rejections(tmp_path: Path) -> None:
    _, run_id, validator = _build_validator(tmp_path)

    batch = validator.validate_payload(
        run_id,
        Coalition.RED,
        2,
        [
            {
                "action_id": "missing_field",
                "action_type": "set_sector_priority",
                "reason": "Missing sector field on purpose.",
                "priority": "high",
            },
            {
                "action_id": "unknown_type",
                "action_type": "launch_everything",
                "reason": "Not a real action.",
            },
            {
                "action_id": "bad_zone",
                "action_type": "reposition_group",
                "reason": "Bad zone id on purpose.",
                "group_id": "red_front_ad",
                "destination_type": "zone",
                "destination_id": "unknown_zone",
            },
        ],
    )

    assert batch.results[0].rejection_code == RejectionCode.MISSING_REQUIRED_FIELD
    assert batch.results[1].rejection_code == RejectionCode.UNKNOWN_ACTION_TYPE
    assert batch.results[2].rejection_code == RejectionCode.DESTINATION_INVALID


def test_reinforce_control_point_normalizes_duplicates_and_enforces_group_count(tmp_path: Path) -> None:
    _, run_id, validator = _build_validator(tmp_path)

    partial = validator.validate_payload(
        run_id,
        Coalition.RED,
        3,
        [
            {
                "action_id": "dup_groups",
                "action_type": "reinforce_control_point",
                "reason": "Deduplicate repeated group ids.",
                "control_point_id": "red_forward_support",
                "group_ids": ["red_ground_group", "red_ground_group"],
                "reinforcement_role": "defend",
            }
        ],
    )
    zero_groups = validator.validate_payload(
        run_id,
        Coalition.RED,
        4,
        [
            {
                "action_id": "zero_groups",
                "action_type": "reinforce_control_point",
                "reason": "No groups.",
                "control_point_id": "red_forward_support",
                "group_ids": [],
                "reinforcement_role": "defend",
            }
        ],
    )
    four_groups = validator.validate_payload(
        run_id,
        Coalition.RED,
        5,
        [
            {
                "action_id": "four_groups",
                "action_type": "reinforce_control_point",
                "reason": "Too many groups.",
                "control_point_id": "red_forward_support",
                "group_ids": ["red_front_ad", "red_ground_group", "red_screen_group", "red_fires_group"],
                "reinforcement_role": "defend",
            }
        ],
    )

    assert partial.results[0].status == ValidationStatus.PARTIALLY_ACCEPTED
    assert partial.results[0].normalized_params["group_ids"] == ["red_ground_group"]
    assert partial.results[0].messages[0].code == "duplicate_group_ids_normalized"
    assert zero_groups.results[0].rejection_code == RejectionCode.INVALID_FIELD_VALUE
    assert four_groups.results[0].rejection_code == RejectionCode.INVALID_FIELD_VALUE


def test_reject_enemy_owned_and_budget_violations(tmp_path: Path) -> None:
    store, run_id, validator = _build_validator(tmp_path)
    with store._connect() as connection:
        connection.execute(
            "UPDATE coalition_state SET budget_remaining = 3 WHERE run_id = ? AND coalition = ?",
            (run_id, Coalition.RED.value),
        )

    batch = validator.validate_payload(
        run_id,
        Coalition.RED,
        6,
        [
            {
                "action_id": "enemy_group",
                "action_type": "reposition_group",
                "reason": "Should fail because it is blue.",
                "group_id": "blue_front_ad",
                "destination_type": "zone",
                "destination_id": "blue_front_support_zone",
            },
            {
                "action_id": "enemy_point",
                "action_type": "reinforce_control_point",
                "reason": "Should fail because point is blue.",
                "control_point_id": "blue_forward_support",
                "group_ids": ["red_ground_group"],
                "reinforcement_role": "defend",
            },
            {
                "action_id": "over_budget",
                "action_type": "deploy_reserve_group",
                "reason": "Budget intentionally too low.",
                "reserve_group_id": "red_reserve_sam",
                "target_sector_id": "red_front",
                "role": "defensive",
            },
        ],
    )

    assert batch.results[0].rejection_code == RejectionCode.ENTITY_NOT_OWNED
    assert batch.results[1].rejection_code == RejectionCode.ENTITY_NOT_OWNED
    assert batch.results[2].rejection_code == RejectionCode.INSUFFICIENT_BUDGET


def test_batch_conflicts_reject_later_conflicting_actions(tmp_path: Path) -> None:
    _, run_id, validator = _build_validator(tmp_path)

    batch = validator.validate_payload(
        run_id,
        Coalition.RED,
        7,
        [
            {
                "action_id": "reserve_first",
                "action_type": "deploy_reserve_group",
                "reason": "First reserve commit.",
                "reserve_group_id": "red_reserve_mech",
                "target_sector_id": "red_front",
                "role": "reserve",
            },
            {
                "action_id": "reserve_second",
                "action_type": "deploy_reserve_group",
                "reason": "Duplicate reserve commit.",
                "reserve_group_id": "red_reserve_mech",
                "target_sector_id": "central_west",
                "role": "reserve",
            },
            {
                "action_id": "move_group",
                "action_type": "reposition_group",
                "reason": "Move first.",
                "group_id": "red_screen_group",
                "destination_type": "sector",
                "destination_id": "central_west",
            },
            {
                "action_id": "withdraw_same_group",
                "action_type": "withdraw_group",
                "reason": "Conflicts with move.",
                "group_id": "red_screen_group",
                "fallback_destination_type": "zone",
                "fallback_destination_id": "red_rear_fallback_zone",
            },
            {
                "action_id": "priority_high",
                "action_type": "set_sector_priority",
                "reason": "Raise priority.",
                "sector_id": "central_west",
                "priority": "high",
            },
            {
                "action_id": "priority_low",
                "action_type": "set_sector_priority",
                "reason": "Conflicting priority.",
                "sector_id": "central_west",
                "priority": "low",
            },
        ],
    )

    result_by_id = {result.action_id: result for result in batch.results}
    assert result_by_id["reserve_first"].status != ValidationStatus.REJECTED
    assert result_by_id["reserve_second"].rejection_code == RejectionCode.ACTION_CONFLICT
    assert result_by_id["move_group"].status != ValidationStatus.REJECTED
    assert result_by_id["withdraw_same_group"].rejection_code == RejectionCode.ACTION_CONFLICT
    assert result_by_id["priority_high"].status != ValidationStatus.REJECTED
    assert result_by_id["priority_low"].rejection_code == RejectionCode.ACTION_CONFLICT


def test_validation_audit_report_uses_coalition_safe_sources(tmp_path: Path) -> None:
    store, run_id, validator = _build_validator(tmp_path)
    store.save_observation_artifact(
        ObservationArtifact(
            id=None,
            run_id=run_id,
            coalition=Coalition.RED,
            decision_cycle=1,
            generated_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            previous_observation_id=None,
            fusion_update_id=None,
            observation=CommanderObservation(
                meta=ObservationMeta(
                    schema_version="1.0",
                    coalition=Coalition.RED,
                    snapshot_time=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
                    decision_cycle=1,
                    seconds_since_last_cycle=30,
                    picture_quality="filtered_and_incomplete",
                ),
                commander_state=CommanderStateView(coalition=Coalition.RED, posture=None, objectives=()),
                scenario_state=ScenarioStateView(
                    theater="Persian Gulf",
                    mission_clock="12:00:00Z",
                    weather_summary=None,
                    known_control_points=(),
                    known_sectors=(),
                ),
                resource_state=ResourceStateView(deployment_budget_remaining=20, available_reserves=()),
                sector_summary=(),
                friendly_forces=(),
                enemy_contacts=(),
                recent_changes=(),
                standing_orders=(),
                requests_for_decision=(),
                attachments=(),
            ),
            narrative="RED commander picture.",
        )
    )

    validator.validate_payload(
        run_id,
        Coalition.RED,
        1,
        [
            {
                "action_id": "audit_1",
                "action_type": "deploy_reserve_group",
                "reason": "Commit reserve.",
                "reserve_group_id": "red_reserve_sam",
                "target_sector_id": "red_front",
                "role": "defensive",
            }
        ],
    )

    audit = validator.build_audit_report(run_id, Coalition.RED)

    assert audit.latest_observation_id is not None
    assert audit.entries[0].action_id == "audit_1"
    assert any(item.source_type == "coalition_owned" for item in audit.entries[0].evidence)
    assert any(item.source_type == "scenario_known" for item in audit.entries[0].evidence)
    assert any(item.source_type == "anti_cheat_boundary" for item in audit.entries[0].evidence)
