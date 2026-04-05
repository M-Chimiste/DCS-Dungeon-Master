"""World state services for Phase 1 truth management."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt
from typing import Any

from dcs_dungeon_master.core.enums import Coalition
from dcs_dungeon_master.core.models import (
    EvidenceRecord,
    ScenarioDefinition,
    WorldControlPointState,
    WorldGroupState,
    WorldStateSnapshot,
)
from dcs_dungeon_master.integration.types import (
    GrpcMissionMetadataSnapshot,
    GrpcStreamEnvelope,
    OlympusAirfieldSnapshot,
    OlympusMissionSnapshot,
    OlympusUnitSnapshot,
)
from dcs_dungeon_master.persistence import SQLiteStateStore


@dataclass(slots=True)
class WorldStateRepository:
    store: SQLiteStateStore

    @property
    def status(self) -> str:
        return "world-state-ready"

    def get_snapshot(self, run_id: str | None = None) -> WorldStateSnapshot:
        return self.store.get_world_state_snapshot(run_id)

    def summarize(self, run_id: str | None = None) -> dict[str, Any]:
        snapshot = self.get_snapshot(run_id)
        group_counts: dict[str, int] = {}
        for group in snapshot.groups:
            key = group.coalition.value if group.coalition else "unknown"
            group_counts[key] = group_counts.get(key, 0) + 1
        return {
            "run_id": snapshot.run_id,
            "scenario_id": snapshot.scenario_id,
            "theater": snapshot.theater,
            "mission_time": snapshot.mission_time,
            "last_ingest_at": snapshot.last_ingest_at.isoformat() if snapshot.last_ingest_at else None,
            "group_count": len(snapshot.groups),
            "group_counts_by_coalition": group_counts,
            "control_point_count": len(snapshot.control_points),
            "evidence_count": len(snapshot.evidence_records),
        }


@dataclass(slots=True)
class KnowledgeDebugView:
    store: SQLiteStateStore
    fusion_service: Any | None = None

    def compare_truth_vs_knowledge(self, run_id: str, coalition: Coalition) -> dict[str, Any]:
        world = self.store.get_world_state_snapshot(run_id)
        if self.fusion_service is not None:
            preview = self.fusion_service.preview(run_id)
            knowledge = next(item for item in preview.knowledge_states if item.coalition is coalition)
        else:
            knowledge = self.store.get_knowledge_state(coalition, run_id)
        enemy_truth_ids = sorted(
            group.id for group in world.groups if group.coalition is not None and group.coalition is not coalition
        )
        visible_track_ids = sorted(track.track_id for track in knowledge.contact_tracks)
        archived_track_ids = sorted(track.track_id for track in knowledge.archived_tracks)
        hidden_enemy_ids = sorted(group_id for group_id in enemy_truth_ids if group_id not in visible_track_ids)
        return {
            "coalition": coalition.value,
            "enemy_truth_group_ids": enemy_truth_ids,
            "visible_track_ids": visible_track_ids,
            "archived_track_ids": archived_track_ids,
            "hidden_enemy_group_ids": hidden_enemy_ids,
        }


@dataclass(slots=True)
class WorldStateUpdater:
    repository: WorldStateRepository
    scenario: ScenarioDefinition

    @property
    def status(self) -> str:
        return "world-state-ready"

    def apply_integration_updates(
        self,
        run_id: str,
        *,
        mission_snapshot: OlympusMissionSnapshot | None = None,
        unit_snapshots: tuple[OlympusUnitSnapshot, ...] = (),
        airfield_snapshots: tuple[OlympusAirfieldSnapshot, ...] = (),
        grpc_metadata: GrpcMissionMetadataSnapshot | None = None,
        grpc_events: tuple[GrpcStreamEnvelope, ...] = (),
        occurred_at: datetime | None = None,
    ) -> WorldStateSnapshot:
        timestamp = occurred_at or datetime.now(UTC)
        current = self.repository.get_snapshot(run_id)
        groups = {group.id: group for group in current.groups}
        control_points = {control_point.control_point_id: control_point for control_point in current.control_points}
        evidence_records: list[EvidenceRecord] = []
        mission_time = current.mission_time

        if mission_snapshot is not None:
            mission_time = mission_snapshot.mission_time or mission_time
            evidence_records.append(
                EvidenceRecord(
                    id=None,
                    run_id=run_id,
                    source="olympus",
                    evidence_type="olympus_mission_snapshot",
                    entity_id=None,
                    coalition=None,
                    sector_id=None,
                    occurred_at=timestamp,
                    summary=f"Mission snapshot at {mission_snapshot.mission_time or 'unknown_time'}",
                    payload=_serialize_payload(mission_snapshot),
                )
            )

        if grpc_metadata is not None:
            mission_time = grpc_metadata.mission_time or mission_time
            evidence_records.append(
                EvidenceRecord(
                    id=None,
                    run_id=run_id,
                    source="dcs_grpc",
                    evidence_type="grpc_mission_metadata",
                    entity_id=None,
                    coalition=None,
                    sector_id=None,
                    occurred_at=timestamp,
                    summary=f"gRPC mission metadata at {grpc_metadata.mission_time}",
                    payload=_serialize_payload(grpc_metadata),
                )
            )

        for snapshot in unit_snapshots:
            coalition = _coalition_or_none(snapshot.coalition)
            sector_id = self._sector_for_position(snapshot.lat, snapshot.lng)
            group_id = snapshot.unit_id
            existing = groups.get(group_id)
            groups[group_id] = WorldGroupState(
                id=group_id,
                source_id=snapshot.unit_id,
                coalition=coalition,
                group_type=snapshot.unit_type or (existing.group_type if existing else "unknown_group"),
                category=snapshot.category,
                name=snapshot.name,
                sector_id=sector_id or (existing.sector_id if existing else None),
                control_point_id=existing.control_point_id if existing else None,
                lat=snapshot.lat,
                lng=snapshot.lng,
                alt=snapshot.alt,
                heading=snapshot.heading,
                speed=snapshot.speed,
                health=snapshot.health,
                source="olympus",
                last_updated_at=timestamp,
                high_value=_is_high_value_group(snapshot.unit_type, snapshot.category),
                mobile=(snapshot.speed or 0.0) > 0.0 or (existing.mobile if existing else True),
            )
            evidence_records.append(
                EvidenceRecord(
                    id=None,
                    run_id=run_id,
                    source="olympus",
                    evidence_type="olympus_unit_snapshot",
                    entity_id=group_id,
                    coalition=coalition,
                    sector_id=sector_id,
                    occurred_at=timestamp,
                    summary=f"Olympus unit snapshot for {group_id}",
                    payload=_serialize_payload(snapshot) | {"observed_by": coalition.value if coalition else None},
                )
            )

        for snapshot in airfield_snapshots:
            control_point_id = self._control_point_id_for_airfield(snapshot)
            if control_point_id is None:
                continue
            previous = control_points.get(control_point_id)
            owner = _coalition_or_none(snapshot.owner)
            sector_id = previous.sector_id if previous else self._sector_for_control_point(control_point_id)
            control_points[control_point_id] = WorldControlPointState(
                control_point_id=control_point_id,
                owner=owner,
                sector_id=sector_id,
                last_updated_at=timestamp,
                source="olympus",
            )
            evidence_records.append(
                EvidenceRecord(
                    id=None,
                    run_id=run_id,
                    source="olympus",
                    evidence_type="olympus_airfield_snapshot",
                    entity_id=control_point_id,
                    coalition=owner,
                    sector_id=sector_id,
                    occurred_at=timestamp,
                    summary=f"Olympus airfield snapshot for {control_point_id}",
                    payload=_serialize_payload(snapshot),
                )
            )

        for event in grpc_events:
            sector_id = _payload_sector(event.payload) or self._sector_for_position(
                _optional_float(event.payload.get("lat")),
                _optional_float(event.payload.get("lng")),
            )
            observed_by = _coalition_or_none(event.coalition or event.payload.get("observed_by"))
            target_coalition = _coalition_or_none(event.payload.get("target_coalition"))
            group_id = event.entity_id or str(event.payload.get("unit_id") or event.payload.get("event_id") or "unknown")
            existing = groups.get(group_id)
            if event.stream_name == "stream_units":
                groups[group_id] = WorldGroupState(
                    id=group_id,
                    source_id=group_id,
                    coalition=target_coalition or (existing.coalition if existing else None),
                    group_type=str(
                        event.payload.get("group_type")
                        or event.payload.get("unit_type")
                        or event.payload.get("category")
                        or (existing.group_type if existing else "unknown_group")
                    ),
                    category=_optional_str(event.payload.get("category")),
                    name=_optional_str(event.payload.get("name")) or (existing.name if existing else None),
                    sector_id=sector_id or (existing.sector_id if existing else None),
                    control_point_id=existing.control_point_id if existing else None,
                    lat=_optional_float(event.payload.get("lat")),
                    lng=_optional_float(event.payload.get("lng")),
                    alt=_optional_float(event.payload.get("alt")),
                    heading=_optional_float(event.payload.get("heading")),
                    speed=_optional_float(event.payload.get("speed")),
                    health=_optional_float(event.payload.get("health")),
                    source="dcs_grpc",
                    last_updated_at=timestamp,
                    high_value=_is_high_value_group(
                        _optional_str(event.payload.get("unit_type")),
                        _optional_str(event.payload.get("category")),
                    ),
                    mobile=bool((_optional_float(event.payload.get("speed")) or 0.0) > 0.0 or (existing.mobile if existing else True)),
                )
            evidence_records.append(
                EvidenceRecord(
                    id=None,
                    run_id=run_id,
                    source="dcs_grpc",
                    evidence_type=f"grpc_{event.stream_name}",
                    entity_id=group_id,
                    coalition=observed_by,
                    sector_id=sector_id,
                    occurred_at=timestamp,
                    summary=f"{event.stream_name}:{event.event_type} for {group_id}",
                    payload=event.payload | {"event_type": event.event_type, "observed_by": observed_by.value if observed_by else None},
                )
            )

        self.repository.store.save_world_state_snapshot(
            run_id=run_id,
            scenario_id=current.scenario_id,
            theater=current.theater,
            mission_time=mission_time,
            last_ingest_at=timestamp,
            groups=tuple(sorted(groups.values(), key=lambda item: item.id)),
            control_points=tuple(sorted(control_points.values(), key=lambda item: item.control_point_id)),
            evidence_records=tuple(evidence_records),
        )
        return self.repository.get_snapshot(run_id)

    def _sector_for_control_point(self, control_point_id: str) -> str:
        for control_point in self.scenario.control_points:
            if control_point.id == control_point_id:
                return control_point.sector_id
        return self.scenario.sectors[0].id

    def _control_point_id_for_airfield(self, snapshot: OlympusAirfieldSnapshot) -> str | None:
        if snapshot.airfield_id in {control_point.id for control_point in self.scenario.control_points}:
            return snapshot.airfield_id
        for control_point in self.scenario.control_points:
            if snapshot.name and control_point.name == snapshot.name:
                return control_point.id
        return None

    def _sector_for_position(self, lat: float | None, lng: float | None) -> str | None:
        if lat is None or lng is None:
            return None
        best_sector_id: str | None = None
        best_distance = float("inf")
        for sector in self.scenario.sectors:
            if sector.center_lat is None or sector.center_lng is None:
                continue
            distance = _nm_distance(lat, lng, sector.center_lat, sector.center_lng)
            if distance <= sector.radius_nm and distance < best_distance:
                best_distance = distance
                best_sector_id = sector.id
        if best_sector_id is not None:
            return best_sector_id
        for sector in self.scenario.sectors:
            if sector.center_lat is None or sector.center_lng is None:
                continue
            distance = _nm_distance(lat, lng, sector.center_lat, sector.center_lng)
            if distance < best_distance:
                best_distance = distance
                best_sector_id = sector.id
        return best_sector_id


def _coalition_or_none(value: object) -> Coalition | None:
    if isinstance(value, Coalition):
        return value
    if isinstance(value, str):
        try:
            return Coalition(value)
        except ValueError:
            return None
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _payload_sector(payload: dict[str, Any]) -> str | None:
    sector_id = payload.get("sector_id")
    return sector_id if isinstance(sector_id, str) and sector_id.strip() else None


def _serialize_payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "__dict__"):
        return {key: val for key, val in vars(value).items() if not key.startswith("_")}
    return {"value": value}


def _is_high_value_group(unit_type: str | None, category: str | None) -> bool:
    haystack = " ".join(part for part in (unit_type, category) if part)
    lowered = haystack.lower()
    return any(token in lowered for token in ("sam", "air_defense", "radar", "fires"))


def _nm_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    earth_radius_nm = 3440.065
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lng = radians(lng2 - lng1)
    a = sin(delta_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lng / 2) ** 2
    c = 2 * asin(sqrt(a))
    return earth_radius_nm * c


__all__ = ["KnowledgeDebugView", "WorldStateRepository", "WorldStateUpdater"]
