"""Sensor fusion services for coalition-safe knowledge generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from dcs_dungeon_master.core.config import FogOfWarConfig
from dcs_dungeon_master.core.enums import Coalition, ConfidenceBand, KnowledgeLevel
from dcs_dungeon_master.core.models import (
    CoalitionContactTrack,
    CoalitionKnowledgeState,
    EvidenceRecord,
    FogOfWarPolicy,
    FusionUpdateResult,
    ScenarioDefinition,
    TrackEvidenceSummary,
    WorldGroupState,
)
from dcs_dungeon_master.persistence import SQLiteStateStore


@dataclass(slots=True)
class SensorFusionService:
    store: SQLiteStateStore
    scenario: ScenarioDefinition
    config: FogOfWarConfig

    @property
    def status(self) -> str:
        return "sensor-fusion-ready"

    def build_policy(self) -> FogOfWarPolicy:
        return FogOfWarPolicy(
            freshness_window_sec=self.config.freshness_window_sec,
            decay_window_sec=self.config.decay_window_sec,
            archive_window_sec=self.config.archive_window_sec,
            adjacency_drift_enabled=self.config.adjacency_drift_enabled,
            inference_mode=self.config.inference_mode,
            debug_truth_comparison=self.config.debug_truth_comparison,
        )

    def generate(self, run_id: str, *, now: datetime | None = None) -> FusionUpdateResult:
        result = self.preview(run_id, now=now)
        self.store.save_fusion_update(run_id, result)
        return result

    def preview(self, run_id: str, *, now: datetime | None = None) -> FusionUpdateResult:
        world = self.store.get_world_state_snapshot(run_id)
        generated_at = now or world.last_ingest_at or datetime.now(UTC)
        policy = self.build_policy()
        knowledge_states = tuple(self._build_knowledge_state(world, coalition, generated_at, policy) for coalition in Coalition)
        return FusionUpdateResult(
            generated_at=generated_at,
            policy=policy,
            knowledge_states=knowledge_states,
            active_track_count=sum(len(state.contact_tracks) for state in knowledge_states),
            archived_track_count=sum(len(state.archived_tracks) for state in knowledge_states),
            change_summary=(
                f"red_tracks={len(knowledge_states[0].contact_tracks)}",
                f"blue_tracks={len(knowledge_states[1].contact_tracks)}",
            ),
        )

    def summarize(self, run_id: str, coalition: Coalition) -> dict[str, object]:
        state = next(item for item in self.preview(run_id).knowledge_states if item.coalition is coalition)
        return {
            "coalition": coalition.value,
            "generated_at": state.generated_at.isoformat(),
            "mission_time": state.mission_time,
            "contact_track_count": len(state.contact_tracks),
            "archived_track_count": len(state.archived_tracks),
            "known_friendly_group_ids": list(state.known_friendly_group_ids),
            "sector_activity": state.sector_activity,
        }

    def debug_contact_track(self, run_id: str, coalition: Coalition, track_id: str) -> dict[str, object]:
        state = next(item for item in self.preview(run_id).knowledge_states if item.coalition is coalition)
        track = next((item for item in (*state.contact_tracks, *state.archived_tracks) if item.track_id == track_id), None)
        if track is None:
            return {"coalition": coalition.value, "track_id": track_id, "found": False}
        return {"coalition": coalition.value, "track_id": track_id, "found": True, "track": asdict(track)}

    def _build_knowledge_state(
        self,
        world,
        coalition: Coalition,
        generated_at: datetime,
        policy: FogOfWarPolicy,
    ) -> CoalitionKnowledgeState:
        friendly_ids = tuple(sorted(group.id for group in world.groups if group.coalition is coalition))
        enemy_groups = tuple(group for group in world.groups if group.coalition is not None and group.coalition is not coalition)
        tracks: list[CoalitionContactTrack] = []
        archived_tracks: list[CoalitionContactTrack] = []
        for group in enemy_groups:
            track = self._build_track(world.evidence_records, group, coalition, generated_at, policy)
            if track is None:
                continue
            if track.archived:
                archived_tracks.append(track)
            else:
                tracks.append(track)
        sector_activity = self._sector_activity(tracks)
        return CoalitionKnowledgeState(
            coalition=coalition,
            generated_at=generated_at,
            mission_time=world.mission_time,
            contact_tracks=tuple(sorted(tracks, key=lambda item: item.track_id)),
            archived_tracks=tuple(sorted(archived_tracks, key=lambda item: item.track_id)),
            known_friendly_group_ids=friendly_ids,
            sector_activity=sector_activity,
        )

    def _build_track(
        self,
        evidence_records: tuple[EvidenceRecord, ...],
        group: WorldGroupState,
        coalition: Coalition,
        generated_at: datetime,
        policy: FogOfWarPolicy,
    ) -> CoalitionContactTrack | None:
        relevant_evidence = tuple(
            evidence
            for evidence in evidence_records
            if evidence.entity_id == group.id and self._visible_to_coalition(evidence, coalition, group)
        )
        if not relevant_evidence:
            return None

        last_evidence = max(relevant_evidence, key=lambda item: item.occurred_at)
        seconds_since = max(0, int((generated_at - last_evidence.occurred_at).total_seconds()))
        archived = seconds_since >= policy.archive_window_sec
        stale = seconds_since >= policy.decay_window_sec
        confidence = self._confidence_for_age(seconds_since, policy, len(relevant_evidence))
        knowledge_level = self._knowledge_level(relevant_evidence, group)
        estimated_sector_id = None
        inferred = False
        if stale:
            inferred = True
            estimated_sector_id = self._estimate_sector(group)
        elif len(relevant_evidence) > 1 and knowledge_level is not KnowledgeLevel.CONFIRMED_TYPE:
            inferred = True

        summary = TrackEvidenceSummary(
            evidence_count=len(relevant_evidence),
            sources=tuple(sorted({evidence.source for evidence in relevant_evidence})),
            last_source=last_evidence.source,
            last_confirmed_at=last_evidence.occurred_at,
        )
        return CoalitionContactTrack(
            track_id=group.id,
            coalition=coalition,
            knowledge_level=knowledge_level,
            confidence=confidence,
            classification=group.group_type if knowledge_level is not KnowledgeLevel.UNKNOWN_PRESENCE else group.category,
            inferred=inferred,
            stale=stale,
            archived=archived,
            first_seen_at=min(evidence.occurred_at for evidence in relevant_evidence),
            last_confirmed_at=last_evidence.occurred_at,
            last_known_sector_id=group.sector_id,
            estimated_sector_id=estimated_sector_id,
            source_summary=summary,
            seconds_since_last_confirmation=seconds_since,
        )

    def _visible_to_coalition(self, evidence: EvidenceRecord, coalition: Coalition, group: WorldGroupState) -> bool:
        if evidence.source == "olympus" and evidence.evidence_type == "olympus_airfield_snapshot":
            return True
        observed_by = evidence.payload.get("observed_by") if isinstance(evidence.payload, dict) else None
        if observed_by == coalition.value:
            return True
        if evidence.source == "olympus" and group.coalition is coalition:
            return True
        if evidence.evidence_type.startswith("grpc_stream_") and evidence.coalition is coalition:
            return True
        return False

    def _confidence_for_age(
        self,
        seconds_since: int,
        policy: FogOfWarPolicy,
        evidence_count: int,
    ) -> ConfidenceBand:
        if seconds_since < policy.freshness_window_sec:
            return ConfidenceBand.HIGH if evidence_count > 1 else ConfidenceBand.MEDIUM
        if seconds_since < policy.decay_window_sec:
            return ConfidenceBand.MEDIUM
        return ConfidenceBand.LOW

    def _knowledge_level(
        self,
        evidence_records: tuple[EvidenceRecord, ...],
        group: WorldGroupState,
    ) -> KnowledgeLevel:
        if group.group_type and any(record.evidence_type in {"olympus_unit_snapshot", "grpc_stream_units"} for record in evidence_records):
            return KnowledgeLevel.CONFIRMED_TYPE
        if group.category and len(evidence_records) > 1:
            return KnowledgeLevel.CLASSIFIED_TYPE
        if group.category:
            return KnowledgeLevel.SUSPECTED_TYPE
        return KnowledgeLevel.UNKNOWN_PRESENCE

    def _estimate_sector(self, group: WorldGroupState) -> str | None:
        if not self.config.adjacency_drift_enabled or group.sector_id is None:
            return group.sector_id
        sector = next((item for item in self.scenario.sectors if item.id == group.sector_id), None)
        if sector is None or not sector.neighbor_ids:
            return group.sector_id
        return tuple(sorted(sector.neighbor_ids))[0]

    def _sector_activity(self, tracks: list[CoalitionContactTrack]) -> dict[str, str]:
        activity: dict[str, str] = {}
        for track in tracks:
            sector_id = track.estimated_sector_id or track.last_known_sector_id
            if not sector_id:
                continue
            if track.confidence is ConfidenceBand.HIGH:
                activity[sector_id] = "high"
            elif track.confidence is ConfidenceBand.MEDIUM and activity.get(sector_id) != "high":
                activity[sector_id] = "medium"
            elif sector_id not in activity:
                activity[sector_id] = "low"
        return activity


__all__ = ["SensorFusionService"]
