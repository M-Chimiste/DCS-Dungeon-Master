"""Stable domain models for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dcs_dungeon_master.core.enums import (
    ActionType,
    Coalition,
    ConfidenceBand,
    GroupPosture,
    InferenceMode,
    KnowledgeLevel,
    RejectionCode,
    ValidationStatus,
)


@dataclass(slots=True, frozen=True)
class ScenarioDefinition:
    id: str
    version: str
    name: str
    theater: str
    summary: str
    sectors: tuple["SectorState", ...]
    control_points: tuple["ControlPointState", ...]
    coalitions: tuple["CoalitionState", ...]
    active_groups: tuple["ActiveGroupState", ...]
    reserve_groups: tuple["ReserveGroupState", ...]
    deployment_restrictions: tuple["DeploymentRestrictionState", ...] = ()

    @property
    def sector_ids(self) -> tuple[str, ...]:
        return tuple(sector.id for sector in self.sectors)

    @property
    def control_point_ids(self) -> tuple[str, ...]:
        return tuple(control_point.id for control_point in self.control_points)


@dataclass(slots=True, frozen=True)
class SectorState:
    id: str
    name: str
    role: str
    neighbor_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    center_lat: float | None = None
    center_lng: float | None = None
    radius_nm: float = 35.0


@dataclass(slots=True, frozen=True)
class ControlPointState:
    id: str
    name: str
    sector_id: str
    kind: str
    owner: Coalition | None = None
    strategic_value: str = "medium"
    lat: float | None = None
    lng: float | None = None


@dataclass(slots=True, frozen=True)
class StandingOrderState:
    id: str
    coalition: Coalition
    text: str
    active: bool = True


@dataclass(slots=True, frozen=True)
class CoalitionState:
    coalition: Coalition
    objectives: tuple[str, ...] = ()
    budget_remaining: int = 0
    reserve_ids: tuple[str, ...] = ()
    standing_orders: tuple[StandingOrderState, ...] = ()
    attrition_pool: int = 0
    replacement_pool: int = 0


@dataclass(slots=True, frozen=True)
class ReserveGroupState:
    id: str
    coalition: Coalition
    group_type: str
    available: bool
    cost: int
    allowed_sector_ids: tuple[str, ...] = ()
    status: str = "available"
    emergency: bool = False
    attrition_count: int = 0
    replacement_pool: int = 0


@dataclass(slots=True, frozen=True)
class ActiveGroupState:
    id: str
    coalition: Coalition
    group_type: str
    posture: GroupPosture
    sector_id: str | None
    mobile: bool = True
    status: str = "ready"
    control_point_id: str | None = None
    attrition_count: int = 0
    replacement_pool: int = 0


@dataclass(slots=True, frozen=True)
class DeploymentRestrictionState:
    id: str
    coalition: Coalition | None
    restriction_type: str
    description: str
    sector_ids: tuple[str, ...] = ()
    control_point_ids: tuple[str, ...] = ()
    adjacency_limited: bool = False


@dataclass(slots=True, frozen=True)
class ContactTrack:
    id: str
    coalition: Coalition
    knowledge_level: KnowledgeLevel
    confidence: ConfidenceBand
    last_known_sector_id: str | None = None
    estimated_sector_id: str | None = None
    seconds_since_last_confirmation: int = 0
    inferred: bool = False
    stale: bool = False
    archived: bool = False
    classification: str | None = None
    first_seen_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    evidence_summary: "TrackEvidenceSummary | None" = None


@dataclass(slots=True, frozen=True)
class WorldGroupState:
    id: str
    source_id: str
    coalition: Coalition | None
    group_type: str
    category: str | None
    name: str | None
    sector_id: str | None
    control_point_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    alt: float | None = None
    heading: float | None = None
    speed: float | None = None
    health: float | None = None
    source: str = "scenario_seed"
    last_updated_at: datetime | None = None
    high_value: bool = False
    mobile: bool = True


@dataclass(slots=True, frozen=True)
class WorldControlPointState:
    control_point_id: str
    owner: Coalition | None
    sector_id: str
    last_updated_at: datetime | None = None
    source: str = "scenario_seed"


@dataclass(slots=True, frozen=True)
class EvidenceRecord:
    id: int | None
    run_id: str
    source: str
    evidence_type: str
    entity_id: str | None
    coalition: Coalition | None
    sector_id: str | None
    occurred_at: datetime
    summary: str
    payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class TrackEvidenceSummary:
    evidence_count: int
    sources: tuple[str, ...]
    last_source: str | None
    last_confirmed_at: datetime | None


@dataclass(slots=True, frozen=True)
class CoalitionContactTrack:
    track_id: str
    coalition: Coalition
    knowledge_level: KnowledgeLevel
    confidence: ConfidenceBand
    classification: str | None
    inferred: bool
    stale: bool
    archived: bool
    first_seen_at: datetime | None
    last_confirmed_at: datetime | None
    last_known_sector_id: str | None
    estimated_sector_id: str | None = None
    source_summary: TrackEvidenceSummary | None = None
    seconds_since_last_confirmation: int = 0


@dataclass(slots=True, frozen=True)
class CoalitionKnowledgeState:
    coalition: Coalition
    generated_at: datetime
    mission_time: str | None
    contact_tracks: tuple[CoalitionContactTrack, ...]
    archived_tracks: tuple[CoalitionContactTrack, ...] = ()
    known_friendly_group_ids: tuple[str, ...] = ()
    sector_activity: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class FogOfWarPolicy:
    freshness_window_sec: int
    decay_window_sec: int
    archive_window_sec: int
    adjacency_drift_enabled: bool = True
    inference_mode: InferenceMode = InferenceMode.CONSERVATIVE
    debug_truth_comparison: bool = False


@dataclass(slots=True, frozen=True)
class WorldStateSnapshot:
    run_id: str
    scenario_id: str
    theater: str
    mission_time: str | None
    last_ingest_at: datetime | None
    groups: tuple[WorldGroupState, ...]
    control_points: tuple[WorldControlPointState, ...]
    evidence_records: tuple[EvidenceRecord, ...] = ()


@dataclass(slots=True, frozen=True)
class FusionUpdateResult:
    generated_at: datetime
    policy: FogOfWarPolicy
    knowledge_states: tuple[CoalitionKnowledgeState, ...]
    active_track_count: int
    archived_track_count: int
    change_summary: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ObservationMeta:
    schema_version: str
    coalition: Coalition
    snapshot_time: datetime
    decision_cycle: int
    seconds_since_last_cycle: int
    picture_quality: str


@dataclass(slots=True, frozen=True)
class CommanderStateView:
    coalition: Coalition
    posture: str | None
    objectives: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    operator_instructions: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class KnownControlPointView:
    id: str
    name: str
    kind: str
    sector_id: str
    status: str


@dataclass(slots=True, frozen=True)
class KnownSectorView:
    id: str
    name: str
    role: str
    status: str


@dataclass(slots=True, frozen=True)
class ScenarioStateView:
    theater: str
    mission_clock: str | None
    weather_summary: str | None
    known_control_points: tuple[KnownControlPointView, ...]
    known_sectors: tuple[KnownSectorView, ...]
    front_status: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ReserveAvailabilityView:
    id: str
    group_type: str
    count: int
    allowed_sectors: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ResourceStateView:
    deployment_budget_remaining: int
    available_reserves: tuple[ReserveAvailabilityView, ...]
    recently_lost_assets: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    key_shortages: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class SectorSummaryEntry:
    sector_id: str
    sector_label: str
    control_status: str
    friendly_strength: str
    enemy_threat: str
    contact_confidence: str
    activity_level: str
    strategic_importance: str
    recent_notes: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class FriendlyForceEntry:
    force_id: str
    force_type: str
    assigned_sector: str | None
    readiness: str
    task: str
    mobility: str
    health_band: str
    high_value: bool = False


@dataclass(slots=True, frozen=True)
class EnemyContactEntry:
    contact_id: str
    knowledge_level: KnowledgeLevel
    classification: str | None
    last_known_sector: str | None
    estimated_sector: str | None
    time_since_last_confirmation_sec: int
    confidence: ConfidenceBand
    detection_sources: tuple[str, ...]
    threat_estimate: str
    stale: bool
    inferred: bool


@dataclass(slots=True, frozen=True)
class CommanderObservation:
    meta: ObservationMeta
    commander_state: CommanderStateView
    scenario_state: ScenarioStateView
    resource_state: ResourceStateView
    sector_summary: tuple[SectorSummaryEntry, ...]
    friendly_forces: tuple[FriendlyForceEntry, ...]
    enemy_contacts: tuple[EnemyContactEntry, ...]
    recent_changes: tuple[str, ...]
    standing_orders: tuple[str, ...]
    requests_for_decision: tuple[str, ...]


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
class ObservationArtifact:
    id: int | None
    run_id: str
    coalition: Coalition
    decision_cycle: int
    generated_at: datetime
    previous_observation_id: int | None
    observation: CommanderObservation
    narrative: str


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


@dataclass(slots=True, frozen=True)
class EventRecord:
    id: int | None
    run_id: str
    event_type: str
    entity_type: str
    entity_id: str | None
    occurred_at: datetime
    payload: dict[str, Any]
