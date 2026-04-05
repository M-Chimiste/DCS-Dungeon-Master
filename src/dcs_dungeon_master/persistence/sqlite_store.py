"""SQLite materialized state and event log storage."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from dcs_dungeon_master.core.enums import Coalition, ConfidenceBand, GroupPosture, KnowledgeLevel
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.core.models import (
    ActiveGroupState,
    CoalitionContactTrack,
    CoalitionKnowledgeState,
    CoalitionState,
    CommanderStateView,
    CommanderObservation,
    ControlPointState,
    DeploymentRestrictionState,
    EnemyContactEntry,
    EvidenceRecord,
    EventRecord,
    FogOfWarPolicy,
    FriendlyForceEntry,
    FusionUpdateResult,
    KnownControlPointView,
    KnownSectorView,
    ObservationMeta,
    ObservationArtifact,
    ResourceStateView,
    ReserveAvailabilityView,
    ScenarioDefinition,
    ReserveGroupState,
    ScenarioStateView,
    SectorSummaryEntry,
    SectorState,
    StandingOrderState,
    TrackEvidenceSummary,
    WorldControlPointState,
    WorldGroupState,
    WorldStateSnapshot,
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
                    created_at TEXT NOT NULL
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
                    payload_json TEXT NOT NULL,
                    narrative_text TEXT NOT NULL,
                    action_links_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE (run_id, coalition, decision_cycle)
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
                """
            )

    def create_run_from_scenario(self, scenario: ScenarioDefinition) -> str:
        self.initialize_schema()
        run_id = uuid4().hex
        created_at = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (run_id, scenario_id, scenario_version, scenario_name, theater, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, scenario.id, scenario.version, scenario.name, scenario.theater, created_at.isoformat()),
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

    def get_run_summary(self, run_id: str | None = None) -> dict[str, Any]:
        resolved_run_id = run_id or self.get_latest_run_id()
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
            "coalitions": coalitions,
            "active_group_count": active_group_count,
            "reserve_group_count": reserve_group_count,
            "standing_order_count": standing_order_count,
            "control_point_ownership": control_point_ownership,
            "event_count": event_count,
            "world_group_count": world_group_count,
            "evidence_count": evidence_count,
            "active_track_count": active_track_count,
            "db_path": str(self.db_path),
        }

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
                    previous_observation_id, payload_json, narrative_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, coalition, decision_cycle) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    schema_version = excluded.schema_version,
                    previous_observation_id = excluded.previous_observation_id,
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
                    },
                ),
            )
        return observation_id

    def get_latest_observation(self, run_id: str, coalition: Coalition) -> ObservationArtifact:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, run_id, coalition, decision_cycle, generated_at, previous_observation_id, payload_json, narrative_text
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
                SELECT id, run_id, coalition, decision_cycle, generated_at, previous_observation_id, payload_json, narrative_text
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
            SELECT id, run_id, coalition, decision_cycle, generated_at, previous_observation_id, payload_json, narrative_text
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

    def save_fusion_update(self, run_id: str, result: FusionUpdateResult) -> None:
        with self._connect() as connection:
            connection.execute(
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
            for state in result.knowledge_states:
                connection.execute(
                    """
                    INSERT INTO knowledge_snapshots (
                        run_id, coalition, generated_at, mission_time, known_friendly_group_ids_json, sector_activity_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
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
                    },
                ),
            )

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
        )
        return ObservationArtifact(
            id=row["id"],
            run_id=row["run_id"],
            coalition=Coalition(row["coalition"]),
            decision_cycle=row["decision_cycle"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
            previous_observation_id=row["previous_observation_id"],
            observation=observation,
            narrative=row["narrative_text"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
