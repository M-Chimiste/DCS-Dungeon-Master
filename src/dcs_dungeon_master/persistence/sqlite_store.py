"""SQLite materialized state and event log storage."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from dcs_dungeon_master.core.enums import (
    ActionType,
    Coalition,
    ConfidenceBand,
    EvaluationRating,
    ExecutionLifecycleState,
    ExecutionStatus,
    FairnessMachineStatus,
    FairnessMode,
    FairnessReviewStatus,
    GroupPosture,
    KnowledgeLevel,
    MatrixCaseStatus,
    RejectionCode,
    RunLifecycleStatus,
    ValidationStatus,
    EvaluationReviewPolicy,
)
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.core.models import (
    ActiveGroupState,
    ActionValidationBatch,
    ActionValidationResult,
    CoalitionContactTrack,
    CoalitionKnowledgeState,
    CoalitionState,
    CommanderStateView,
    CommanderObservation,
    ControlPointState,
    CoalitionInspectionView,
    CurrentExecutionNote,
    DecisionCycleResult,
    DeploymentRestrictionState,
    EnemyContactEntry,
    EvidenceRecord,
    EvaluationComparisonReport,
    EvaluationCycleMetrics,
    EvaluationRunSummary,
    EventRecord,
    ExecutionBatchResult,
    ExecutionCommand,
    ExecutionResult,
    FogOfWarPolicy,
    FairnessFinding,
    FriendlyForceEntry,
    FusionUpdateResult,
    FusionSnapshotRecord,
    IngestCycleResult,
    KnownControlPointView,
    KnownSectorView,
    ModelInvocationAttempt,
    ModelInvocationResult,
    NormalizedIntegrationBatch,
    ObservationMeta,
    ObservationArtifact,
    ObservationAttachment,
    ObservationAttachmentArtifact,
    ResourceStateView,
    ReplayBundleManifest,
    ReplayExportResult,
    ReserveAvailabilityView,
    RunComparisonResult,
    RunControlState,
    RunFailureEntry,
    RunSummary,
    RunTimelineEntry,
    ScenarioDefinition,
    ScenarioDraftView,
    ReserveGroupState,
    ScenarioZone,
    ScenarioStateView,
    SectorSummaryEntry,
    SectorState,
    StandingOrderState,
    StandingOrderHistoryEntry,
    StandingOrderRecord,
    TuningNote,
    TrackEvidenceSummary,
    ValidationMessage,
    WorldControlPointState,
    WorldGroupState,
    WorldStateSnapshot,
    MatrixCaseResult,
    MatrixRunReport,
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class SQLiteStateStore:
    """Thin persistence API over SQLite."""

    def __init__(self, db_path: str | Path, *, enable_wal: bool = True) -> None:
        self.db_path = Path(db_path)
        self.enable_wal = enable_wal

    def initialize_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if self.enable_wal:
                connection.execute("PRAGMA journal_mode=WAL;")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    scenario_version TEXT NOT NULL,
                    scenario_name TEXT NOT NULL,
                    theater TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'dry',
                    status TEXT NOT NULL DEFAULT 'created',
                    config_digest TEXT,
                    config_snapshot_json TEXT,
                    red_backend_name TEXT,
                    blue_backend_name TEXT,
                    started_at TEXT,
                    paused_at TEXT,
                    resumed_at TEXT,
                    stopped_at TEXT,
                    failed_at TEXT,
                    terminal_reason TEXT,
                    evaluation_metadata_json TEXT
                );

                CREATE TABLE IF NOT EXISTS sectors (
                    run_id TEXT NOT NULL,
                    sector_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    neighbor_ids_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    center_lat REAL,
                    center_lng REAL,
                    radius_nm REAL NOT NULL,
                    PRIMARY KEY (run_id, sector_id)
                );

                CREATE TABLE IF NOT EXISTS control_points (
                    run_id TEXT NOT NULL,
                    control_point_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sector_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    owner TEXT,
                    strategic_value TEXT NOT NULL,
                    lat REAL,
                    lng REAL,
                    PRIMARY KEY (run_id, control_point_id)
                );

                CREATE TABLE IF NOT EXISTS scenario_zones (
                    run_id TEXT NOT NULL,
                    zone_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sector_id TEXT NOT NULL,
                    center_lat REAL NOT NULL,
                    center_lng REAL NOT NULL,
                    radius_nm REAL NOT NULL,
                    tags_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, zone_id)
                );

                CREATE TABLE IF NOT EXISTS coalition_state (
                    run_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    budget_remaining INTEGER NOT NULL,
                    objectives_json TEXT NOT NULL,
                    attrition_pool INTEGER NOT NULL,
                    replacement_pool INTEGER NOT NULL,
                    PRIMARY KEY (run_id, coalition)
                );

                CREATE TABLE IF NOT EXISTS standing_orders (
                    run_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    text TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    PRIMARY KEY (run_id, order_id)
                );

                CREATE TABLE IF NOT EXISTS active_groups (
                    run_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    group_type TEXT NOT NULL,
                    posture TEXT NOT NULL,
                    sector_id TEXT,
                    mobile INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    control_point_id TEXT,
                    attrition_count INTEGER NOT NULL,
                    replacement_pool INTEGER NOT NULL,
                    PRIMARY KEY (run_id, group_id)
                );

                CREATE TABLE IF NOT EXISTS reserve_groups (
                    run_id TEXT NOT NULL,
                    reserve_group_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    group_type TEXT NOT NULL,
                    available INTEGER NOT NULL,
                    cost INTEGER NOT NULL,
                    allowed_sector_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    emergency INTEGER NOT NULL,
                    attrition_count INTEGER NOT NULL,
                    replacement_pool INTEGER NOT NULL,
                    PRIMARY KEY (run_id, reserve_group_id)
                );

                CREATE TABLE IF NOT EXISTS deployment_restrictions (
                    run_id TEXT NOT NULL,
                    restriction_id TEXT NOT NULL,
                    coalition TEXT,
                    restriction_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    sector_ids_json TEXT NOT NULL,
                    control_point_ids_json TEXT NOT NULL,
                    adjacency_limited INTEGER NOT NULL,
                    PRIMARY KEY (run_id, restriction_id)
                );

                CREATE TABLE IF NOT EXISTS scenario_drafts (
                    draft_id TEXT PRIMARY KEY,
                    source_scenario_id TEXT NOT NULL,
                    scenario_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    theater TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    pydcs_mapping_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS world_state_snapshots (
                    run_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    theater TEXT NOT NULL,
                    mission_time TEXT,
                    last_ingest_at TEXT
                );

                CREATE TABLE IF NOT EXISTS world_groups (
                    run_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    coalition TEXT,
                    group_type TEXT NOT NULL,
                    category TEXT,
                    name TEXT,
                    sector_id TEXT,
                    control_point_id TEXT,
                    lat REAL,
                    lng REAL,
                    alt REAL,
                    heading REAL,
                    speed REAL,
                    health REAL,
                    source TEXT NOT NULL,
                    last_updated_at TEXT,
                    high_value INTEGER NOT NULL,
                    mobile INTEGER NOT NULL,
                    PRIMARY KEY (run_id, group_id)
                );

                CREATE TABLE IF NOT EXISTS world_control_points (
                    run_id TEXT NOT NULL,
                    control_point_id TEXT NOT NULL,
                    owner TEXT,
                    sector_id TEXT NOT NULL,
                    last_updated_at TEXT,
                    source TEXT NOT NULL,
                    PRIMARY KEY (run_id, control_point_id)
                );

                CREATE TABLE IF NOT EXISTS evidence_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    entity_id TEXT,
                    coalition TEXT,
                    sector_id TEXT,
                    occurred_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    fusion_update_id INTEGER NOT NULL,
                    coalition TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    mission_time TEXT,
                    known_friendly_group_ids_json TEXT NOT NULL,
                    sector_activity_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS contact_tracks (
                    run_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    knowledge_level TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    classification TEXT,
                    inferred INTEGER NOT NULL,
                    stale INTEGER NOT NULL,
                    archived INTEGER NOT NULL,
                    first_seen_at TEXT,
                    last_confirmed_at TEXT,
                    last_known_sector_id TEXT,
                    estimated_sector_id TEXT,
                    evidence_count INTEGER NOT NULL,
                    sources_json TEXT NOT NULL,
                    last_source TEXT,
                    seconds_since_last_confirmation INTEGER NOT NULL,
                    PRIMARY KEY (run_id, coalition, track_id)
                );

                CREATE TABLE IF NOT EXISTS fusion_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    active_track_count INTEGER NOT NULL,
                    archived_track_count INTEGER NOT NULL,
                    change_summary_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    decision_cycle INTEGER NOT NULL,
                    generated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    previous_observation_id INTEGER,
                    fusion_update_id INTEGER,
                    payload_json TEXT NOT NULL,
                    narrative_text TEXT NOT NULL,
                    action_links_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE (run_id, coalition, decision_cycle)
                );

                CREATE TABLE IF NOT EXISTS observation_attachment_artifacts (
                    observation_id INTEGER NOT NULL,
                    attachment_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    decision_cycle INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    submitted_to_backend INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (observation_id, attachment_id)
                );

                CREATE TABLE IF NOT EXISTS model_invocations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    decision_cycle INTEGER NOT NULL,
                    backend_name TEXT NOT NULL,
                    observation_id INTEGER,
                    requested_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    request_payload_json TEXT NOT NULL,
                    raw_response_text TEXT,
                    parsed_actions_json TEXT NOT NULL,
                    parse_status TEXT NOT NULL,
                    error_detail TEXT,
                    latency_ms INTEGER,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    validation_batch_id INTEGER,
                    wrapped_response_json TEXT,
                    failover_used INTEGER NOT NULL DEFAULT 0,
                    attempt_trace_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS ingest_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    mission_time TEXT,
                    olympus_available INTEGER NOT NULL,
                    grpc_available INTEGER NOT NULL,
                    normalized_batch_json TEXT NOT NULL,
                    error_classification TEXT,
                    error_detail TEXT
                );

                CREATE TABLE IF NOT EXISTS decision_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    decision_cycle INTEGER NOT NULL,
                    snapshot_time TEXT NOT NULL,
                    red_observation_id INTEGER,
                    blue_observation_id INTEGER,
                    red_invocation_id INTEGER,
                    blue_invocation_id INTEGER,
                    red_execution_batch_id INTEGER,
                    blue_execution_batch_id INTEGER,
                    classification TEXT NOT NULL DEFAULT 'both_failed',
                    summary_json TEXT NOT NULL,
                    UNIQUE (run_id, decision_cycle)
                );

                CREATE TABLE IF NOT EXISTS execution_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    decision_cycle INTEGER NOT NULL,
                    observation_id INTEGER,
                    model_invocation_id INTEGER,
                    validation_batch_id INTEGER,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    lifecycle_state TEXT NOT NULL DEFAULT 'pending',
                    status TEXT NOT NULL,
                    summary_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS execution_results (
                    batch_id INTEGER NOT NULL,
                    action_index INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    action_type TEXT,
                    status TEXT NOT NULL,
                    execution_summary TEXT NOT NULL,
                    command_count INTEGER NOT NULL,
                    resource_delta_json TEXT NOT NULL,
                    standing_order_delta_json TEXT NOT NULL,
                    resulting_entities_json TEXT NOT NULL,
                    error_detail TEXT,
                    applied_commands_json TEXT NOT NULL,
                    PRIMARY KEY (batch_id, action_id)
                );

                CREATE TABLE IF NOT EXISTS execution_standing_orders (
                    run_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    fingerprint TEXT NOT NULL,
                    text TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    last_updated_at TEXT,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, order_id)
                );

                CREATE TABLE IF NOT EXISTS execution_standing_order_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    transition TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS current_execution_notes (
                    run_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    decision_cycle INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    note TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, coalition, entity_type, entity_id)
                );

                CREATE TABLE IF NOT EXISTS action_validation_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    decision_cycle INTEGER NOT NULL,
                    submitted_at TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL,
                    non_hold_action_count INTEGER NOT NULL,
                    soft_cap_exceeded INTEGER NOT NULL,
                    batch_messages_json TEXT NOT NULL,
                    latest_observation_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS action_validation_results (
                    batch_id INTEGER NOT NULL,
                    action_index INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    action_type TEXT,
                    status TEXT NOT NULL,
                    rejection_code TEXT,
                    message TEXT,
                    normalized_params_json TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    validated_stages_json TEXT NOT NULL,
                    PRIMARY KEY (batch_id, action_id)
                );

                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_run_summaries (
                    run_id TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    summary_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_cycle_metrics (
                    run_id TEXT NOT NULL,
                    decision_cycle INTEGER NOT NULL,
                    generated_at TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, decision_cycle)
                );

                CREATE TABLE IF NOT EXISTS fairness_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    coalition TEXT,
                    decision_cycle INTEGER,
                    finding_type TEXT NOT NULL,
                    machine_status TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    score_penalty INTEGER NOT NULL,
                    finding_json TEXT NOT NULL,
                    reviewer_note TEXT,
                    reviewed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS evaluation_matrix_case_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_name TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    repeat_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    red_backend TEXT NOT NULL,
                    blue_backend TEXT NOT NULL,
                    decision_cadence_sec INTEGER NOT NULL,
                    run_cycles INTEGER NOT NULL,
                    run_id TEXT,
                    evaluation_run_id TEXT,
                    detail TEXT
                );

                CREATE TABLE IF NOT EXISTS evaluation_matrix_reports (
                    profile_name TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    report_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS replay_exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    exported_at TEXT NOT NULL,
                    file_inventory_json TEXT NOT NULL,
                    includes_evaluation_summary INTEGER NOT NULL,
                    includes_cycle_evaluations INTEGER NOT NULL,
                    includes_fairness_findings INTEGER NOT NULL,
                    includes_matrix_report INTEGER NOT NULL
                );
                """
            )
            self._ensure_column(connection, "decision_cycles", "classification", "TEXT NOT NULL DEFAULT 'both_failed'")
            self._ensure_column(connection, "decision_cycles", "red_execution_batch_id", "INTEGER")
            self._ensure_column(connection, "decision_cycles", "blue_execution_batch_id", "INTEGER")
            self._ensure_column(connection, "knowledge_snapshots", "fusion_update_id", "INTEGER")
            self._ensure_column(connection, "observations", "fusion_update_id", "INTEGER")
            self._ensure_column(connection, "execution_batches", "lifecycle_state", "TEXT NOT NULL DEFAULT 'pending'")
            self._ensure_column(connection, "runs", "mode", "TEXT NOT NULL DEFAULT 'dry'")
            self._ensure_column(connection, "runs", "status", "TEXT NOT NULL DEFAULT 'created'")
            self._ensure_column(connection, "runs", "config_digest", "TEXT")
            self._ensure_column(connection, "runs", "config_snapshot_json", "TEXT")
            self._ensure_column(connection, "runs", "red_backend_name", "TEXT")
            self._ensure_column(connection, "runs", "blue_backend_name", "TEXT")
            self._ensure_column(connection, "runs", "started_at", "TEXT")
            self._ensure_column(connection, "runs", "paused_at", "TEXT")
            self._ensure_column(connection, "runs", "resumed_at", "TEXT")
            self._ensure_column(connection, "runs", "stopped_at", "TEXT")
            self._ensure_column(connection, "runs", "failed_at", "TEXT")
            self._ensure_column(connection, "runs", "terminal_reason", "TEXT")
            self._ensure_column(connection, "runs", "evaluation_metadata_json", "TEXT")
            self._ensure_column(connection, "model_invocations", "failover_used", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "model_invocations", "attempt_trace_json", "TEXT NOT NULL DEFAULT '[]'")

    def create_run_from_scenario(
        self,
        scenario: ScenarioDefinition,
        *,
        mode: str = "dry",
        config_digest: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
        red_backend_name: str | None = None,
        blue_backend_name: str | None = None,
        evaluation_metadata: dict[str, Any] | None = None,
    ) -> str:
        self.initialize_schema()
        run_id = uuid4().hex
        created_at = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, scenario_id, scenario_version, scenario_name, theater, created_at, mode, status,
                    config_digest, config_snapshot_json, red_backend_name, blue_backend_name, evaluation_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    scenario.id,
                    scenario.version,
                    scenario.name,
                    scenario.theater,
                    created_at.isoformat(),
                    mode,
                    RunLifecycleStatus.CREATED.value,
                    config_digest,
                    _json(config_snapshot) if config_snapshot is not None else None,
                    red_backend_name,
                    blue_backend_name,
                    _json(evaluation_metadata) if evaluation_metadata is not None else None,
                ),
            )
            for sector in scenario.sectors:
                self._insert_sector(connection, run_id, sector)
                self.append_event(
                    connection,
                    EventRecord(
                        id=None,
                        run_id=run_id,
                        event_type="sector_initialized",
                        entity_type="sector",
                        entity_id=sector.id,
                        occurred_at=created_at,
                        payload=asdict(sector),
                    ),
                )
            for control_point in scenario.control_points:
                self._insert_control_point(connection, run_id, control_point)
                self._upsert_world_control_point(
                    connection,
                    run_id,
                    WorldControlPointState(
                        control_point_id=control_point.id,
                        owner=control_point.owner,
                        sector_id=control_point.sector_id,
                        last_updated_at=created_at,
                        source="scenario_seed",
                    ),
                )
                self.append_event(
                    connection,
                    EventRecord(
                        id=None,
                        run_id=run_id,
                        event_type="control_point_initialized",
                        entity_type="control_point",
                        entity_id=control_point.id,
                        occurred_at=created_at,
                        payload=asdict(control_point),
                    ),
                )
            for zone in scenario.zones:
                self._insert_zone(connection, run_id, zone)
                self.append_event(
                    connection,
                    EventRecord(
                        id=None,
                        run_id=run_id,
                        event_type="zone_initialized",
                        entity_type="scenario_zone",
                        entity_id=zone.id,
                        occurred_at=created_at,
                        payload=asdict(zone),
                    ),
                )
            for coalition_state in scenario.coalitions:
                self._insert_coalition_state(connection, run_id, coalition_state)
                for standing_order in coalition_state.standing_orders:
                    self._insert_standing_order(connection, run_id, standing_order)
                self.append_event(
                    connection,
                    EventRecord(
                        id=None,
                        run_id=run_id,
                        event_type="coalition_initialized",
                        entity_type="coalition_state",
                        entity_id=coalition_state.coalition.value,
                        occurred_at=created_at,
                        payload={
                            "coalition": coalition_state.coalition.value,
                            "budget_remaining": coalition_state.budget_remaining,
                            "objectives": coalition_state.objectives,
                        },
                    ),
                )
            for group in scenario.active_groups:
                connection.execute(
                    """
                    INSERT INTO active_groups (
                        run_id, group_id, coalition, group_type, posture, sector_id, mobile, status,
                        control_point_id, attrition_count, replacement_pool
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        group.id,
                        group.coalition.value,
                        group.group_type,
                        group.posture.value,
                        group.sector_id,
                        int(group.mobile),
                        group.status,
                        group.control_point_id,
                        group.attrition_count,
                        group.replacement_pool,
                    ),
                )
                self._upsert_world_group(
                    connection,
                    run_id,
                    WorldGroupState(
                        id=group.id,
                        source_id=group.id,
                        coalition=group.coalition,
                        group_type=group.group_type,
                        category="scenario_group",
                        name=group.id,
                        sector_id=group.sector_id,
                        control_point_id=group.control_point_id,
                        source="scenario_seed",
                        last_updated_at=created_at,
                        high_value="air_defense" in group.group_type or "fires" in group.group_type,
                        mobile=group.mobile,
                    ),
                )
                self.append_event(
                    connection,
                    EventRecord(
                        id=None,
                        run_id=run_id,
                        event_type="active_group_initialized",
                        entity_type="active_group",
                        entity_id=group.id,
                        occurred_at=created_at,
                        payload=asdict(group),
                    ),
                )
            for reserve in scenario.reserve_groups:
                connection.execute(
                    """
                    INSERT INTO reserve_groups (
                        run_id, reserve_group_id, coalition, group_type, available, cost,
                        allowed_sector_ids_json, status, emergency, attrition_count, replacement_pool
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        reserve.id,
                        reserve.coalition.value,
                        reserve.group_type,
                        int(reserve.available),
                        reserve.cost,
                        _json(reserve.allowed_sector_ids),
                        reserve.status,
                        int(reserve.emergency),
                        reserve.attrition_count,
                        reserve.replacement_pool,
                    ),
                )
                self.append_event(
                    connection,
                    EventRecord(
                        id=None,
                        run_id=run_id,
                        event_type="reserve_group_initialized",
                        entity_type="reserve_group",
                        entity_id=reserve.id,
                        occurred_at=created_at,
                        payload=asdict(reserve),
                    ),
                )
            for restriction in scenario.deployment_restrictions:
                self._insert_restriction(connection, run_id, restriction)
                self.append_event(
                    connection,
                    EventRecord(
                        id=None,
                        run_id=run_id,
                        event_type="restriction_initialized",
                        entity_type="deployment_restriction",
                        entity_id=restriction.id,
                        occurred_at=created_at,
                        payload=asdict(restriction),
                    ),
                )
            self._upsert_world_state_snapshot(
                connection,
                run_id,
                scenario.id,
                scenario.theater,
                mission_time=None,
                last_ingest_at=created_at,
            )
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=run_id,
                    event_type="scenario_initialized",
                    entity_type="scenario",
                    entity_id=scenario.id,
                    occurred_at=created_at,
                    payload={
                        "scenario_id": scenario.id,
                        "scenario_version": scenario.version,
                        "theater": scenario.theater,
                        "sector_count": len(scenario.sectors),
                        "control_point_count": len(scenario.control_points),
                        "zone_count": len(scenario.zones),
                        "active_group_count": len(scenario.active_groups),
                        "reserve_group_count": len(scenario.reserve_groups),
                    },
                ),
            )
        return run_id

    def append_event(self, connection: sqlite3.Connection, event: EventRecord) -> None:
        connection.execute(
            """
            INSERT INTO event_log (run_id, event_type, entity_type, entity_id, occurred_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.run_id,
                event.event_type,
                event.entity_type,
                event.entity_id,
                event.occurred_at.isoformat(),
                _json(event.payload),
            ),
        )

    def get_latest_run_id(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise PersistenceError("No initialized scenario runs found in the SQLite store.")
        return str(row["run_id"])

    def list_run_ids(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT run_id FROM runs ORDER BY created_at, run_id").fetchall()
        return tuple(str(row["run_id"]) for row in rows)

    def get_run_control_state(self, run_id: str | None = None) -> RunControlState:
        resolved_run_id = run_id or self.get_latest_run_id()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT run_id, scenario_id, scenario_version, scenario_name, theater, created_at, mode, status,
                       config_digest, config_snapshot_json, red_backend_name, blue_backend_name,
                       started_at, paused_at, resumed_at, stopped_at, failed_at, terminal_reason,
                       evaluation_metadata_json
                FROM runs
                WHERE run_id = ?
                """,
                (resolved_run_id,),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"Unknown run id: {resolved_run_id}")
        return self._row_to_run_control_state(row)

    def list_run_control_states(self) -> tuple[RunControlState, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, scenario_id, scenario_version, scenario_name, theater, mode, status, created_at,
                       config_digest, config_snapshot_json, red_backend_name, blue_backend_name, started_at,
                       paused_at, resumed_at, stopped_at, failed_at, terminal_reason, evaluation_metadata_json
                FROM runs
                ORDER BY created_at DESC, run_id DESC
                """
            ).fetchall()
        return tuple(self._row_to_run_control_state(row) for row in rows)

    def update_run_status(
        self,
        run_id: str,
        status: RunLifecycleStatus,
        *,
        changed_at: datetime,
        terminal_reason: str | None = None,
    ) -> RunControlState:
        current = self.get_run_control_state(run_id)
        with self._connect() as connection:
            columns: dict[str, Any] = {
                "status": status.value,
                "terminal_reason": terminal_reason if status in {RunLifecycleStatus.STOPPED, RunLifecycleStatus.FAILED} else None,
            }
            if status is RunLifecycleStatus.RUNNING:
                if current.status is RunLifecycleStatus.CREATED:
                    columns["started_at"] = changed_at.isoformat()
                else:
                    columns["resumed_at"] = changed_at.isoformat()
                columns["paused_at"] = None
            elif status is RunLifecycleStatus.PAUSED:
                columns["paused_at"] = changed_at.isoformat()
            elif status is RunLifecycleStatus.STOPPED:
                columns["stopped_at"] = changed_at.isoformat()
            elif status is RunLifecycleStatus.FAILED:
                columns["failed_at"] = changed_at.isoformat()
            assignments = ", ".join(f"{column} = ?" for column in columns)
            connection.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?",
                (*columns.values(), run_id),
            )
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=run_id,
                    event_type="run_status_changed",
                    entity_type="run",
                    entity_id=run_id,
                    occurred_at=changed_at,
                    payload={
                        "previous_status": current.status.value,
                        "status": status.value,
                        "terminal_reason": terminal_reason,
                    },
                ),
            )
        return self.get_run_control_state(run_id)

    def get_run_summary(self, run_id: str | None = None) -> dict[str, Any]:
        resolved_run_id = run_id or self.get_latest_run_id()
        run_control = self.get_run_control_state(resolved_run_id)
        with self._connect() as connection:
            run_row = connection.execute(
                """
                SELECT run_id, scenario_id, scenario_version, scenario_name, theater, created_at
                FROM runs WHERE run_id = ?
                """,
                (resolved_run_id,),
            ).fetchone()
            if run_row is None:
                raise PersistenceError(f"Unknown run id: {resolved_run_id}")

            coalition_rows = connection.execute(
                """
                SELECT coalition, budget_remaining, objectives_json, attrition_pool, replacement_pool
                FROM coalition_state WHERE run_id = ? ORDER BY coalition
                """,
                (resolved_run_id,),
            ).fetchall()
            standing_order_count = connection.execute(
                "SELECT COUNT(*) AS count FROM standing_orders WHERE run_id = ? AND active = 1",
                (resolved_run_id,),
            ).fetchone()["count"]
            active_group_count = connection.execute(
                "SELECT COUNT(*) AS count FROM active_groups WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            reserve_group_count = connection.execute(
                "SELECT COUNT(*) AS count FROM reserve_groups WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            control_point_rows = connection.execute(
                """
                SELECT owner, COUNT(*) AS count
                FROM control_points
                WHERE run_id = ?
                GROUP BY owner
                ORDER BY owner
                """,
                (resolved_run_id,),
            ).fetchall()
            zone_count = connection.execute(
                "SELECT COUNT(*) AS count FROM scenario_zones WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            event_count = connection.execute(
                "SELECT COUNT(*) AS count FROM event_log WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            world_group_count = connection.execute(
                "SELECT COUNT(*) AS count FROM world_groups WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            evidence_count = connection.execute(
                "SELECT COUNT(*) AS count FROM evidence_records WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            active_track_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM contact_tracks
                WHERE run_id = ? AND archived = 0
                """,
                (resolved_run_id,),
            ).fetchone()["count"]
            action_validation_batch_count = connection.execute(
                "SELECT COUNT(*) AS count FROM action_validation_batches WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            model_invocation_count = connection.execute(
                "SELECT COUNT(*) AS count FROM model_invocations WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            decision_cycle_count = connection.execute(
                "SELECT COUNT(*) AS count FROM decision_cycles WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            latest_decision_cycle_row = connection.execute(
                "SELECT MAX(decision_cycle) AS latest_decision_cycle FROM decision_cycles WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()
            execution_batch_count = connection.execute(
                "SELECT COUNT(*) AS count FROM execution_batches WHERE run_id = ?",
                (resolved_run_id,),
            ).fetchone()["count"]
            execution_result_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM execution_results
                WHERE batch_id IN (SELECT id FROM execution_batches WHERE run_id = ?)
                """,
                (resolved_run_id,),
            ).fetchone()["count"]

        coalitions = {
            row["coalition"]: {
                "budget_remaining": row["budget_remaining"],
                "objectives": json.loads(row["objectives_json"]),
                "attrition_pool": row["attrition_pool"],
                "replacement_pool": row["replacement_pool"],
            }
            for row in coalition_rows
        }
        control_point_ownership = {
            (row["owner"] if row["owner"] is not None else "unowned"): row["count"] for row in control_point_rows
        }
        return {
            "run_id": resolved_run_id,
            "scenario_id": run_row["scenario_id"],
            "scenario_version": run_row["scenario_version"],
            "scenario_name": run_row["scenario_name"],
            "theater": run_row["theater"],
            "created_at": run_row["created_at"],
            "mode": run_control.mode,
            "status": run_control.status.value,
            "config_digest": run_control.config_digest,
            "red_backend_name": run_control.red_backend_name,
            "blue_backend_name": run_control.blue_backend_name,
            "started_at": run_control.started_at.isoformat() if run_control.started_at else None,
            "paused_at": run_control.paused_at.isoformat() if run_control.paused_at else None,
            "resumed_at": run_control.resumed_at.isoformat() if run_control.resumed_at else None,
            "stopped_at": run_control.stopped_at.isoformat() if run_control.stopped_at else None,
            "failed_at": run_control.failed_at.isoformat() if run_control.failed_at else None,
            "terminal_reason": run_control.terminal_reason,
            "evaluation_metadata": run_control.evaluation_metadata,
            "coalitions": coalitions,
            "active_group_count": active_group_count,
            "reserve_group_count": reserve_group_count,
            "zone_count": zone_count,
            "standing_order_count": standing_order_count,
            "control_point_ownership": control_point_ownership,
            "event_count": event_count,
            "world_group_count": world_group_count,
            "evidence_count": evidence_count,
            "active_track_count": active_track_count,
            "action_validation_batch_count": action_validation_batch_count,
            "model_invocation_count": model_invocation_count,
            "decision_cycle_count": decision_cycle_count,
            "latest_decision_cycle": latest_decision_cycle_row["latest_decision_cycle"],
            "execution_batch_count": execution_batch_count,
            "execution_result_count": execution_result_count,
            "db_path": str(self.db_path),
        }

    def save_scenario_draft(self, draft: ScenarioDraftView) -> str:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scenario_drafts (
                    draft_id, source_scenario_id, scenario_id, name, theater, updated_at, payload_json, pydcs_mapping_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    source_scenario_id = excluded.source_scenario_id,
                    scenario_id = excluded.scenario_id,
                    name = excluded.name,
                    theater = excluded.theater,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json,
                    pydcs_mapping_json = excluded.pydcs_mapping_json
                """,
                (
                    draft.draft_id,
                    draft.source_scenario_id,
                    draft.scenario_id,
                    draft.display_name,
                    draft.theater,
                    draft.updated_at.isoformat(),
                    _json(draft.scenario),
                    _json(draft.pydcs_mapping),
                ),
            )
        return draft.draft_id

    def get_scenario_draft(self, draft_id: str) -> ScenarioDraftView:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT draft_id, source_scenario_id, scenario_id, name, theater, updated_at, payload_json, pydcs_mapping_json
                FROM scenario_drafts
                WHERE draft_id = ?
                """,
                (draft_id,),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"Unknown scenario draft id: {draft_id}")
        return self._row_to_scenario_draft(row)

    def list_scenario_drafts(self) -> tuple[ScenarioDraftView, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT draft_id, source_scenario_id, scenario_id, name, theater, updated_at, payload_json, pydcs_mapping_json
                FROM scenario_drafts
                ORDER BY updated_at DESC, draft_id DESC
                """
            ).fetchall()
        return tuple(self._row_to_scenario_draft(row) for row in rows)

    def delete_scenario_draft(self, draft_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM scenario_drafts WHERE draft_id = ?", (draft_id,))
        if cursor.rowcount == 0:
            raise PersistenceError(f"Unknown scenario draft id: {draft_id}")

    def get_coalition_states(self, run_id: str) -> tuple[CoalitionState, ...]:
        with self._connect() as connection:
            coalition_rows = connection.execute(
                """
                SELECT coalition, budget_remaining, objectives_json, attrition_pool, replacement_pool
                FROM coalition_state WHERE run_id = ? ORDER BY coalition
                """,
                (run_id,),
            ).fetchall()
            standing_order_rows = connection.execute(
                """
                SELECT order_id, coalition, text, active
                FROM standing_orders WHERE run_id = ?
                ORDER BY order_id
                """,
                (run_id,),
            ).fetchall()
        orders_by_coalition: dict[str, list[StandingOrderState]] = {Coalition.RED.value: [], Coalition.BLUE.value: []}
        for row in standing_order_rows:
            orders_by_coalition[row["coalition"]].append(
                StandingOrderState(
                    id=row["order_id"],
                    coalition=Coalition(row["coalition"]),
                    text=row["text"],
                    active=bool(row["active"]),
                )
            )
        return tuple(
            CoalitionState(
                coalition=Coalition(row["coalition"]),
                budget_remaining=row["budget_remaining"],
                objectives=tuple(json.loads(row["objectives_json"])),
                standing_orders=tuple(orders_by_coalition[row["coalition"]]),
                attrition_pool=row["attrition_pool"],
                replacement_pool=row["replacement_pool"],
            )
            for row in coalition_rows
        )

    def list_events(self, run_id: str) -> tuple[EventRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, event_type, entity_type, entity_id, occurred_at, payload_json
                FROM event_log WHERE run_id = ? ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            EventRecord(
                id=row["id"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        )

    def get_active_groups(self, run_id: str) -> tuple[ActiveGroupState, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT group_id, coalition, group_type, posture, sector_id, mobile, status, control_point_id,
                       attrition_count, replacement_pool
                FROM active_groups WHERE run_id = ? ORDER BY group_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            ActiveGroupState(
                id=row["group_id"],
                coalition=Coalition(row["coalition"]),
                group_type=row["group_type"],
                posture=GroupPosture(row["posture"]),
                sector_id=row["sector_id"],
                mobile=bool(row["mobile"]),
                status=row["status"],
                control_point_id=row["control_point_id"],
                attrition_count=row["attrition_count"],
                replacement_pool=row["replacement_pool"],
            )
            for row in rows
        )

    def get_reserve_groups(self, run_id: str, coalition: Coalition | None = None) -> tuple[ReserveGroupState, ...]:
        query = """
            SELECT reserve_group_id, coalition, group_type, available, cost, allowed_sector_ids_json, status,
                   emergency, attrition_count, replacement_pool
            FROM reserve_groups WHERE run_id = ?
        """
        params: tuple[object, ...] = (run_id,)
        if coalition is not None:
            query += " AND coalition = ?"
            params = (run_id, coalition.value)
        query += " ORDER BY reserve_group_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(
            ReserveGroupState(
                id=row["reserve_group_id"],
                coalition=Coalition(row["coalition"]),
                group_type=row["group_type"],
                available=bool(row["available"]),
                cost=row["cost"],
                allowed_sector_ids=tuple(json.loads(row["allowed_sector_ids_json"])),
                status=row["status"],
                emergency=bool(row["emergency"]),
                attrition_count=row["attrition_count"],
                replacement_pool=row["replacement_pool"],
            )
            for row in rows
        )

    def save_observation_artifact(self, artifact: ObservationArtifact) -> int:
        payload_json = _json(asdict(artifact.observation))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO observations (
                    run_id, coalition, decision_cycle, generated_at, schema_version,
                    previous_observation_id, fusion_update_id, payload_json, narrative_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, coalition, decision_cycle) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    schema_version = excluded.schema_version,
                    previous_observation_id = excluded.previous_observation_id,
                    fusion_update_id = excluded.fusion_update_id,
                    payload_json = excluded.payload_json,
                    narrative_text = excluded.narrative_text
                """,
                (
                    artifact.run_id,
                    artifact.coalition.value,
                    artifact.decision_cycle,
                    artifact.generated_at.isoformat(),
                    artifact.observation.meta.schema_version,
                    artifact.previous_observation_id,
                    artifact.fusion_update_id,
                    payload_json,
                    artifact.narrative,
                ),
            )
            row = connection.execute(
                """
                SELECT id FROM observations
                WHERE run_id = ? AND coalition = ? AND decision_cycle = ?
                """,
                (artifact.run_id, artifact.coalition.value, artifact.decision_cycle),
            ).fetchone()
            observation_id = int(row["id"]) if row is not None else int(cursor.lastrowid)
            connection.execute(
                "DELETE FROM observation_attachment_artifacts WHERE observation_id = ?",
                (observation_id,),
            )
            for attachment in artifact.attachment_artifacts:
                connection.execute(
                    """
                    INSERT INTO observation_attachment_artifacts (
                        observation_id, attachment_id, run_id, coalition, decision_cycle, media_type, role,
                        file_path, submitted_to_backend, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation_id,
                        attachment.attachment_id,
                        attachment.run_id,
                        attachment.coalition.value,
                        attachment.decision_cycle,
                        attachment.media_type,
                        attachment.role,
                        attachment.file_path,
                        int(attachment.submitted_to_backend),
                        _json(attachment.metadata),
                    ),
                )
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=artifact.run_id,
                    event_type="observation_persisted",
                    entity_type="observation",
                    entity_id=str(observation_id),
                    occurred_at=artifact.generated_at,
                    payload={
                        "coalition": artifact.coalition.value,
                        "decision_cycle": artifact.decision_cycle,
                        "fusion_update_id": artifact.fusion_update_id,
                        "attachment_count": len(artifact.attachment_artifacts),
                    },
                ),
            )
        return observation_id

    def get_latest_observation(self, run_id: str, coalition: Coalition) -> ObservationArtifact:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, generated_at, previous_observation_id, fusion_update_id, payload_json, narrative_text
                FROM observations
                WHERE run_id = ? AND coalition = ?
                ORDER BY decision_cycle DESC, id DESC
                LIMIT 1
                """,
                (run_id, coalition.value),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"No observation found for coalition '{coalition.value}' in run '{run_id}'.")
        return self._row_to_observation_artifact(row)

    def get_previous_observation(
        self,
        run_id: str,
        coalition: Coalition,
        decision_cycle: int,
    ) -> ObservationArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, generated_at, previous_observation_id, fusion_update_id, payload_json, narrative_text
                FROM observations
                WHERE run_id = ? AND coalition = ? AND decision_cycle < ?
                ORDER BY decision_cycle DESC, id DESC
                LIMIT 1
                """,
                (run_id, coalition.value, decision_cycle),
            ).fetchone()
        return self._row_to_observation_artifact(row) if row is not None else None

    def list_observations(self, run_id: str, coalition: Coalition | None = None) -> tuple[ObservationArtifact, ...]:
        query = """
            SELECT id, run_id, coalition, decision_cycle, generated_at, previous_observation_id, fusion_update_id, payload_json, narrative_text
            FROM observations
            WHERE run_id = ?
        """
        params: tuple[object, ...] = (run_id,)
        if coalition is not None:
            query += " AND coalition = ?"
            params = (run_id, coalition.value)
        query += " ORDER BY coalition, decision_cycle, id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_observation_artifact(row) for row in rows)

    def save_model_invocation_result(self, result: ModelInvocationResult) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO model_invocations (
                    run_id, coalition, decision_cycle, backend_name, observation_id, requested_at, completed_at,
                    status, attempt_count, request_payload_json, raw_response_text, parsed_actions_json, parse_status,
                    error_detail, latency_ms, prompt_tokens, completion_tokens, total_tokens, validation_batch_id,
                    wrapped_response_json, failover_used, attempt_trace_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.coalition.value,
                    result.decision_cycle,
                    result.backend_name,
                    result.observation_id,
                    result.requested_at.isoformat(),
                    result.completed_at.isoformat(),
                    result.status,
                    result.attempt_count,
                    _json(result.request_payload),
                    result.raw_response,
                    _json(result.parsed_actions),
                    result.parse_status,
                    result.error_detail,
                    result.latency_ms,
                    result.prompt_tokens,
                    result.completion_tokens,
                    result.total_tokens,
                    result.validation_batch_id,
                    _json(result.wrapped_response_object) if result.wrapped_response_object is not None else None,
                    int(result.failover_used),
                    _json(tuple(asdict(item) for item in result.attempt_trace)),
                ),
            )
            invocation_id = int(cursor.lastrowid)
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=result.run_id,
                    event_type="model_invocation_persisted",
                    entity_type="model_invocation",
                    entity_id=str(invocation_id),
                    occurred_at=result.completed_at,
                    payload={
                        "coalition": result.coalition.value,
                        "decision_cycle": result.decision_cycle,
                        "backend_name": result.backend_name,
                        "status": result.status,
                        "parse_status": result.parse_status,
                        "validation_batch_id": result.validation_batch_id,
                        "failover_used": result.failover_used,
                    },
                ),
            )
        return invocation_id

    def save_ingest_cycle_result(self, result: IngestCycleResult) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ingest_cycles (
                    run_id, occurred_at, mission_time, olympus_available, grpc_available,
                    normalized_batch_json, error_classification, error_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.occurred_at.isoformat(),
                    result.mission_time,
                    int(result.olympus_available),
                    int(result.grpc_available),
                    _json(asdict(result.normalized_batch)),
                    result.error_classification,
                    result.error_detail,
                ),
            )
            ingest_id = int(cursor.lastrowid)
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=result.run_id,
                    event_type="integration_ingest_cycle_persisted",
                    entity_type="ingest_cycle",
                    entity_id=str(ingest_id),
                    occurred_at=result.occurred_at,
                    payload={
                        "mission_time": result.mission_time,
                        "olympus_available": result.olympus_available,
                        "grpc_available": result.grpc_available,
                        "normalized_batch": asdict(result.normalized_batch),
                        "error_classification": result.error_classification,
                    },
                ),
            )
        return ingest_id

    def list_ingest_cycle_results(self, run_id: str) -> tuple[IngestCycleResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, occurred_at, mission_time, olympus_available, grpc_available,
                       normalized_batch_json, error_classification, error_detail
                FROM ingest_cycles
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return tuple(self._row_to_ingest_cycle_result(row) for row in rows)

    def get_latest_model_invocation(self, run_id: str, coalition: Coalition) -> ModelInvocationResult:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, backend_name, observation_id, requested_at, completed_at,
                       status, attempt_count, request_payload_json, raw_response_text, parsed_actions_json, parse_status,
                       error_detail, latency_ms, prompt_tokens, completion_tokens, total_tokens, validation_batch_id,
                       wrapped_response_json, failover_used, attempt_trace_json
                FROM model_invocations
                WHERE run_id = ? AND coalition = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, coalition.value),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"No model invocations found for coalition '{coalition.value}' in run '{run_id}'.")
        return self._row_to_model_invocation_result(row)

    def list_model_invocations(
        self,
        run_id: str,
        coalition: Coalition | None = None,
    ) -> tuple[ModelInvocationResult, ...]:
        query = """
            SELECT id, run_id, coalition, decision_cycle, backend_name, observation_id, requested_at, completed_at,
                   status, attempt_count, request_payload_json, raw_response_text, parsed_actions_json, parse_status,
                   error_detail, latency_ms, prompt_tokens, completion_tokens, total_tokens, validation_batch_id,
                   wrapped_response_json, failover_used, attempt_trace_json
            FROM model_invocations
            WHERE run_id = ?
        """
        params: tuple[object, ...] = (run_id,)
        if coalition is not None:
            query += " AND coalition = ?"
            params = (run_id, coalition.value)
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_model_invocation_result(row) for row in rows)

    def create_execution_batch(self, batch: ExecutionBatchResult) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO execution_batches (
                    run_id, coalition, decision_cycle, observation_id, model_invocation_id, validation_batch_id,
                    started_at, completed_at, lifecycle_state, status, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.run_id,
                    batch.coalition.value,
                    batch.decision_cycle,
                    batch.observation_id,
                    batch.model_invocation_id,
                    batch.validation_batch_id,
                    batch.started_at.isoformat(),
                    batch.completed_at.isoformat() if batch.completed_at else None,
                    batch.lifecycle_state.value,
                    batch.status.value,
                    _json(batch.summary),
                ),
            )
            batch_id = int(cursor.lastrowid)
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=batch.run_id,
                    event_type="execution_batch_persisted",
                    entity_type="execution_batch",
                    entity_id=str(batch_id),
                    occurred_at=batch.started_at,
                    payload={
                        "coalition": batch.coalition.value,
                        "decision_cycle": batch.decision_cycle,
                        "lifecycle_state": batch.lifecycle_state.value,
                        "status": batch.status.value,
                        "validation_batch_id": batch.validation_batch_id,
                    },
                ),
            )
        return batch_id

    def finalize_execution_batch(self, batch: ExecutionBatchResult) -> int:
        if batch.id is None:
            raise PersistenceError("Execution batch id is required for finalization.")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE execution_batches
                SET completed_at = ?, lifecycle_state = ?, status = ?, summary_json = ?
                WHERE id = ?
                """,
                (
                    batch.completed_at.isoformat() if batch.completed_at else None,
                    batch.lifecycle_state.value,
                    batch.status.value,
                    _json(batch.summary),
                    batch.id,
                ),
            )
            connection.execute("DELETE FROM execution_results WHERE batch_id = ?", (batch.id,))
            for index, result in enumerate(batch.results):
                connection.execute(
                    """
                    INSERT INTO execution_results (
                        batch_id, action_index, action_id, action_type, status, execution_summary, command_count,
                        resource_delta_json, standing_order_delta_json, resulting_entities_json, error_detail,
                        applied_commands_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch.id,
                        index,
                        result.action_id,
                        result.action_type.value if result.action_type else None,
                        result.status.value,
                        result.execution_summary,
                        result.command_count,
                        _json(result.resource_delta),
                        _json(result.standing_order_delta),
                        _json(result.resulting_entities),
                        result.error_detail,
                        _json([asdict(command) for command in result.applied_commands]),
                    ),
                )
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=batch.run_id,
                    event_type="execution_batch_finalized",
                    entity_type="execution_batch",
                    entity_id=str(batch.id),
                    occurred_at=batch.completed_at or batch.started_at,
                    payload={
                        "coalition": batch.coalition.value,
                        "decision_cycle": batch.decision_cycle,
                        "lifecycle_state": batch.lifecycle_state.value,
                        "status": batch.status.value,
                        "validation_batch_id": batch.validation_batch_id,
                    },
                ),
            )
        return batch.id

    def save_execution_batch_result(self, batch: ExecutionBatchResult) -> int:
        if batch.id is None:
            batch_id = self.create_execution_batch(
                ExecutionBatchResult(
                    id=None,
                    run_id=batch.run_id,
                    coalition=batch.coalition,
                    decision_cycle=batch.decision_cycle,
                    observation_id=batch.observation_id,
                    model_invocation_id=batch.model_invocation_id,
                    validation_batch_id=batch.validation_batch_id,
                    started_at=batch.started_at,
                    completed_at=None,
                    lifecycle_state=ExecutionLifecycleState.PENDING,
                    status=batch.status,
                    results=(),
                    summary=(),
                )
            )
            batch = ExecutionBatchResult(
                id=batch_id,
                run_id=batch.run_id,
                coalition=batch.coalition,
                decision_cycle=batch.decision_cycle,
                observation_id=batch.observation_id,
                model_invocation_id=batch.model_invocation_id,
                validation_batch_id=batch.validation_batch_id,
                started_at=batch.started_at,
                completed_at=batch.completed_at,
                lifecycle_state=batch.lifecycle_state,
                status=batch.status,
                results=batch.results,
                summary=batch.summary,
            )
        return self.finalize_execution_batch(batch)

    def get_latest_execution_batch(self, run_id: str, coalition: Coalition) -> ExecutionBatchResult:
        with self._connect() as connection:
            batch_row = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, observation_id, model_invocation_id,
                       validation_batch_id, started_at, completed_at, lifecycle_state, status, summary_json
                FROM execution_batches
                WHERE run_id = ? AND coalition = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, coalition.value),
            ).fetchone()
            if batch_row is None:
                raise PersistenceError(f"No execution batches found for coalition '{coalition.value}' in run '{run_id}'.")
            result_rows = connection.execute(
                """
                SELECT batch_id, action_index, action_id, action_type, status, execution_summary, command_count,
                       resource_delta_json, standing_order_delta_json, resulting_entities_json, error_detail,
                       applied_commands_json
                FROM execution_results
                WHERE batch_id = ?
                ORDER BY action_index
                """,
                (batch_row["id"],),
            ).fetchall()
        return self._row_to_execution_batch_result(batch_row, result_rows)

    def list_execution_batches(
        self,
        run_id: str,
        coalition: Coalition | None = None,
    ) -> tuple[ExecutionBatchResult, ...]:
        query = """
            SELECT id, run_id, coalition, decision_cycle, observation_id, model_invocation_id,
                   validation_batch_id, started_at, completed_at, lifecycle_state, status, summary_json
            FROM execution_batches
            WHERE run_id = ?
        """
        params: tuple[object, ...] = (run_id,)
        if coalition is not None:
            query += " AND coalition = ?"
            params = (run_id, coalition.value)
        query += " ORDER BY id"
        with self._connect() as connection:
            batch_rows = connection.execute(query, params).fetchall()
            rows_by_batch = {
                row["id"]: connection.execute(
                    """
                    SELECT batch_id, action_index, action_id, action_type, status, execution_summary, command_count,
                           resource_delta_json, standing_order_delta_json, resulting_entities_json, error_detail,
                           applied_commands_json
                    FROM execution_results
                    WHERE batch_id = ?
                    ORDER BY action_index
                    """,
                    (row["id"],),
                ).fetchall()
                for row in batch_rows
            }
        return tuple(self._row_to_execution_batch_result(row, rows_by_batch[row["id"]]) for row in batch_rows)

    def get_active_execution_standing_orders(
        self,
        run_id: str,
        coalition: Coalition | None = None,
    ) -> tuple[StandingOrderRecord, ...]:
        query = """
            SELECT run_id, order_id, coalition, action_type, entity_type, entity_id, fingerprint,
                   text, active, last_updated_at, metadata_json
            FROM execution_standing_orders
            WHERE run_id = ? AND active = 1
        """
        params: tuple[object, ...] = (run_id,)
        if coalition is not None:
            query += " AND coalition = ?"
            params = (run_id, coalition.value)
        query += " ORDER BY coalition, entity_type, entity_id, order_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_execution_standing_order(row) for row in rows)

    def list_execution_standing_order_history(self, run_id: str) -> tuple[StandingOrderHistoryEntry, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, order_id, coalition, action_type, entity_type, entity_id, transition, occurred_at, metadata_json
                FROM execution_standing_order_history
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return tuple(self._row_to_standing_order_history_entry(row) for row in rows)

    def list_current_execution_notes(
        self,
        run_id: str,
        coalition: Coalition | None = None,
    ) -> tuple[CurrentExecutionNote, ...]:
        query = """
            SELECT run_id, coalition, entity_type, entity_id, decision_cycle, action_id, note, lifecycle_state, updated_at, metadata_json
            FROM current_execution_notes
            WHERE run_id = ?
        """
        params: tuple[object, ...] = (run_id,)
        if coalition is not None:
            query += " AND coalition = ?"
            params = (run_id, coalition.value)
        query += " ORDER BY coalition, entity_type, entity_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_current_execution_note(row) for row in rows)

    def save_evaluation_run_summary(self, summary: EvaluationRunSummary) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_run_summaries (run_id, generated_at, summary_json)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    summary_json = excluded.summary_json
                """,
                (summary.run_id, summary.generated_at.isoformat(), _json(asdict(summary))),
            )
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=summary.run_id,
                    event_type="evaluation_summary_persisted",
                    entity_type="evaluation",
                    entity_id=summary.run_id,
                    occurred_at=summary.generated_at,
                    payload={
                        "fairness_score": summary.fairness_score,
                        "fairness_rating": summary.fairness_rating.value,
                        "suspicious_finding_count": summary.suspicious_finding_count,
                    },
                ),
            )

    def get_evaluation_run_summary(self, run_id: str) -> EvaluationRunSummary:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id, generated_at, summary_json FROM evaluation_run_summaries WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"No evaluation summary found for run '{run_id}'.")
        return self._row_to_evaluation_run_summary(row)

    def save_evaluation_cycle_metrics(
        self,
        run_id: str,
        metrics: tuple[EvaluationCycleMetrics, ...],
        *,
        generated_at: datetime,
    ) -> None:
        with self._connect() as connection:
            for item in metrics:
                connection.execute(
                    """
                    INSERT INTO evaluation_cycle_metrics (run_id, decision_cycle, generated_at, metrics_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(run_id, decision_cycle) DO UPDATE SET
                        generated_at = excluded.generated_at,
                        metrics_json = excluded.metrics_json
                    """,
                    (run_id, item.decision_cycle, generated_at.isoformat(), _json(asdict(item))),
                )

    def list_evaluation_cycle_metrics(self, run_id: str) -> tuple[EvaluationCycleMetrics, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, decision_cycle, generated_at, metrics_json
                FROM evaluation_cycle_metrics
                WHERE run_id = ?
                ORDER BY decision_cycle
                """,
                (run_id,),
            ).fetchall()
        return tuple(self._row_to_evaluation_cycle_metrics(row) for row in rows)

    def replace_fairness_findings(self, run_id: str, findings: tuple[FairnessFinding, ...]) -> tuple[FairnessFinding, ...]:
        persisted: list[FairnessFinding] = []
        with self._connect() as connection:
            connection.execute("DELETE FROM fairness_findings WHERE run_id = ?", (run_id,))
            for finding in findings:
                cursor = connection.execute(
                    """
                    INSERT INTO fairness_findings (
                        run_id, coalition, decision_cycle, finding_type, machine_status, review_status,
                        detail, score_penalty, finding_json, reviewer_note, reviewed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        finding.coalition.value if finding.coalition else None,
                        finding.decision_cycle,
                        finding.finding_type,
                        finding.machine_status.value,
                        finding.review_status.value,
                        finding.detail,
                        finding.score_penalty,
                        _json(finding.finding_data),
                        finding.reviewer_note,
                        finding.reviewed_at.isoformat() if finding.reviewed_at else None,
                    ),
                )
                finding_id = int(cursor.lastrowid)
                persisted.append(
                    FairnessFinding(
                        id=finding_id,
                        run_id=run_id,
                        coalition=finding.coalition,
                        decision_cycle=finding.decision_cycle,
                        finding_type=finding.finding_type,
                        machine_status=finding.machine_status,
                        review_status=finding.review_status,
                        detail=finding.detail,
                        score_penalty=finding.score_penalty,
                        finding_data=finding.finding_data,
                        reviewer_note=finding.reviewer_note,
                        reviewed_at=finding.reviewed_at,
                    )
                )
        return tuple(persisted)

    def list_fairness_findings(self, run_id: str) -> tuple[FairnessFinding, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, finding_type, machine_status, review_status,
                       detail, score_penalty, finding_json, reviewer_note, reviewed_at
                FROM fairness_findings
                WHERE run_id = ?
                ORDER BY decision_cycle, id
                """,
                (run_id,),
            ).fetchall()
        return tuple(self._row_to_fairness_finding(row) for row in rows)

    def list_fairness_review_queue(self) -> tuple[FairnessFinding, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, finding_type, machine_status, review_status,
                       detail, score_penalty, finding_json, reviewer_note, reviewed_at
                FROM fairness_findings
                WHERE review_status = ?
                ORDER BY run_id, decision_cycle, id
                """,
                (FairnessReviewStatus.PENDING_REVIEW.value,),
            ).fetchall()
        return tuple(self._row_to_fairness_finding(row) for row in rows)

    def update_fairness_finding_review(
        self,
        finding_id: int,
        *,
        review_status: FairnessReviewStatus,
        reviewer_note: str | None,
        reviewed_at: datetime,
    ) -> FairnessFinding:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE fairness_findings
                SET review_status = ?, reviewer_note = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (review_status.value, reviewer_note, reviewed_at.isoformat(), finding_id),
            )
            row = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, finding_type, machine_status, review_status,
                       detail, score_penalty, finding_json, reviewer_note, reviewed_at
                FROM fairness_findings
                WHERE id = ?
                """,
                (finding_id,),
            ).fetchone()
            if row is None:
                raise PersistenceError(f"Unknown fairness finding id: {finding_id}")
            finding = self._row_to_fairness_finding(row)
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=finding.run_id,
                    event_type="fairness_finding_reviewed",
                    entity_type="fairness_finding",
                    entity_id=str(finding.id),
                    occurred_at=reviewed_at,
                    payload={
                        "review_status": finding.review_status.value,
                        "reviewer_note": reviewer_note,
                    },
                ),
            )
        return finding

    def save_matrix_case_result(self, result: MatrixCaseResult) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO evaluation_matrix_case_results (
                    profile_name, case_id, description, repeat_index, status, mode, red_backend, blue_backend,
                    decision_cadence_sec, run_cycles, run_id, evaluation_run_id, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.profile_name,
                    result.case_id,
                    result.description,
                    result.repeat_index,
                    result.status.value,
                    result.mode,
                    result.red_backend,
                    result.blue_backend,
                    result.decision_cadence_sec,
                    result.run_cycles,
                    result.run_id,
                    result.evaluation_run_id,
                    result.detail,
                ),
            )
        return int(cursor.lastrowid)

    def list_matrix_case_results(self, profile_name: str) -> tuple[MatrixCaseResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, profile_name, case_id, description, repeat_index, status, mode, red_backend, blue_backend,
                       decision_cadence_sec, run_cycles, run_id, evaluation_run_id, detail
                FROM evaluation_matrix_case_results
                WHERE profile_name = ?
                ORDER BY case_id, repeat_index, id
                """,
                (profile_name,),
            ).fetchall()
        return tuple(self._row_to_matrix_case_result(row) for row in rows)

    def save_matrix_run_report(self, report: MatrixRunReport) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_matrix_reports (profile_name, generated_at, report_json)
                VALUES (?, ?, ?)
                ON CONFLICT(profile_name) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    report_json = excluded.report_json
                """,
                (report.profile_name, report.generated_at.isoformat(), _json(asdict(report))),
            )

    def get_matrix_run_report(self, profile_name: str) -> MatrixRunReport:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_name, generated_at, report_json FROM evaluation_matrix_reports WHERE profile_name = ?",
                (profile_name,),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"No matrix report found for profile '{profile_name}'.")
        return self._row_to_matrix_run_report(row)

    def save_replay_export_result(self, result: ReplayExportResult) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO replay_exports (
                    run_id, output_dir, manifest_path, file_count, exported_at, file_inventory_json,
                    includes_evaluation_summary, includes_cycle_evaluations, includes_fairness_findings, includes_matrix_report
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.output_dir,
                    result.manifest_path,
                    result.file_count,
                    (result.exported_at or datetime.now(UTC)).isoformat(),
                    _json(list(result.file_inventory)),
                    int(result.includes_evaluation_summary),
                    int(result.includes_cycle_evaluations),
                    int(result.includes_fairness_findings),
                    int(result.includes_matrix_report),
                ),
            )
        return int(cursor.lastrowid)

    def list_replay_export_results(self, run_id: str) -> tuple[ReplayExportResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, output_dir, manifest_path, file_count, exported_at, file_inventory_json,
                       includes_evaluation_summary, includes_cycle_evaluations, includes_fairness_findings, includes_matrix_report
                FROM replay_exports
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return tuple(self._row_to_replay_export_result(row) for row in rows)

    def apply_execution_state_updates(
        self,
        run_id: str,
        coalition: Coalition,
        action_id: str,
        action_type: ActionType,
        status: ExecutionStatus,
        *,
        state_updates: dict[str, Any],
        standing_orders: tuple[StandingOrderRecord, ...],
        executed_at: datetime,
        summary: str,
        decision_cycle: int | None = None,
    ) -> None:
        if status not in {ExecutionStatus.SUCCEEDED, ExecutionStatus.PARTIALLY_SUCCEEDED, ExecutionStatus.NO_CHANGE}:
            return
        with self._connect() as connection:
            budget_delta = int(state_updates.get("budget_delta", 0))
            if budget_delta != 0:
                connection.execute(
                    """
                    UPDATE coalition_state
                    SET budget_remaining = budget_remaining + ?
                    WHERE run_id = ? AND coalition = ?
                    """,
                    (budget_delta, run_id, coalition.value),
                )

            for reserve_update in state_updates.get("reserve_groups", ()):
                connection.execute(
                    """
                    UPDATE reserve_groups
                    SET available = ?, status = ?
                    WHERE run_id = ? AND reserve_group_id = ?
                    """,
                    (
                        int(bool(reserve_update.get("available", False))),
                        reserve_update.get("status", "committed"),
                        run_id,
                        reserve_update["reserve_group_id"],
                    ),
                )

            for group_update in state_updates.get("active_groups", ()):
                self._upsert_active_group_state(connection, run_id, ActiveGroupState(**group_update))

            for world_group_update in state_updates.get("world_groups", ()):
                payload = dict(world_group_update)
                if payload.get("coalition") is not None and not isinstance(payload["coalition"], Coalition):
                    payload["coalition"] = Coalition(payload["coalition"])
                self._upsert_world_group(connection, run_id, WorldGroupState(**payload))

            for standing_order in standing_orders:
                self._upsert_execution_standing_order(connection, standing_order)

            note_targets = set()
            for group_update in state_updates.get("active_groups", ()):
                if group_update.get("id"):
                    note_targets.add(("group", group_update["id"]))
            for order in standing_orders:
                if order.entity_id:
                    note_targets.add((order.entity_type, order.entity_id))
            for entity_type, entity_id in sorted(note_targets):
                connection.execute(
                    """
                    INSERT INTO current_execution_notes (
                        run_id, coalition, entity_type, entity_id, decision_cycle, action_id, note,
                        lifecycle_state, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, coalition, entity_type, entity_id) DO UPDATE SET
                        decision_cycle = excluded.decision_cycle,
                        action_id = excluded.action_id,
                        note = excluded.note,
                        lifecycle_state = excluded.lifecycle_state,
                        updated_at = excluded.updated_at,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        run_id,
                        coalition.value,
                        entity_type,
                        entity_id,
                        decision_cycle or 0,
                        action_id,
                        summary,
                        ExecutionLifecycleState.COMPLETED.value,
                        executed_at.isoformat(),
                        _json({"action_type": action_type.value, "status": status.value}),
                    ),
                )

            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=run_id,
                    event_type="execution_state_applied",
                    entity_type="execution",
                    entity_id=action_id,
                    occurred_at=executed_at,
                    payload={
                        "action_type": action_type.value,
                        "status": status.value,
                        "summary": summary,
                        "budget_delta": budget_delta,
                        "standing_order_count": len(standing_orders),
                    },
                ),
            )

    def save_decision_cycle_result(self, result: DecisionCycleResult) -> int:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO decision_cycles (
                    run_id, decision_cycle, snapshot_time, red_observation_id, blue_observation_id,
                    red_invocation_id, blue_invocation_id, red_execution_batch_id, blue_execution_batch_id,
                    classification, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, decision_cycle) DO UPDATE SET
                    snapshot_time = excluded.snapshot_time,
                    red_observation_id = excluded.red_observation_id,
                    blue_observation_id = excluded.blue_observation_id,
                    red_invocation_id = excluded.red_invocation_id,
                    blue_invocation_id = excluded.blue_invocation_id,
                    red_execution_batch_id = excluded.red_execution_batch_id,
                    blue_execution_batch_id = excluded.blue_execution_batch_id,
                    classification = excluded.classification,
                    summary_json = excluded.summary_json
                """,
                (
                    result.run_id,
                    result.decision_cycle,
                    result.snapshot_time.isoformat(),
                    result.red_observation_id,
                    result.blue_observation_id,
                    result.red_invocation_id,
                    result.blue_invocation_id,
                    result.red_execution_batch_id,
                    result.blue_execution_batch_id,
                    result.classification,
                    _json(result.summary),
                ),
            )
            row = connection.execute(
                """
                SELECT id FROM decision_cycles
                WHERE run_id = ? AND decision_cycle = ?
                """,
                (result.run_id, result.decision_cycle),
            ).fetchone()
            cycle_id = int(row["id"]) if row is not None else 0
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=result.run_id,
                    event_type="decision_cycle_persisted",
                    entity_type="decision_cycle",
                    entity_id=str(cycle_id),
                    occurred_at=result.snapshot_time,
                    payload={
                        "decision_cycle": result.decision_cycle,
                        "red_invocation_id": result.red_invocation_id,
                        "blue_invocation_id": result.blue_invocation_id,
                    },
                ),
            )
        return cycle_id

    def list_decision_cycles(self, run_id: str) -> tuple[DecisionCycleResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, decision_cycle, snapshot_time, red_observation_id, blue_observation_id,
                       red_invocation_id, blue_invocation_id, red_execution_batch_id, blue_execution_batch_id,
                       classification, summary_json
                FROM decision_cycles
                WHERE run_id = ?
                ORDER BY decision_cycle, id
                """,
                (run_id,),
            ).fetchall()
        return tuple(self._row_to_decision_cycle_result(row) for row in rows)

    def save_action_validation_batch(self, batch: ActionValidationBatch) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO action_validation_batches (
                    run_id, coalition, decision_cycle, submitted_at, raw_payload_json,
                    non_hold_action_count, soft_cap_exceeded, batch_messages_json, latest_observation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.run_id,
                    batch.coalition.value,
                    batch.decision_cycle,
                    batch.submitted_at.isoformat(),
                    _json(batch.raw_payload),
                    batch.non_hold_action_count,
                    int(batch.soft_cap_exceeded),
                    _json([asdict(message) for message in batch.batch_messages]),
                    batch.latest_observation_id,
                ),
            )
            batch_id = int(cursor.lastrowid)
            for index, result in enumerate(batch.results):
                connection.execute(
                    """
                    INSERT INTO action_validation_results (
                        batch_id, action_index, action_id, action_type, status, rejection_code, message,
                        normalized_params_json, messages_json, validated_stages_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        index,
                        result.action_id,
                        result.action_type.value if result.action_type else None,
                        result.status.value,
                        result.rejection_code.value if result.rejection_code else None,
                        result.message,
                        _json(result.normalized_params),
                        _json([asdict(message) for message in result.messages]),
                        _json(result.validated_stages),
                    ),
                )
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=batch.run_id,
                    event_type="action_validation_persisted",
                    entity_type="action_validation_batch",
                    entity_id=str(batch_id),
                    occurred_at=batch.submitted_at,
                    payload={
                        "coalition": batch.coalition.value,
                        "decision_cycle": batch.decision_cycle,
                        "result_count": len(batch.results),
                        "soft_cap_exceeded": batch.soft_cap_exceeded,
                    },
                ),
            )
        return batch_id

    def get_latest_action_validation_batch(
        self,
        run_id: str,
        coalition: Coalition,
    ) -> ActionValidationBatch:
        with self._connect() as connection:
            batch_row = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, submitted_at, raw_payload_json,
                       non_hold_action_count, soft_cap_exceeded, batch_messages_json, latest_observation_id
                FROM action_validation_batches
                WHERE run_id = ? AND coalition = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, coalition.value),
            ).fetchone()
            if batch_row is None:
                raise PersistenceError(
                    f"No action validation batches found for coalition '{coalition.value}' in run '{run_id}'."
                )
            result_rows = connection.execute(
                """
                SELECT batch_id, action_index, action_id, action_type, status, rejection_code, message,
                       normalized_params_json, messages_json, validated_stages_json
                FROM action_validation_results
                WHERE batch_id = ?
                ORDER BY action_index
                """,
                (batch_row["id"],),
            ).fetchall()
        return self._row_to_action_validation_batch(batch_row, result_rows)

    def list_action_validation_batches(
        self,
        run_id: str,
        coalition: Coalition | None = None,
    ) -> tuple[ActionValidationBatch, ...]:
        query = """
            SELECT id, run_id, coalition, decision_cycle, submitted_at, raw_payload_json,
                   non_hold_action_count, soft_cap_exceeded, batch_messages_json, latest_observation_id
            FROM action_validation_batches
            WHERE run_id = ?
        """
        params: tuple[object, ...] = (run_id,)
        if coalition is not None:
            query += " AND coalition = ?"
            params = (run_id, coalition.value)
        query += " ORDER BY id"
        with self._connect() as connection:
            batch_rows = connection.execute(query, params).fetchall()
            if not batch_rows:
                return ()
            batch_ids = tuple(int(row["id"]) for row in batch_rows)
            placeholders = ",".join("?" for _ in batch_ids)
            result_rows = connection.execute(
                f"""
                SELECT batch_id, action_index, action_id, action_type, status, rejection_code, message,
                       normalized_params_json, messages_json, validated_stages_json
                FROM action_validation_results
                WHERE batch_id IN ({placeholders})
                ORDER BY batch_id, action_index
                """,
                batch_ids,
            ).fetchall()
        grouped_results: dict[int, list[sqlite3.Row]] = {}
        for row in result_rows:
            grouped_results.setdefault(int(row["batch_id"]), []).append(row)
        return tuple(
            self._row_to_action_validation_batch(row, grouped_results.get(int(row["id"]), [])) for row in batch_rows
        )

    def save_world_state_snapshot(
        self,
        run_id: str,
        scenario_id: str,
        theater: str,
        mission_time: str | None,
        last_ingest_at: datetime,
        groups: tuple[WorldGroupState, ...],
        control_points: tuple[WorldControlPointState, ...],
        evidence_records: tuple[EvidenceRecord, ...],
    ) -> None:
        with self._connect() as connection:
            self._upsert_world_state_snapshot(connection, run_id, scenario_id, theater, mission_time, last_ingest_at)
            for group in groups:
                self._upsert_world_group(connection, run_id, group)
            for control_point in control_points:
                self._upsert_world_control_point(connection, run_id, control_point)
            for evidence_record in evidence_records:
                self._insert_evidence_record(connection, evidence_record)
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=run_id,
                    event_type="world_state_updated",
                    entity_type="world_state",
                    entity_id=run_id,
                    occurred_at=last_ingest_at,
                    payload={
                        "mission_time": mission_time,
                        "group_count": len(groups),
                        "evidence_count": len(evidence_records),
                    },
                ),
            )

    def get_world_state_snapshot(self, run_id: str | None = None) -> WorldStateSnapshot:
        resolved_run_id = run_id or self.get_latest_run_id()
        with self._connect() as connection:
            snapshot_row = connection.execute(
                """
                SELECT run_id, scenario_id, theater, mission_time, last_ingest_at
                FROM world_state_snapshots WHERE run_id = ?
                """,
                (resolved_run_id,),
            ).fetchone()
            if snapshot_row is None:
                raise PersistenceError(f"No world state snapshot found for run id: {resolved_run_id}")
            group_rows = connection.execute(
                """
                SELECT group_id, source_id, coalition, group_type, category, name, sector_id, control_point_id,
                       lat, lng, alt, heading, speed, health, source, last_updated_at, high_value, mobile
                FROM world_groups WHERE run_id = ? ORDER BY group_id
                """,
                (resolved_run_id,),
            ).fetchall()
            control_rows = connection.execute(
                """
                SELECT control_point_id, owner, sector_id, last_updated_at, source
                FROM world_control_points WHERE run_id = ? ORDER BY control_point_id
                """,
                (resolved_run_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT id, run_id, source, evidence_type, entity_id, coalition, sector_id, occurred_at, summary, payload_json
                FROM evidence_records WHERE run_id = ? ORDER BY id
                """,
                (resolved_run_id,),
            ).fetchall()
        return WorldStateSnapshot(
            run_id=resolved_run_id,
            scenario_id=snapshot_row["scenario_id"],
            theater=snapshot_row["theater"],
            mission_time=snapshot_row["mission_time"],
            last_ingest_at=_parse_dt(snapshot_row["last_ingest_at"]),
            groups=tuple(self._row_to_world_group(row) for row in group_rows),
            control_points=tuple(self._row_to_world_control_point(row) for row in control_rows),
            evidence_records=tuple(self._row_to_evidence_record(row) for row in evidence_rows),
        )

    def list_evidence_records(self, run_id: str | None = None) -> tuple[EvidenceRecord, ...]:
        return self.get_world_state_snapshot(run_id).evidence_records

    def save_fusion_update(self, run_id: str, result: FusionUpdateResult) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO fusion_updates (
                    run_id, generated_at, policy_json, active_track_count, archived_track_count, change_summary_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.generated_at.isoformat(),
                    _json(asdict(result.policy)),
                    result.active_track_count,
                    result.archived_track_count,
                    _json(result.change_summary),
                ),
            )
            fusion_update_id = int(cursor.lastrowid)
            for state in result.knowledge_states:
                connection.execute(
                    """
                    INSERT INTO knowledge_snapshots (
                        run_id, fusion_update_id, coalition, generated_at, mission_time, known_friendly_group_ids_json, sector_activity_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        fusion_update_id,
                        state.coalition.value,
                        state.generated_at.isoformat(),
                        state.mission_time,
                        _json(state.known_friendly_group_ids),
                        _json(state.sector_activity),
                    ),
                )
                for track in (*state.contact_tracks, *state.archived_tracks):
                    self._upsert_contact_track(connection, run_id, track)
            self.append_event(
                connection,
                EventRecord(
                    id=None,
                    run_id=run_id,
                    event_type="fusion_updated",
                    entity_type="sensor_fusion",
                    entity_id=run_id,
                    occurred_at=result.generated_at,
                    payload={
                        "active_track_count": result.active_track_count,
                        "archived_track_count": result.archived_track_count,
                        "change_summary": result.change_summary,
                        "fusion_update_id": fusion_update_id,
                    },
                ),
            )
        return fusion_update_id

    def get_knowledge_state(
        self,
        coalition: Coalition,
        run_id: str | None = None,
    ) -> CoalitionKnowledgeState:
        resolved_run_id = run_id or self.get_latest_run_id()
        with self._connect() as connection:
            snapshot_row = connection.execute(
                """
                SELECT coalition, generated_at, mission_time, known_friendly_group_ids_json, sector_activity_json
                FROM knowledge_snapshots
                WHERE run_id = ? AND coalition = ?
                ORDER BY id DESC LIMIT 1
                """,
                (resolved_run_id, coalition.value),
            ).fetchone()
            if snapshot_row is None:
                raise PersistenceError(f"No knowledge snapshot found for coalition '{coalition.value}'.")
            track_rows = connection.execute(
                """
                SELECT coalition, track_id, knowledge_level, confidence, classification, inferred, stale, archived,
                       first_seen_at, last_confirmed_at, last_known_sector_id, estimated_sector_id, evidence_count,
                       sources_json, last_source, seconds_since_last_confirmation
                FROM contact_tracks
                WHERE run_id = ? AND coalition = ?
                ORDER BY archived, track_id
                """,
                (resolved_run_id, coalition.value),
            ).fetchall()
        tracks = tuple(self._row_to_contact_track(row) for row in track_rows)
        return CoalitionKnowledgeState(
            coalition=coalition,
            generated_at=datetime.fromisoformat(snapshot_row["generated_at"]),
            mission_time=snapshot_row["mission_time"],
            contact_tracks=tuple(track for track in tracks if not track.archived),
            archived_tracks=tuple(track for track in tracks if track.archived),
            known_friendly_group_ids=tuple(json.loads(snapshot_row["known_friendly_group_ids_json"])),
            sector_activity=json.loads(snapshot_row["sector_activity_json"]),
        )

    def get_latest_fusion_update_id(self, run_id: str | None = None) -> int:
        resolved_run_id = run_id or self.get_latest_run_id()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM fusion_updates
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (resolved_run_id,),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"No fusion updates found for run '{resolved_run_id}'.")
        return int(row["id"])

    def list_fusion_snapshot_records(
        self,
        run_id: str,
        coalition: Coalition | None = None,
    ) -> tuple[FusionSnapshotRecord, ...]:
        query = """
            SELECT id, run_id, fusion_update_id, coalition, generated_at, mission_time, known_friendly_group_ids_json, sector_activity_json
            FROM knowledge_snapshots
            WHERE run_id = ?
        """
        params: tuple[object, ...] = (run_id,)
        if coalition is not None:
            query += " AND coalition = ?"
            params = (run_id, coalition.value)
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_fusion_snapshot_record(row) for row in rows)

    def get_contact_track_debug(
        self,
        coalition: Coalition,
        track_id: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or self.get_latest_run_id()
        track = self._get_contact_track_row(resolved_run_id, coalition, track_id)
        if track is None:
            raise PersistenceError(f"Unknown contact track '{track_id}' for coalition '{coalition.value}'.")
        with self._connect() as connection:
            evidence_rows = connection.execute(
                """
                SELECT id, run_id, source, evidence_type, entity_id, coalition, sector_id, occurred_at, summary, payload_json
                FROM evidence_records
                WHERE run_id = ? AND entity_id = ?
                ORDER BY occurred_at DESC, id DESC
                """,
                (resolved_run_id, track_id),
            ).fetchall()
        return {
            "track": asdict(self._row_to_contact_track(track)),
            "evidence": [asdict(self._row_to_evidence_record(row)) for row in evidence_rows],
        }

    def _get_contact_track_row(
        self,
        run_id: str,
        coalition: Coalition,
        track_id: str,
    ) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT coalition, track_id, knowledge_level, confidence, classification, inferred, stale, archived,
                       first_seen_at, last_confirmed_at, last_known_sector_id, estimated_sector_id, evidence_count,
                       sources_json, last_source, seconds_since_last_confirmation
                FROM contact_tracks
                WHERE run_id = ? AND coalition = ? AND track_id = ?
                """,
                (run_id, coalition.value, track_id),
            ).fetchone()

    def _insert_sector(self, connection: sqlite3.Connection, run_id: str, sector: SectorState) -> None:
        connection.execute(
            """
            INSERT INTO sectors (
                run_id, sector_id, name, role, neighbor_ids_json, tags_json, center_lat, center_lng, radius_nm
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sector.id,
                sector.name,
                sector.role,
                _json(sector.neighbor_ids),
                _json(sector.tags),
                sector.center_lat,
                sector.center_lng,
                sector.radius_nm,
            ),
        )

    def _insert_control_point(self, connection: sqlite3.Connection, run_id: str, control_point: ControlPointState) -> None:
        connection.execute(
            """
            INSERT INTO control_points (
                run_id, control_point_id, name, sector_id, kind, owner, strategic_value, lat, lng
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                control_point.id,
                control_point.name,
                control_point.sector_id,
                control_point.kind,
                control_point.owner.value if control_point.owner else None,
                control_point.strategic_value,
                control_point.lat,
                control_point.lng,
            ),
        )

    def _insert_zone(self, connection: sqlite3.Connection, run_id: str, zone: ScenarioZone) -> None:
        connection.execute(
            """
            INSERT INTO scenario_zones (
                run_id, zone_id, name, sector_id, center_lat, center_lng, radius_nm, tags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                zone.id,
                zone.name,
                zone.sector_id,
                zone.center_lat,
                zone.center_lng,
                zone.radius_nm,
                _json(zone.tags),
            ),
        )

    def _insert_coalition_state(self, connection: sqlite3.Connection, run_id: str, coalition_state: CoalitionState) -> None:
        connection.execute(
            """
            INSERT INTO coalition_state (
                run_id, coalition, budget_remaining, objectives_json, attrition_pool, replacement_pool
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                coalition_state.coalition.value,
                coalition_state.budget_remaining,
                _json(coalition_state.objectives),
                coalition_state.attrition_pool,
                coalition_state.replacement_pool,
            ),
        )

    def _insert_standing_order(self, connection: sqlite3.Connection, run_id: str, standing_order: StandingOrderState) -> None:
        connection.execute(
            """
            INSERT INTO standing_orders (run_id, order_id, coalition, text, active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                standing_order.id,
                standing_order.coalition.value,
                standing_order.text,
                int(standing_order.active),
            ),
        )

    def _upsert_active_group_state(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        group: ActiveGroupState,
    ) -> None:
        connection.execute(
            """
            INSERT INTO active_groups (
                run_id, group_id, coalition, group_type, posture, sector_id, mobile, status,
                control_point_id, attrition_count, replacement_pool
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, group_id) DO UPDATE SET
                coalition = excluded.coalition,
                group_type = excluded.group_type,
                posture = excluded.posture,
                sector_id = excluded.sector_id,
                mobile = excluded.mobile,
                status = excluded.status,
                control_point_id = excluded.control_point_id,
                attrition_count = excluded.attrition_count,
                replacement_pool = excluded.replacement_pool
            """,
            (
                run_id,
                group.id,
                group.coalition.value,
                group.group_type,
                group.posture.value,
                group.sector_id,
                int(group.mobile),
                group.status,
                group.control_point_id,
                group.attrition_count,
                group.replacement_pool,
            ),
        )

    def _upsert_execution_standing_order(self, connection: sqlite3.Connection, standing_order: StandingOrderRecord) -> None:
        superseded_rows = connection.execute(
            """
            SELECT order_id, metadata_json
            FROM execution_standing_orders
            WHERE run_id = ? AND coalition = ? AND action_type = ? AND entity_type = ?
              AND COALESCE(entity_id, '') = COALESCE(?, '') AND order_id != ? AND active = 1
            """,
            (
                standing_order.run_id,
                standing_order.coalition.value,
                standing_order.action_type.value,
                standing_order.entity_type,
                standing_order.entity_id,
                standing_order.id,
            ),
        ).fetchall()
        connection.execute(
            """
            UPDATE execution_standing_orders
            SET active = 0
            WHERE run_id = ? AND coalition = ? AND action_type = ? AND entity_type = ?
              AND COALESCE(entity_id, '') = COALESCE(?, '') AND order_id != ?
            """,
            (
                standing_order.run_id,
                standing_order.coalition.value,
                standing_order.action_type.value,
                standing_order.entity_type,
                standing_order.entity_id,
                standing_order.id,
            ),
        )
        connection.execute(
            """
            INSERT INTO execution_standing_orders (
                run_id, order_id, coalition, action_type, entity_type, entity_id, fingerprint,
                text, active, last_updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, order_id) DO UPDATE SET
                fingerprint = excluded.fingerprint,
                text = excluded.text,
                active = excluded.active,
                last_updated_at = excluded.last_updated_at,
                metadata_json = excluded.metadata_json
            """,
            (
                standing_order.run_id,
                standing_order.id,
                standing_order.coalition.value,
                standing_order.action_type.value,
                standing_order.entity_type,
                standing_order.entity_id,
                standing_order.fingerprint,
                standing_order.text,
                int(standing_order.active),
                standing_order.last_updated_at.isoformat() if standing_order.last_updated_at else None,
                _json(standing_order.metadata),
            ),
        )
        connection.execute(
            """
            INSERT INTO standing_orders (run_id, order_id, coalition, text, active)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, order_id) DO UPDATE SET
                text = excluded.text,
                active = excluded.active
            """,
            (
                standing_order.run_id,
                standing_order.id,
                standing_order.coalition.value,
                standing_order.text,
                int(standing_order.active),
            ),
        )
        for row in superseded_rows:
            connection.execute(
                """
                INSERT INTO execution_standing_order_history (
                    run_id, order_id, coalition, action_type, entity_type, entity_id, transition, occurred_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    standing_order.run_id,
                    row["order_id"],
                    standing_order.coalition.value,
                    standing_order.action_type.value,
                    standing_order.entity_type,
                    standing_order.entity_id,
                    "superseded",
                    standing_order.last_updated_at.isoformat() if standing_order.last_updated_at else datetime.now(UTC).isoformat(),
                    row["metadata_json"],
                ),
            )
        connection.execute(
            """
            INSERT INTO execution_standing_order_history (
                run_id, order_id, coalition, action_type, entity_type, entity_id, transition, occurred_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                standing_order.run_id,
                standing_order.id,
                standing_order.coalition.value,
                standing_order.action_type.value,
                standing_order.entity_type,
                standing_order.entity_id,
                "activated" if standing_order.active else "updated",
                standing_order.last_updated_at.isoformat() if standing_order.last_updated_at else datetime.now(UTC).isoformat(),
                _json(standing_order.metadata),
            ),
        )

    def _insert_restriction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        restriction: DeploymentRestrictionState,
    ) -> None:
        connection.execute(
            """
            INSERT INTO deployment_restrictions (
                run_id, restriction_id, coalition, restriction_type, description,
                sector_ids_json, control_point_ids_json, adjacency_limited
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                restriction.id,
                restriction.coalition.value if restriction.coalition else None,
                restriction.restriction_type,
                restriction.description,
                _json(restriction.sector_ids),
                _json(restriction.control_point_ids),
                int(restriction.adjacency_limited),
            ),
        )

    def _upsert_world_state_snapshot(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        scenario_id: str,
        theater: str,
        mission_time: str | None,
        last_ingest_at: datetime | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO world_state_snapshots (run_id, scenario_id, theater, mission_time, last_ingest_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                mission_time = excluded.mission_time,
                last_ingest_at = excluded.last_ingest_at
            """,
            (
                run_id,
                scenario_id,
                theater,
                mission_time,
                last_ingest_at.isoformat() if last_ingest_at else None,
            ),
        )

    def _upsert_world_group(self, connection: sqlite3.Connection, run_id: str, group: WorldGroupState) -> None:
        connection.execute(
            """
            INSERT INTO world_groups (
                run_id, group_id, source_id, coalition, group_type, category, name, sector_id, control_point_id,
                lat, lng, alt, heading, speed, health, source, last_updated_at, high_value, mobile
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, group_id) DO UPDATE SET
                source_id = excluded.source_id,
                coalition = excluded.coalition,
                group_type = excluded.group_type,
                category = excluded.category,
                name = excluded.name,
                sector_id = excluded.sector_id,
                control_point_id = excluded.control_point_id,
                lat = excluded.lat,
                lng = excluded.lng,
                alt = excluded.alt,
                heading = excluded.heading,
                speed = excluded.speed,
                health = excluded.health,
                source = excluded.source,
                last_updated_at = excluded.last_updated_at,
                high_value = excluded.high_value,
                mobile = excluded.mobile
            """,
            (
                run_id,
                group.id,
                group.source_id,
                group.coalition.value if group.coalition else None,
                group.group_type,
                group.category,
                group.name,
                group.sector_id,
                group.control_point_id,
                group.lat,
                group.lng,
                group.alt,
                group.heading,
                group.speed,
                group.health,
                group.source,
                group.last_updated_at.isoformat() if group.last_updated_at else None,
                int(group.high_value),
                int(group.mobile),
            ),
        )

    def _upsert_world_control_point(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        control_point: WorldControlPointState,
    ) -> None:
        connection.execute(
            """
            INSERT INTO world_control_points (run_id, control_point_id, owner, sector_id, last_updated_at, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, control_point_id) DO UPDATE SET
                owner = excluded.owner,
                sector_id = excluded.sector_id,
                last_updated_at = excluded.last_updated_at,
                source = excluded.source
            """,
            (
                run_id,
                control_point.control_point_id,
                control_point.owner.value if control_point.owner else None,
                control_point.sector_id,
                control_point.last_updated_at.isoformat() if control_point.last_updated_at else None,
                control_point.source,
            ),
        )

    def _insert_evidence_record(self, connection: sqlite3.Connection, evidence_record: EvidenceRecord) -> None:
        connection.execute(
            """
            INSERT INTO evidence_records (
                run_id, source, evidence_type, entity_id, coalition, sector_id, occurred_at, summary, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_record.run_id,
                evidence_record.source,
                evidence_record.evidence_type,
                evidence_record.entity_id,
                evidence_record.coalition.value if evidence_record.coalition else None,
                evidence_record.sector_id,
                evidence_record.occurred_at.isoformat(),
                evidence_record.summary,
                _json(evidence_record.payload),
            ),
        )

    def _upsert_contact_track(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        track: CoalitionContactTrack,
    ) -> None:
        sources = track.source_summary.sources if track.source_summary else ()
        last_source = track.source_summary.last_source if track.source_summary else None
        evidence_count = track.source_summary.evidence_count if track.source_summary else 0
        connection.execute(
            """
            INSERT INTO contact_tracks (
                run_id, coalition, track_id, knowledge_level, confidence, classification, inferred, stale, archived,
                first_seen_at, last_confirmed_at, last_known_sector_id, estimated_sector_id, evidence_count,
                sources_json, last_source, seconds_since_last_confirmation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, coalition, track_id) DO UPDATE SET
                knowledge_level = excluded.knowledge_level,
                confidence = excluded.confidence,
                classification = excluded.classification,
                inferred = excluded.inferred,
                stale = excluded.stale,
                archived = excluded.archived,
                first_seen_at = excluded.first_seen_at,
                last_confirmed_at = excluded.last_confirmed_at,
                last_known_sector_id = excluded.last_known_sector_id,
                estimated_sector_id = excluded.estimated_sector_id,
                evidence_count = excluded.evidence_count,
                sources_json = excluded.sources_json,
                last_source = excluded.last_source,
                seconds_since_last_confirmation = excluded.seconds_since_last_confirmation
            """,
            (
                run_id,
                track.coalition.value,
                track.track_id,
                track.knowledge_level.value,
                track.confidence.value,
                track.classification,
                int(track.inferred),
                int(track.stale),
                int(track.archived),
                track.first_seen_at.isoformat() if track.first_seen_at else None,
                track.last_confirmed_at.isoformat() if track.last_confirmed_at else None,
                track.last_known_sector_id,
                track.estimated_sector_id,
                evidence_count,
                _json(sources),
                last_source,
                track.seconds_since_last_confirmation,
            ),
        )

    def _row_to_world_group(self, row: sqlite3.Row) -> WorldGroupState:
        return WorldGroupState(
            id=row["group_id"],
            source_id=row["source_id"],
            coalition=Coalition(row["coalition"]) if row["coalition"] else None,
            group_type=row["group_type"],
            category=row["category"],
            name=row["name"],
            sector_id=row["sector_id"],
            control_point_id=row["control_point_id"],
            lat=row["lat"],
            lng=row["lng"],
            alt=row["alt"],
            heading=row["heading"],
            speed=row["speed"],
            health=row["health"],
            source=row["source"],
            last_updated_at=_parse_dt(row["last_updated_at"]),
            high_value=bool(row["high_value"]),
            mobile=bool(row["mobile"]),
        )

    def _row_to_world_control_point(self, row: sqlite3.Row) -> WorldControlPointState:
        return WorldControlPointState(
            control_point_id=row["control_point_id"],
            owner=Coalition(row["owner"]) if row["owner"] else None,
            sector_id=row["sector_id"],
            last_updated_at=_parse_dt(row["last_updated_at"]),
            source=row["source"],
        )

    def _row_to_evidence_record(self, row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            id=row["id"],
            run_id=row["run_id"],
            source=row["source"],
            evidence_type=row["evidence_type"],
            entity_id=row["entity_id"],
            coalition=Coalition(row["coalition"]) if row["coalition"] else None,
            sector_id=row["sector_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            summary=row["summary"],
            payload=json.loads(row["payload_json"]),
        )

    def _row_to_contact_track(self, row: sqlite3.Row) -> CoalitionContactTrack:
        summary = TrackEvidenceSummary(
            evidence_count=row["evidence_count"],
            sources=tuple(json.loads(row["sources_json"])),
            last_source=row["last_source"],
            last_confirmed_at=_parse_dt(row["last_confirmed_at"]),
        )
        return CoalitionContactTrack(
            track_id=row["track_id"],
            coalition=Coalition(row["coalition"]),
            knowledge_level=KnowledgeLevel(row["knowledge_level"]),
            confidence=ConfidenceBand(row["confidence"]),
            classification=row["classification"],
            inferred=bool(row["inferred"]),
            stale=bool(row["stale"]),
            archived=bool(row["archived"]),
            first_seen_at=_parse_dt(row["first_seen_at"]),
            last_confirmed_at=_parse_dt(row["last_confirmed_at"]),
            last_known_sector_id=row["last_known_sector_id"],
            estimated_sector_id=row["estimated_sector_id"],
            source_summary=summary,
            seconds_since_last_confirmation=row["seconds_since_last_confirmation"],
        )

    def _row_to_observation_artifact(self, row: sqlite3.Row) -> ObservationArtifact:
        payload = json.loads(row["payload_json"])
        with self._connect() as connection:
            attachment_rows = connection.execute(
                """
                SELECT attachment_id, run_id, coalition, decision_cycle, media_type, role, file_path,
                       submitted_to_backend, metadata_json
                FROM observation_attachment_artifacts
                WHERE observation_id = ?
                ORDER BY attachment_id
                """,
                (row["id"],),
            ).fetchall()
        observation = CommanderObservation(
            meta=ObservationMeta(
                schema_version=payload["meta"]["schema_version"],
                coalition=Coalition(payload["meta"]["coalition"]),
                snapshot_time=datetime.fromisoformat(payload["meta"]["snapshot_time"]),
                decision_cycle=payload["meta"]["decision_cycle"],
                seconds_since_last_cycle=payload["meta"]["seconds_since_last_cycle"],
                picture_quality=payload["meta"]["picture_quality"],
            ),
            commander_state=CommanderStateView(
                coalition=Coalition(payload["commander_state"]["coalition"]),
                posture=payload["commander_state"].get("posture"),
                objectives=tuple(payload["commander_state"]["objectives"]),
                constraints=tuple(payload["commander_state"].get("constraints", ())),
                operator_instructions=tuple(payload["commander_state"].get("operator_instructions", ())),
            ),
            scenario_state=ScenarioStateView(
                theater=payload["scenario_state"]["theater"],
                mission_clock=payload["scenario_state"].get("mission_clock"),
                weather_summary=payload["scenario_state"].get("weather_summary"),
                known_control_points=tuple(
                    KnownControlPointView(
                        id=item["id"],
                        name=item["name"],
                        kind=item["kind"],
                        sector_id=item["sector_id"],
                        status=item["status"],
                    )
                    for item in payload["scenario_state"]["known_control_points"]
                ),
                known_sectors=tuple(
                    KnownSectorView(
                        id=item["id"],
                        name=item["name"],
                        role=item["role"],
                        status=item["status"],
                    )
                    for item in payload["scenario_state"]["known_sectors"]
                ),
                front_status=tuple(payload["scenario_state"].get("front_status", ())),
            ),
            resource_state=ResourceStateView(
                deployment_budget_remaining=payload["resource_state"]["deployment_budget_remaining"],
                available_reserves=tuple(
                    ReserveAvailabilityView(
                        id=item["id"],
                        group_type=item["group_type"],
                        count=item["count"],
                        allowed_sectors=tuple(item.get("allowed_sectors", ())),
                    )
                    for item in payload["resource_state"]["available_reserves"]
                ),
                recently_lost_assets=tuple(payload["resource_state"].get("recently_lost_assets", ())),
                restrictions=tuple(payload["resource_state"].get("restrictions", ())),
                key_shortages=tuple(payload["resource_state"].get("key_shortages", ())),
            ),
            sector_summary=tuple(
                SectorSummaryEntry(
                    sector_id=item["sector_id"],
                    sector_label=item["sector_label"],
                    control_status=item["control_status"],
                    friendly_strength=item["friendly_strength"],
                    enemy_threat=item["enemy_threat"],
                    contact_confidence=item["contact_confidence"],
                    activity_level=item["activity_level"],
                    strategic_importance=item["strategic_importance"],
                    recent_notes=tuple(item.get("recent_notes", ())),
                )
                for item in payload["sector_summary"]
            ),
            friendly_forces=tuple(
                FriendlyForceEntry(
                    force_id=item["force_id"],
                    force_type=item["force_type"],
                    assigned_sector=item.get("assigned_sector"),
                    readiness=item["readiness"],
                    task=item["task"],
                    mobility=item["mobility"],
                    health_band=item["health_band"],
                    high_value=bool(item.get("high_value", False)),
                )
                for item in payload["friendly_forces"]
            ),
            enemy_contacts=tuple(
                EnemyContactEntry(
                    contact_id=item["contact_id"],
                    knowledge_level=KnowledgeLevel(item["knowledge_level"]),
                    classification=item.get("classification"),
                    last_known_sector=item.get("last_known_sector"),
                    estimated_sector=item.get("estimated_sector"),
                    time_since_last_confirmation_sec=item["time_since_last_confirmation_sec"],
                    confidence=ConfidenceBand(item["confidence"]),
                    detection_sources=tuple(item.get("detection_sources", ())),
                    threat_estimate=item["threat_estimate"],
                    stale=bool(item["stale"]),
                    inferred=bool(item["inferred"]),
                )
                for item in payload["enemy_contacts"]
            ),
            recent_changes=tuple(payload["recent_changes"]),
            standing_orders=tuple(payload["standing_orders"]),
            requests_for_decision=tuple(payload["requests_for_decision"]),
            attachments=tuple(
                ObservationAttachment(
                    attachment_id=item["attachment_id"],
                    media_type=item["media_type"],
                    role=item["role"],
                    description=item["description"],
                    uri=item.get("uri"),
                    source=item.get("source", "placeholder"),
                    coalition_filtered=bool(item.get("coalition_filtered", True)),
                    metadata=dict(item.get("metadata", {})),
                )
                for item in payload.get("attachments", ())
            ),
        )
        return ObservationArtifact(
            id=row["id"],
            run_id=row["run_id"],
            coalition=Coalition(row["coalition"]),
            decision_cycle=row["decision_cycle"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
            previous_observation_id=row["previous_observation_id"],
            fusion_update_id=row["fusion_update_id"],
            observation=observation,
            narrative=row["narrative_text"],
            attachment_artifacts=tuple(
                ObservationAttachmentArtifact(
                    attachment_id=item["attachment_id"],
                    run_id=item["run_id"],
                    coalition=Coalition(item["coalition"]),
                    decision_cycle=item["decision_cycle"],
                    media_type=item["media_type"],
                    role=item["role"],
                    file_path=item["file_path"],
                    submitted_to_backend=bool(item["submitted_to_backend"]),
                    metadata=json.loads(item["metadata_json"]),
                )
                for item in attachment_rows
            ),
        )

    def _row_to_action_validation_batch(
        self,
        batch_row: sqlite3.Row,
        result_rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    ) -> ActionValidationBatch:
        return ActionValidationBatch(
            id=batch_row["id"],
            run_id=batch_row["run_id"],
            coalition=Coalition(batch_row["coalition"]),
            decision_cycle=batch_row["decision_cycle"],
            submitted_at=datetime.fromisoformat(batch_row["submitted_at"]),
            raw_payload=tuple(json.loads(batch_row["raw_payload_json"])),
            results=tuple(
                ActionValidationResult(
                    action_id=row["action_id"],
                    action_type=ActionType(row["action_type"]) if row["action_type"] else None,
                    status=ValidationStatus(row["status"]),
                    rejection_code=RejectionCode(row["rejection_code"]) if row["rejection_code"] else None,
                    message=row["message"],
                    normalized_params=json.loads(row["normalized_params_json"]),
                    messages=tuple(
                        ValidationMessage(**message) for message in json.loads(row["messages_json"])
                    ),
                    validated_stages=tuple(json.loads(row["validated_stages_json"])),
                )
                for row in result_rows
            ),
            non_hold_action_count=batch_row["non_hold_action_count"],
            soft_cap_exceeded=bool(batch_row["soft_cap_exceeded"]),
            batch_messages=tuple(
                ValidationMessage(**message) for message in json.loads(batch_row["batch_messages_json"])
            ),
            latest_observation_id=batch_row["latest_observation_id"],
        )

    def _row_to_model_invocation_result(self, row: sqlite3.Row) -> ModelInvocationResult:
        return ModelInvocationResult(
            id=row["id"],
            run_id=row["run_id"],
            coalition=Coalition(row["coalition"]),
            decision_cycle=row["decision_cycle"],
            backend_name=row["backend_name"],
            observation_id=row["observation_id"],
            requested_at=datetime.fromisoformat(row["requested_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]),
            status=row["status"],
            attempt_count=row["attempt_count"],
            request_payload=json.loads(row["request_payload_json"]),
            raw_response=row["raw_response_text"],
            parsed_actions=tuple(json.loads(row["parsed_actions_json"])),
            parse_status=row["parse_status"],
            error_detail=row["error_detail"],
            latency_ms=row["latency_ms"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
            validation_batch_id=row["validation_batch_id"],
            wrapped_response_object=json.loads(row["wrapped_response_json"]) if row["wrapped_response_json"] else None,
            failover_used=bool(row["failover_used"]),
            attempt_trace=tuple(
                ModelInvocationAttempt(**item) for item in json.loads(row["attempt_trace_json"] or "[]")
            ),
        )

    def _row_to_ingest_cycle_result(self, row: sqlite3.Row) -> IngestCycleResult:
        normalized_batch = json.loads(row["normalized_batch_json"])
        return IngestCycleResult(
            id=row["id"],
            run_id=row["run_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            mission_time=row["mission_time"],
            olympus_available=bool(row["olympus_available"]),
            grpc_available=bool(row["grpc_available"]),
            normalized_batch=NormalizedIntegrationBatch(**normalized_batch),
            error_classification=row["error_classification"],
            error_detail=row["error_detail"],
        )

    def _row_to_execution_batch_result(
        self,
        batch_row: sqlite3.Row,
        result_rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    ) -> ExecutionBatchResult:
        return ExecutionBatchResult(
            id=batch_row["id"],
            run_id=batch_row["run_id"],
            coalition=Coalition(batch_row["coalition"]),
            decision_cycle=batch_row["decision_cycle"],
            observation_id=batch_row["observation_id"],
            model_invocation_id=batch_row["model_invocation_id"],
            validation_batch_id=batch_row["validation_batch_id"],
            started_at=datetime.fromisoformat(batch_row["started_at"]),
            completed_at=_parse_dt(batch_row["completed_at"]),
            lifecycle_state=ExecutionLifecycleState(batch_row["lifecycle_state"]),
            status=ExecutionStatus(batch_row["status"]),
            results=tuple(
                ExecutionResult(
                    action_id=row["action_id"],
                    action_type=ActionType(row["action_type"]) if row["action_type"] else None,
                    status=ExecutionStatus(row["status"]),
                    execution_summary=row["execution_summary"],
                    command_count=row["command_count"],
                    resource_delta=json.loads(row["resource_delta_json"]),
                    standing_order_delta=tuple(json.loads(row["standing_order_delta_json"])),
                    resulting_entities=tuple(json.loads(row["resulting_entities_json"])),
                    error_detail=row["error_detail"],
                    applied_commands=tuple(
                        ExecutionCommand(**command) for command in json.loads(row["applied_commands_json"])
                    ),
                )
                for row in result_rows
            ),
            summary=tuple(json.loads(batch_row["summary_json"])),
        )

    def _row_to_current_execution_note(self, row: sqlite3.Row) -> CurrentExecutionNote:
        return CurrentExecutionNote(
            run_id=row["run_id"],
            coalition=Coalition(row["coalition"]),
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            decision_cycle=row["decision_cycle"],
            action_id=row["action_id"],
            note=row["note"],
            lifecycle_state=ExecutionLifecycleState(row["lifecycle_state"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata_json"]),
        )

    def _row_to_standing_order_history_entry(self, row: sqlite3.Row) -> StandingOrderHistoryEntry:
        return StandingOrderHistoryEntry(
            id=row["id"],
            run_id=row["run_id"],
            order_id=row["order_id"],
            coalition=Coalition(row["coalition"]),
            action_type=ActionType(row["action_type"]),
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            transition=row["transition"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            metadata=json.loads(row["metadata_json"]),
        )

    def _row_to_fusion_snapshot_record(self, row: sqlite3.Row) -> FusionSnapshotRecord:
        return FusionSnapshotRecord(
            id=row["id"],
            run_id=row["run_id"],
            coalition=Coalition(row["coalition"]),
            fusion_update_id=row["fusion_update_id"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
            mission_time=row["mission_time"],
            known_friendly_group_ids=tuple(json.loads(row["known_friendly_group_ids_json"])),
            sector_activity=json.loads(row["sector_activity_json"]),
        )

    def _row_to_execution_standing_order(self, row: sqlite3.Row) -> StandingOrderRecord:
        return StandingOrderRecord(
            id=row["order_id"],
            run_id=row["run_id"],
            coalition=Coalition(row["coalition"]),
            action_type=ActionType(row["action_type"]),
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            fingerprint=row["fingerprint"],
            text=row["text"],
            active=bool(row["active"]),
            last_updated_at=_parse_dt(row["last_updated_at"]),
            metadata=json.loads(row["metadata_json"]),
        )

    def _row_to_decision_cycle_result(self, row: sqlite3.Row) -> DecisionCycleResult:
        return DecisionCycleResult(
            id=row["id"],
            run_id=row["run_id"],
            decision_cycle=row["decision_cycle"],
            snapshot_time=datetime.fromisoformat(row["snapshot_time"]),
            red_observation_id=row["red_observation_id"],
            blue_observation_id=row["blue_observation_id"],
            red_invocation_id=row["red_invocation_id"],
            blue_invocation_id=row["blue_invocation_id"],
            red_execution_batch_id=row["red_execution_batch_id"],
            blue_execution_batch_id=row["blue_execution_batch_id"],
            classification=row["classification"] or "both_failed",
            summary=tuple(json.loads(row["summary_json"])),
        )

    def _row_to_run_control_state(self, row: sqlite3.Row) -> RunControlState:
        return RunControlState(
            run_id=row["run_id"],
            scenario_id=row["scenario_id"],
            scenario_version=row["scenario_version"],
            scenario_name=row["scenario_name"],
            theater=row["theater"],
            mode=row["mode"] or "dry",
            status=RunLifecycleStatus(row["status"] or RunLifecycleStatus.CREATED.value),
            created_at=datetime.fromisoformat(row["created_at"]),
            config_digest=row["config_digest"],
            config_snapshot=json.loads(row["config_snapshot_json"]) if row["config_snapshot_json"] else None,
            red_backend_name=row["red_backend_name"],
            blue_backend_name=row["blue_backend_name"],
            started_at=_parse_dt(row["started_at"]),
            paused_at=_parse_dt(row["paused_at"]),
            resumed_at=_parse_dt(row["resumed_at"]),
            stopped_at=_parse_dt(row["stopped_at"]),
            failed_at=_parse_dt(row["failed_at"]),
            terminal_reason=row["terminal_reason"],
            evaluation_metadata=json.loads(row["evaluation_metadata_json"]) if row["evaluation_metadata_json"] else None,
        )

    def _row_to_scenario_draft(self, row: sqlite3.Row) -> ScenarioDraftView:
        scenario_payload = json.loads(row["payload_json"])
        return ScenarioDraftView(
            draft_id=row["draft_id"],
            source_scenario_id=row["source_scenario_id"],
            source_scenario_name=None,
            scenario_id=row["scenario_id"],
            name=str(scenario_payload.get("name", row["scenario_id"])),
            display_name=row["name"],
            theater=row["theater"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            scenario=scenario_payload,
            dirty=False,
            map_reference=None,
            pydcs_mapping=json.loads(row["pydcs_mapping_json"]),
        )

    def _row_to_evaluation_run_summary(self, row: sqlite3.Row) -> EvaluationRunSummary:
        payload = json.loads(row["summary_json"])
        return EvaluationRunSummary(
            run_id=payload["run_id"],
            generated_at=datetime.fromisoformat(payload["generated_at"]),
            profile_name=payload.get("profile_name"),
            decision_cadence_sec=payload.get("decision_cadence_sec"),
            prompt_version=payload.get("prompt_version"),
            resource_settings_version=payload.get("resource_settings_version"),
            fairness_mode=FairnessMode(payload["fairness_mode"]),
            review_policy=EvaluationReviewPolicy(payload["review_policy"]),
            loop_health=payload["loop_health"],
            action_quality=payload["action_quality"],
            resource_discipline=payload["resource_discipline"],
            strategic_behavior=payload["strategic_behavior"],
            observation_integrity=payload["observation_integrity"],
            fairness_score=payload["fairness_score"],
            fairness_rating=EvaluationRating(payload["fairness_rating"]),
            suspicious_finding_count=payload["suspicious_finding_count"],
            findings_pending_review=payload["findings_pending_review"],
            top_rejection_codes=payload["top_rejection_codes"],
            tuning_notes=tuple(
                TuningNote(
                    note_type=item["note_type"],
                    severity=item["severity"],
                    detail=item["detail"],
                )
                for item in payload.get("tuning_notes", ())
            ),
        )

    def _row_to_evaluation_cycle_metrics(self, row: sqlite3.Row) -> EvaluationCycleMetrics:
        payload = json.loads(row["metrics_json"])
        return EvaluationCycleMetrics(
            run_id=payload["run_id"],
            decision_cycle=payload["decision_cycle"],
            coalition_action_counts=payload["coalition_action_counts"],
            duplicate_action_count=payload["duplicate_action_count"],
            conflict_action_count=payload["conflict_action_count"],
            low_confidence_enemy_decision_count=payload["low_confidence_enemy_decision_count"],
            average_enemy_contact_staleness_sec=payload["average_enemy_contact_staleness_sec"],
            suspicious_finding_count=payload["suspicious_finding_count"],
            control_point_owner_changes=payload["control_point_owner_changes"],
        )

    def _row_to_fairness_finding(self, row: sqlite3.Row) -> FairnessFinding:
        return FairnessFinding(
            id=row["id"],
            run_id=row["run_id"],
            coalition=Coalition(row["coalition"]) if row["coalition"] else None,
            decision_cycle=row["decision_cycle"],
            finding_type=row["finding_type"],
            machine_status=FairnessMachineStatus(row["machine_status"]),
            review_status=FairnessReviewStatus(row["review_status"]),
            detail=row["detail"],
            score_penalty=row["score_penalty"],
            finding_data=json.loads(row["finding_json"]),
            reviewer_note=row["reviewer_note"],
            reviewed_at=_parse_dt(row["reviewed_at"]),
        )

    def _row_to_matrix_case_result(self, row: sqlite3.Row) -> MatrixCaseResult:
        return MatrixCaseResult(
            id=row["id"],
            profile_name=row["profile_name"],
            case_id=row["case_id"],
            description=row["description"],
            repeat_index=row["repeat_index"],
            status=MatrixCaseStatus(row["status"]),
            mode=row["mode"],
            red_backend=row["red_backend"],
            blue_backend=row["blue_backend"],
            decision_cadence_sec=row["decision_cadence_sec"],
            run_cycles=row["run_cycles"],
            run_id=row["run_id"],
            evaluation_run_id=row["evaluation_run_id"],
            detail=row["detail"],
        )

    def _row_to_matrix_run_report(self, row: sqlite3.Row) -> MatrixRunReport:
        payload = json.loads(row["report_json"])
        return MatrixRunReport(
            profile_name=payload["profile_name"],
            generated_at=datetime.fromisoformat(payload["generated_at"]),
            case_results=tuple(
                MatrixCaseResult(
                    id=item.get("id"),
                    profile_name=item["profile_name"],
                    case_id=item["case_id"],
                    description=item["description"],
                    repeat_index=item["repeat_index"],
                    status=MatrixCaseStatus(item["status"]),
                    mode=item["mode"],
                    red_backend=item["red_backend"],
                    blue_backend=item["blue_backend"],
                    decision_cadence_sec=item["decision_cadence_sec"],
                    run_cycles=item["run_cycles"],
                    run_id=item.get("run_id"),
                    evaluation_run_id=item.get("evaluation_run_id"),
                    detail=item.get("detail"),
                )
                for item in payload["case_results"]
            ),
            completed_case_count=payload["completed_case_count"],
            failed_case_count=payload["failed_case_count"],
            skipped_case_count=payload["skipped_case_count"],
        )

    def _row_to_replay_export_result(self, row: sqlite3.Row) -> ReplayExportResult:
        return ReplayExportResult(
            run_id=row["run_id"],
            output_dir=row["output_dir"],
            manifest_path=row["manifest_path"],
            file_count=row["file_count"],
            exported_at=_parse_dt(row["exported_at"]),
            file_inventory=tuple(json.loads(row["file_inventory_json"])),
            includes_evaluation_summary=bool(row["includes_evaluation_summary"]),
            includes_cycle_evaluations=bool(row["includes_cycle_evaluations"]),
            includes_fairness_findings=bool(row["includes_fairness_findings"]),
            includes_matrix_report=bool(row["includes_matrix_report"]),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
