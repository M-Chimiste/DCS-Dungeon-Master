"""Stable domain models for Milestone 0."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dcs_dungeon_master.core.enums import (
    ActionType,
    Coalition,
    GroupPosture,
    RejectionCode,
    ValidationStatus,
)


@dataclass(slots=True, frozen=True)
class ScenarioDefinition:
    id: str
    name: str
    theater: str
    sector_ids: tuple[str, ...]
    control_point_ids: tuple[str, ...]
    summary: str


@dataclass(slots=True, frozen=True)
class SectorState:
    id: str
    name: str
    tags: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ControlPointState:
    id: str
    name: str
    sector_id: str
    kind: str
    owner: Coalition | None = None


@dataclass(slots=True, frozen=True)
class CoalitionState:
    coalition: Coalition
    objectives: tuple[str, ...] = ()
    budget_remaining: int = 0
    reserve_ids: tuple[str, ...] = ()
    standing_orders: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ReserveGroupState:
    id: str
    coalition: Coalition
    group_type: str
    available: bool
    cost: int
    allowed_sector_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ActiveGroupState:
    id: str
    coalition: Coalition
    group_type: str
    posture: GroupPosture
    sector_id: str | None
    mobile: bool = True
    status: str = "ready"


@dataclass(slots=True, frozen=True)
class ContactTrack:
    id: str
    knowledge_level: str
    confidence: str
    last_known_sector_id: str | None = None
    seconds_since_last_confirmation: int = 0
    inferred: bool = False


@dataclass(slots=True, frozen=True)
class ObservationSnapshot:
    id: str
    coalition: Coalition
    schema_version: str
    scenario_id: str
    decision_cycle: int
    generated_at: datetime
    payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ActionRequest:
    id: str
    coalition: Coalition
    action_type: ActionType
    reason: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ValidatedAction:
    request_id: str
    status: ValidationStatus
    rejection_code: RejectionCode | None = None
    message: str | None = None
    normalized_params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ExecutionResult:
    action_id: str
    status: ValidationStatus
    execution_summary: str
    resource_delta: dict[str, Any] = field(default_factory=dict)
    standing_order_delta: tuple[str, ...] = ()
    resulting_entities: tuple[str, ...] = ()
