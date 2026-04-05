"""Stable domain models for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dcs_dungeon_master.core.enums import (
    ActionType,
    Coalition,
    ConfidenceBand,
    DestinationType,
    EvaluationRating,
    ExecutionLifecycleState,
    ExecutionStatus,
    FairnessMachineStatus,
    FairnessMode,
    FairnessReviewStatus,
    GroupPosture,
    InferenceMode,
    KnowledgeLevel,
    MatrixCaseStatus,
    RejectionCode,
    EvaluationReviewPolicy,
    RunLifecycleStatus,
    SectorPriority,
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
    zones: tuple["ScenarioZone", ...] = ()
    deployment_restrictions: tuple["DeploymentRestrictionState", ...] = ()

    @property
    def sector_ids(self) -> tuple[str, ...]:
        return tuple(sector.id for sector in self.sectors)

    @property
    def control_point_ids(self) -> tuple[str, ...]:
        return tuple(control_point.id for control_point in self.control_points)

    @property
    def zone_ids(self) -> tuple[str, ...]:
        return tuple(zone.id for zone in self.zones)


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
class ScenarioZone:
    id: str
    name: str
    sector_id: str
    center_lat: float
    center_lng: float
    radius_nm: float
    tags: tuple[str, ...] = ()


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
class ObservationAttachment:
    attachment_id: str
    media_type: str
    role: str
    description: str
    uri: str | None = None
    source: str = "placeholder"
    coalition_filtered: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


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
    attachments: tuple[ObservationAttachment, ...] = ()


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
    fusion_update_id: int | None
    observation: CommanderObservation
    narrative: str


@dataclass(slots=True, frozen=True)
class ValidationMessage:
    level: str
    code: str
    text: str


@dataclass(slots=True, frozen=True)
class ActionRequest:
    id: str
    coalition: Coalition
    action_type: ActionType
    reason: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ActionBatch:
    run_id: str
    coalition: Coalition
    decision_cycle: int
    actions: tuple[ActionRequest, ...]


@dataclass(slots=True, frozen=True)
class NormalizedActionRequest:
    action_id: str
    action_type: ActionType
    reason: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ValidationContext:
    run_id: str
    coalition: Coalition
    decision_cycle: int
    scenario: ScenarioDefinition
    coalition_state: CoalitionState
    active_groups: tuple[ActiveGroupState, ...]
    reserve_groups: tuple[ReserveGroupState, ...]
    world_state: WorldStateSnapshot
    latest_observation: ObservationArtifact | None = None


@dataclass(slots=True, frozen=True)
class ValidationAuditEvidence:
    source_type: str
    identifier: str | None
    detail: str


@dataclass(slots=True, frozen=True)
class ActionValidationAuditEntry:
    action_id: str
    action_type: ActionType | None
    status: ValidationStatus
    rejection_code: RejectionCode | None
    message: str | None
    latest_observation_id: int | None
    evidence: tuple[ValidationAuditEvidence, ...] = ()


@dataclass(slots=True, frozen=True)
class ActionValidationAuditReport:
    batch_id: int | None
    run_id: str
    coalition: Coalition
    decision_cycle: int
    latest_observation_id: int | None
    entries: tuple[ActionValidationAuditEntry, ...]


@dataclass(slots=True, frozen=True)
class ActionValidationResult:
    action_id: str
    action_type: ActionType | None
    status: ValidationStatus
    rejection_code: RejectionCode | None = None
    message: str | None = None
    normalized_params: dict[str, Any] = field(default_factory=dict)
    messages: tuple[ValidationMessage, ...] = ()
    validated_stages: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ActionValidationBatch:
    id: int | None
    run_id: str
    coalition: Coalition
    decision_cycle: int
    submitted_at: datetime
    raw_payload: tuple[dict[str, Any], ...]
    results: tuple[ActionValidationResult, ...]
    non_hold_action_count: int
    soft_cap_exceeded: bool
    batch_messages: tuple[ValidationMessage, ...] = ()
    latest_observation_id: int | None = None


@dataclass(slots=True, frozen=True)
class ModelInvocationRequest:
    run_id: str
    coalition: Coalition
    decision_cycle: int
    backend_name: str
    observation_id: int | None
    system_prompt: str
    user_payload: dict[str, Any]
    request_payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ParsedActionProposal:
    payload: tuple[dict[str, Any], ...]
    raw_content: str
    wrapped_object: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class ModelInvocationResult:
    id: int | None
    run_id: str
    coalition: Coalition
    decision_cycle: int
    backend_name: str
    observation_id: int | None
    requested_at: datetime
    completed_at: datetime
    status: str
    attempt_count: int
    request_payload: dict[str, Any]
    raw_response: str | None
    parsed_actions: tuple[dict[str, Any], ...] = ()
    parse_status: str = "not_attempted"
    error_detail: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    validation_batch_id: int | None = None
    wrapped_response_object: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class ModelBackendCapability:
    backend_name: str
    endpoint: str
    hosting_mode: str
    openai_compatible: bool
    supports_structured_output: bool
    supports_multimodal: bool


@dataclass(slots=True, frozen=True)
class DecisionCycleResult:
    id: int | None
    run_id: str
    decision_cycle: int
    snapshot_time: datetime
    red_observation_id: int | None
    blue_observation_id: int | None
    red_invocation_id: int | None
    blue_invocation_id: int | None
    red_execution_batch_id: int | None = None
    blue_execution_batch_id: int | None = None
    classification: str = "both_failed"
    summary: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ExecutionCommand:
    command_id: str
    transport: str
    method: str
    path: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class StandingOrderRecord:
    id: str
    run_id: str
    coalition: Coalition
    action_type: ActionType
    entity_type: str
    entity_id: str | None
    fingerprint: str
    text: str
    active: bool = True
    last_updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ExecutionPlan:
    action_id: str
    coalition: Coalition
    action_type: ActionType
    commands: tuple[ExecutionCommand, ...] = ()
    standing_orders: tuple[StandingOrderRecord, ...] = ()
    resource_delta: dict[str, Any] = field(default_factory=dict)
    resulting_entities: tuple[str, ...] = ()
    state_updates: dict[str, Any] = field(default_factory=dict)
    summary: str | None = None


@dataclass(slots=True, frozen=True)
class ExecutionResult:
    action_id: str
    action_type: ActionType | None
    status: ExecutionStatus
    execution_summary: str
    command_count: int = 0
    resource_delta: dict[str, Any] = field(default_factory=dict)
    standing_order_delta: tuple[str, ...] = ()
    resulting_entities: tuple[str, ...] = ()
    error_detail: str | None = None
    applied_commands: tuple[ExecutionCommand, ...] = ()


@dataclass(slots=True, frozen=True)
class ExecutionBatchResult:
    id: int | None
    run_id: str
    coalition: Coalition
    decision_cycle: int
    observation_id: int | None
    model_invocation_id: int | None
    validation_batch_id: int | None
    started_at: datetime
    completed_at: datetime | None
    lifecycle_state: ExecutionLifecycleState
    status: ExecutionStatus
    results: tuple[ExecutionResult, ...]
    summary: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ExecutionCapabilityMap:
    primary_transport: str
    secondary_transport: str | None
    supported_action_types: tuple[ActionType, ...]
    posture_command_supported: bool = True
    reserve_deploy_supported: bool = True
    movement_supported: bool = True


@dataclass(slots=True, frozen=True)
class EventRecord:
    id: int | None
    run_id: str
    event_type: str
    entity_type: str
    entity_id: str | None
    occurred_at: datetime
    payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class CurrentExecutionNote:
    run_id: str
    coalition: Coalition
    entity_type: str
    entity_id: str
    decision_cycle: int
    action_id: str
    note: str
    lifecycle_state: ExecutionLifecycleState
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class StandingOrderHistoryEntry:
    id: int | None
    run_id: str
    order_id: str
    coalition: Coalition
    action_type: ActionType
    entity_type: str
    entity_id: str | None
    transition: str
    occurred_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class FusionSnapshotRecord:
    id: int | None
    run_id: str
    coalition: Coalition
    fusion_update_id: int
    generated_at: datetime
    mission_time: str | None
    known_friendly_group_ids: tuple[str, ...]
    sector_activity: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RunControlState:
    run_id: str
    scenario_id: str
    scenario_version: str
    scenario_name: str
    theater: str
    mode: str
    status: RunLifecycleStatus
    created_at: datetime
    config_digest: str | None = None
    config_snapshot: dict[str, Any] | None = None
    red_backend_name: str | None = None
    blue_backend_name: str | None = None
    started_at: datetime | None = None
    paused_at: datetime | None = None
    resumed_at: datetime | None = None
    stopped_at: datetime | None = None
    failed_at: datetime | None = None
    terminal_reason: str | None = None
    evaluation_metadata: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class OperatorCommandResult:
    run_id: str
    previous_status: RunLifecycleStatus
    current_status: RunLifecycleStatus
    detail: str
    changed_at: datetime


@dataclass(slots=True, frozen=True)
class RunTimelineEntry:
    run_id: str
    decision_cycle: int
    snapshot_time: datetime
    classification: str
    red_observation_id: int | None
    blue_observation_id: int | None
    red_invocation_status: str | None
    blue_invocation_status: str | None
    red_execution_status: str | None
    blue_execution_status: str | None
    red_execution_lifecycle_state: str | None = None
    blue_execution_lifecycle_state: str | None = None
    failures: tuple[str, ...] = ()
    summary: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class CoalitionInspectionView:
    run_id: str
    coalition: Coalition
    latest_observation: dict[str, Any] | None
    latest_model_invocation: dict[str, Any] | None
    latest_action_validation: dict[str, Any] | None
    latest_execution: dict[str, Any] | None
    current_execution_notes: tuple[dict[str, Any], ...] = ()


@dataclass(slots=True, frozen=True)
class RunFailureEntry:
    subsystem: str
    coalition: Coalition | None
    decision_cycle: int | None
    detail: str


@dataclass(slots=True, frozen=True)
class RunWarningEntry:
    subsystem: str
    coalition: Coalition | None
    decision_cycle: int | None
    detail: str


@dataclass(slots=True, frozen=True)
class RunSummary:
    run: RunControlState
    latest_decision_cycle: int
    observation_count: int
    model_invocation_count: int
    validation_count: int
    execution_count: int
    decision_cycle_count: int
    observation_count_by_coalition: dict[str, int]
    model_invocation_count_by_coalition: dict[str, int]
    validation_count_by_coalition: dict[str, int]
    execution_count_by_coalition: dict[str, int]
    cycle_classification_counts: dict[str, int]
    model_status_counts: dict[str, int]
    execution_status_counts: dict[str, int]
    failure_counts: dict[str, int]
    warning_counts: dict[str, int]
    last_error_summary: tuple[str, ...]
    failures: tuple[RunFailureEntry, ...] = ()
    warnings: tuple[RunWarningEntry, ...] = ()
    coalition_views: tuple[CoalitionInspectionView, ...] = ()


@dataclass(slots=True, frozen=True)
class LoopRunResult:
    run_id: str
    start_decision_cycle: int
    requested_cycles: int
    completed_cycles: int
    stopped_early: bool
    terminal_run_status: RunLifecycleStatus
    results: tuple[DecisionCycleResult, ...] = ()


@dataclass(slots=True, frozen=True)
class ReplayBundleManifest:
    bundle_version: str
    run_id: str
    scenario_id: str
    exported_at: datetime
    file_inventory: tuple[str, ...]
    comparison_metadata: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ReplayExportResult:
    run_id: str
    output_dir: str
    manifest_path: str
    file_count: int


@dataclass(slots=True, frozen=True)
class RunComparisonResult:
    left_run_id: str
    right_run_id: str
    scenario_ids: tuple[str, str]
    theaters: tuple[str, str]
    modes: tuple[str, str]
    statuses: tuple[str, str]
    backend_assignments: dict[str, tuple[str | None, str | None]]
    decision_cycle_counts: tuple[int, int]
    latest_decision_cycles: tuple[int, int]
    cycle_classification_counts: dict[str, tuple[int, int]]
    model_status_counts: dict[str, tuple[int, int]]
    execution_status_counts: dict[str, tuple[int, int]]
    failure_counts: dict[str, tuple[int, int]]


@dataclass(slots=True, frozen=True)
class EvaluationProfileConfigView:
    profile_name: str
    decision_cadence_sec: int
    prompt_version: str
    resource_settings_version: str
    review_policy: EvaluationReviewPolicy
    fairness_mode: FairnessMode
    default_run_cycles: int
    scenario_seed: str | None = None
    notes: str | None = None


@dataclass(slots=True, frozen=True)
class EvaluationMatrixCaseConfigView:
    id: str
    description: str
    mode: str
    red_backend: str
    blue_backend: str
    decision_cadence_sec: int
    run_cycles: int
    repeat_count: int
    system_prompt_variant_override: str | None = None
    visual_attachment: bool = False


@dataclass(slots=True, frozen=True)
class EvaluationCycleMetrics:
    run_id: str
    decision_cycle: int
    coalition_action_counts: dict[str, dict[str, int]]
    duplicate_action_count: int
    conflict_action_count: int
    low_confidence_enemy_decision_count: int
    average_enemy_contact_staleness_sec: float
    suspicious_finding_count: int
    control_point_owner_changes: int


@dataclass(slots=True, frozen=True)
class FairnessFinding:
    id: int | None
    run_id: str
    coalition: Coalition | None
    decision_cycle: int | None
    finding_type: str
    machine_status: FairnessMachineStatus
    review_status: FairnessReviewStatus
    detail: str
    score_penalty: int
    finding_data: dict[str, Any] = field(default_factory=dict)
    reviewer_note: str | None = None
    reviewed_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class TuningNote:
    note_type: str
    severity: str
    detail: str


@dataclass(slots=True, frozen=True)
class EvaluationRunSummary:
    run_id: str
    generated_at: datetime
    profile_name: str | None
    decision_cadence_sec: int | None
    prompt_version: str | None
    resource_settings_version: str | None
    fairness_mode: FairnessMode
    review_policy: EvaluationReviewPolicy
    loop_health: dict[str, Any]
    action_quality: dict[str, Any]
    resource_discipline: dict[str, Any]
    strategic_behavior: dict[str, Any]
    observation_integrity: dict[str, Any]
    fairness_score: int
    fairness_rating: EvaluationRating
    suspicious_finding_count: int
    findings_pending_review: int
    top_rejection_codes: dict[str, int]
    tuning_notes: tuple[TuningNote, ...] = ()


@dataclass(slots=True, frozen=True)
class EvaluationComparisonReport:
    left_run_id: str
    right_run_id: str
    generated_at: datetime
    fairness_score_delta: int
    loop_health_delta: dict[str, float]
    strategic_behavior_delta: dict[str, float]
    rejection_code_shift: dict[str, tuple[int, int]]
    tuning_note_changes: dict[str, tuple[bool, bool]]


@dataclass(slots=True, frozen=True)
class MatrixCaseResult:
    id: int | None
    profile_name: str
    case_id: str
    description: str
    repeat_index: int
    status: MatrixCaseStatus
    mode: str
    red_backend: str
    blue_backend: str
    decision_cadence_sec: int
    run_cycles: int
    run_id: str | None = None
    evaluation_run_id: str | None = None
    detail: str | None = None


@dataclass(slots=True, frozen=True)
class MatrixRunReport:
    profile_name: str
    generated_at: datetime
    case_results: tuple[MatrixCaseResult, ...]
    completed_case_count: int
    failed_case_count: int
    skipped_case_count: int
