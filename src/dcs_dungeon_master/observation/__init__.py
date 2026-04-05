"""Observation builder and renderer services for Phase 1."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from typing import Any

from dcs_dungeon_master.core.enums import Coalition, ConfidenceBand
from dcs_dungeon_master.core.models import (
    CommanderObservation,
    CommanderStateView,
    EnemyContactEntry,
    FriendlyForceEntry,
    KnownControlPointView,
    KnownSectorView,
    ObservationArtifact,
    ObservationMeta,
    ReserveAvailabilityView,
    ResourceStateView,
    ScenarioDefinition,
    ScenarioStateView,
    SectorSummaryEntry,
)
from dcs_dungeon_master.core.versions import OBSERVATION_SCHEMA_VERSION
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.sensor_fusion import SensorFusionService


@dataclass(slots=True)
class ObservationBuilder:
    store: SQLiteStateStore
    scenario: ScenarioDefinition
    sensor_fusion: SensorFusionService

    @property
    def status(self) -> str:
        return "observation-ready"

    def build_observation(
        self,
        run_id: str,
        coalition: Coalition,
        decision_cycle: int,
        *,
        now: datetime | None = None,
        seconds_since_last_cycle: int = 30,
        persist: bool = True,
        fusion_result=None,
    ) -> ObservationArtifact:
        world = self.store.get_world_state_snapshot(run_id)
        generated_at = now or world.last_ingest_at or datetime.now(UTC)
        knowledge_result = fusion_result or self.sensor_fusion.preview(run_id, now=generated_at)
        knowledge = next(item for item in knowledge_result.knowledge_states if item.coalition is coalition)
        coalition_state = next(item for item in self.store.get_coalition_states(run_id) if item.coalition is coalition)
        active_groups = tuple(item for item in self.store.get_active_groups(run_id) if item.coalition is coalition)
        reserve_groups = self.store.get_reserve_groups(run_id, coalition)
        previous_artifact = self.store.get_previous_observation(run_id, coalition, decision_cycle)

        sector_summary = self._build_sector_summary(world, knowledge, coalition, previous_artifact)
        observation = CommanderObservation(
            meta=ObservationMeta(
                schema_version=OBSERVATION_SCHEMA_VERSION,
                coalition=coalition,
                snapshot_time=generated_at,
                decision_cycle=decision_cycle,
                seconds_since_last_cycle=seconds_since_last_cycle,
                picture_quality="filtered_and_incomplete",
            ),
            commander_state=self._build_commander_state(coalition_state, active_groups),
            scenario_state=self._build_scenario_state(world, coalition, sector_summary),
            resource_state=self._build_resource_state(coalition_state, reserve_groups, coalition),
            sector_summary=sector_summary,
            friendly_forces=self._build_friendly_forces(active_groups, world),
            enemy_contacts=self._build_enemy_contacts(knowledge),
            recent_changes=(),
            standing_orders=tuple(order.text for order in coalition_state.standing_orders if order.active),
            requests_for_decision=(),
        )
        observation = self._with_recent_changes_and_requests(observation, previous_artifact)
        narrative = self.render_narrative(observation)
        artifact = ObservationArtifact(
            id=None,
            run_id=run_id,
            coalition=coalition,
            decision_cycle=decision_cycle,
            generated_at=generated_at,
            previous_observation_id=previous_artifact.id if previous_artifact else None,
            observation=observation,
            narrative=narrative,
        )
        if persist:
            artifact = ObservationArtifact(
                id=self.store.save_observation_artifact(artifact),
                run_id=artifact.run_id,
                coalition=artifact.coalition,
                decision_cycle=artifact.decision_cycle,
                generated_at=artifact.generated_at,
                previous_observation_id=artifact.previous_observation_id,
                observation=artifact.observation,
                narrative=artifact.narrative,
            )
        return artifact

    def build_observation_pair(
        self,
        run_id: str,
        decision_cycle: int,
        *,
        now: datetime | None = None,
        seconds_since_last_cycle: int = 30,
        persist: bool = True,
    ) -> tuple[ObservationArtifact, ObservationArtifact]:
        world = self.store.get_world_state_snapshot(run_id)
        generated_at = now or world.last_ingest_at or datetime.now(UTC)
        fusion_result = self.sensor_fusion.preview(run_id, now=generated_at)
        return (
            self.build_observation(
                run_id,
                Coalition.RED,
                decision_cycle,
                now=generated_at,
                seconds_since_last_cycle=seconds_since_last_cycle,
                persist=persist,
                fusion_result=fusion_result,
            ),
            self.build_observation(
                run_id,
                Coalition.BLUE,
                decision_cycle,
                now=generated_at,
                seconds_since_last_cycle=seconds_since_last_cycle,
                persist=persist,
                fusion_result=fusion_result,
            ),
        )

    def summarize_latest(self, run_id: str, coalition: Coalition) -> dict[str, Any]:
        artifact = self.store.get_latest_observation(run_id, coalition)
        observation = artifact.observation
        return {
            "coalition": coalition.value,
            "decision_cycle": artifact.decision_cycle,
            "generated_at": artifact.generated_at.isoformat(),
            "enemy_contact_count": len(observation.enemy_contacts),
            "friendly_force_count": len(observation.friendly_forces),
            "recent_change_count": len(observation.recent_changes),
            "request_count": len(observation.requests_for_decision),
        }

    def render_latest(self, run_id: str, coalition: Coalition, fmt: str) -> dict[str, Any] | str:
        artifact = self.store.get_latest_observation(run_id, coalition)
        return self.render_artifact(artifact, fmt)

    def render_artifact(self, artifact: ObservationArtifact, fmt: str) -> dict[str, Any] | str:
        if fmt == "json":
            return self._jsonable(asdict(artifact.observation))
        if fmt == "narrative":
            return artifact.narrative
        return {
            "observation_id": artifact.id,
            "run_id": artifact.run_id,
            "coalition": artifact.coalition.value,
            "decision_cycle": artifact.decision_cycle,
            "generated_at": artifact.generated_at.isoformat(),
            "canonical": self._jsonable(asdict(artifact.observation)),
            "narrative": artifact.narrative,
        }

    def render_narrative(self, observation: CommanderObservation) -> str:
        top_sectors = [
            sector
            for sector in observation.sector_summary
            if sector.enemy_threat in {"high", "medium"} or sector.activity_level in {"active", "watchful"}
        ]
        top_sectors = sorted(top_sectors, key=lambda item: (item.enemy_threat, item.strategic_importance, item.sector_id), reverse=True)[:2]
        top_sector_summary = (
            ", ".join(
                f"{sector.sector_label} threat {sector.enemy_threat} confidence {sector.contact_confidence}"
                for sector in top_sectors
            )
            if top_sectors
            else "no sectors currently flagged above low threat"
        )
        contact_summary = (
            ", ".join(
                f"{contact.contact_id} {contact.knowledge_level.value} near {contact.estimated_sector or contact.last_known_sector or 'unknown'}"
                for contact in observation.enemy_contacts[:2]
            )
            if observation.enemy_contacts
            else "no enemy contacts currently visible"
        )
        request_summary = (
            "; ".join(observation.requests_for_decision[:2])
            if observation.requests_for_decision
            else "hold current plan"
        )
        return (
            f"{observation.meta.coalition.value.upper()} commander picture at "
            f"{observation.meta.snapshot_time.isoformat()} in {observation.scenario_state.theater}. "
            f"Budget {observation.resource_state.deployment_budget_remaining} with "
            f"{len(observation.resource_state.available_reserves)} reserves available. "
            f"Priority sectors: {top_sector_summary}. "
            f"Enemy picture: {contact_summary}. "
            f"Decisions requested: {request_summary}."
        )

    def _build_commander_state(self, coalition_state, active_groups) -> CommanderStateView:
        posture = None
        if active_groups:
            posture = Counter(group.posture.value for group in active_groups).most_common(1)[0][0]
        constraints = tuple(
            restriction.description
            for restriction in self.scenario.deployment_restrictions
            if restriction.coalition in {None, coalition_state.coalition}
        )
        return CommanderStateView(
            coalition=coalition_state.coalition,
            posture=posture,
            objectives=coalition_state.objectives,
            constraints=constraints,
            operator_instructions=(),
        )

    def _build_scenario_state(self, world, coalition: Coalition, sector_summary) -> ScenarioStateView:
        weather_summary = None
        for evidence in reversed(world.evidence_records):
            if evidence.evidence_type == "olympus_mission_snapshot":
                weather_summary = evidence.payload.get("weather_summary")
                break
        control_point_lookup = {item.control_point_id: item for item in world.control_points}
        known_control_points = []
        for control_point in sorted(self.scenario.control_points, key=lambda item: item.id):
            world_point = control_point_lookup.get(control_point.id)
            owner = world_point.owner if world_point else control_point.owner
            known_control_points.append(
                KnownControlPointView(
                    id=control_point.id,
                    name=control_point.name,
                    kind=control_point.kind,
                    sector_id=control_point.sector_id,
                    status=self._owner_status(owner, coalition),
                )
            )
        sector_status_by_id = {item.sector_id: item.control_status for item in sector_summary}
        known_sectors = tuple(
            KnownSectorView(
                id=sector.id,
                name=sector.name,
                role=sector.role,
                status=sector_status_by_id.get(sector.id, "unknown"),
            )
            for sector in sorted(self.scenario.sectors, key=lambda item: item.id)
        )
        front_status = tuple(
            f"{sector.name}:{sector.role}"
            for sector in sorted(self.scenario.sectors, key=lambda item: item.id)
            if sector.role in {"front", "contested", "air_corridor"}
        )
        return ScenarioStateView(
            theater=self.scenario.theater,
            mission_clock=world.mission_time,
            weather_summary=weather_summary,
            known_control_points=tuple(known_control_points),
            known_sectors=known_sectors,
            front_status=front_status,
        )

    def _build_resource_state(self, coalition_state, reserve_groups, coalition: Coalition) -> ResourceStateView:
        available_reserves = tuple(
            ReserveAvailabilityView(
                id=reserve.id,
                group_type=reserve.group_type,
                count=1,
                allowed_sectors=reserve.allowed_sector_ids,
            )
            for reserve in sorted(reserve_groups, key=lambda item: item.id)
            if reserve.available and reserve.status == "available"
        )
        restrictions = tuple(
            restriction.description
            for restriction in self.scenario.deployment_restrictions
            if restriction.coalition in {None, coalition}
        )
        shortages = []
        if coalition_state.budget_remaining <= 6:
            shortages.append("deployment_budget_low")
        if not available_reserves:
            shortages.append("no_reserves_available")
        return ResourceStateView(
            deployment_budget_remaining=coalition_state.budget_remaining,
            available_reserves=available_reserves,
            restrictions=restrictions,
            key_shortages=tuple(shortages),
            recently_lost_assets=(),
        )

    def _build_sector_summary(self, world, knowledge, coalition: Coalition, previous_artifact: ObservationArtifact | None):
        groups_by_sector: dict[str, list[Any]] = {}
        for group in world.groups:
            if group.sector_id:
                groups_by_sector.setdefault(group.sector_id, []).append(group)
        contacts_by_sector: dict[str, list[Any]] = {}
        for track in knowledge.contact_tracks:
            sector_id = track.estimated_sector_id or track.last_known_sector_id
            if sector_id:
                contacts_by_sector.setdefault(sector_id, []).append(track)
        control_points_by_sector: dict[str, list[Any]] = {}
        for item in world.control_points:
            control_points_by_sector.setdefault(item.sector_id, []).append(item)
        previous_summary = {}
        if previous_artifact is not None:
            previous_summary = {item.sector_id: item for item in previous_artifact.observation.sector_summary}

        entries = []
        for sector in sorted(self.scenario.sectors, key=lambda item: item.id):
            friendly_groups = [group for group in groups_by_sector.get(sector.id, []) if group.coalition is coalition]
            enemy_contacts = contacts_by_sector.get(sector.id, [])
            control_status = self._sector_control_status(control_points_by_sector.get(sector.id, []), coalition)
            enemy_threat = self._band_from_count(len(enemy_contacts), len([track for track in enemy_contacts if track.confidence is ConfidenceBand.HIGH]))
            friendly_strength = self._band_from_count(len(friendly_groups), len([group for group in friendly_groups if group.high_value]))
            contact_confidence = self._max_confidence(enemy_contacts)
            activity_level = self._activity_level(world.evidence_records, sector.id)
            strategic_importance = self._strategic_importance(sector.id)
            notes = []
            if previous_summary.get(sector.id) and previous_summary[sector.id].enemy_threat != enemy_threat:
                notes.append(f"enemy_threat_{previous_summary[sector.id].enemy_threat}_to_{enemy_threat}")
            if enemy_contacts:
                notes.append(f"{len(enemy_contacts)}_known_contacts")
            entries.append(
                SectorSummaryEntry(
                    sector_id=sector.id,
                    sector_label=sector.name,
                    control_status=control_status,
                    friendly_strength=friendly_strength,
                    enemy_threat=enemy_threat,
                    contact_confidence=contact_confidence,
                    activity_level=activity_level,
                    strategic_importance=strategic_importance,
                    recent_notes=tuple(notes[:3]),
                )
            )
        return tuple(entries)

    def _build_friendly_forces(self, active_groups, world) -> tuple[FriendlyForceEntry, ...]:
        world_lookup = {group.id: group for group in world.groups}
        entries = []
        for group in sorted(active_groups, key=lambda item: item.id):
            world_group = world_lookup.get(group.id)
            health_band = "unknown"
            if world_group and world_group.health is not None:
                if world_group.health >= 0.8:
                    health_band = "healthy"
                elif world_group.health >= 0.5:
                    health_band = "degraded"
                else:
                    health_band = "critical"
            elif group.status == "ready":
                health_band = "healthy"
            entries.append(
                FriendlyForceEntry(
                    force_id=group.id,
                    force_type=group.group_type,
                    assigned_sector=(world_group.sector_id if world_group else group.sector_id),
                    readiness="ready" if group.status == "ready" else group.status,
                    task=group.posture.value,
                    mobility="mobile" if group.mobile else "static",
                    health_band=health_band,
                    high_value=bool(world_group.high_value) if world_group else ("air_defense" in group.group_type or "fires" in group.group_type),
                )
            )
        return tuple(entries)

    def _build_enemy_contacts(self, knowledge) -> tuple[EnemyContactEntry, ...]:
        return tuple(
            EnemyContactEntry(
                contact_id=track.track_id,
                knowledge_level=track.knowledge_level,
                classification=track.classification,
                last_known_sector=track.last_known_sector_id,
                estimated_sector=track.estimated_sector_id,
                time_since_last_confirmation_sec=track.seconds_since_last_confirmation,
                confidence=track.confidence,
                detection_sources=track.source_summary.sources if track.source_summary else (),
                threat_estimate=self._threat_estimate(track.classification, track.confidence),
                stale=track.stale,
                inferred=track.inferred,
            )
            for track in sorted(knowledge.contact_tracks, key=lambda item: item.track_id)
        )

    def _with_recent_changes_and_requests(
        self,
        observation: CommanderObservation,
        previous_artifact: ObservationArtifact | None,
    ) -> CommanderObservation:
        recent_changes = self._recent_changes(observation, previous_artifact)
        requests = self._requests_for_decision(observation)
        return CommanderObservation(
            meta=observation.meta,
            commander_state=observation.commander_state,
            scenario_state=observation.scenario_state,
            resource_state=observation.resource_state,
            sector_summary=observation.sector_summary,
            friendly_forces=observation.friendly_forces,
            enemy_contacts=observation.enemy_contacts,
            recent_changes=recent_changes,
            standing_orders=observation.standing_orders,
            requests_for_decision=requests,
        )

    def _recent_changes(
        self,
        current: CommanderObservation,
        previous_artifact: ObservationArtifact | None,
    ) -> tuple[str, ...]:
        if previous_artifact is None:
            return ()
        previous = previous_artifact.observation
        changes: list[str] = []
        prev_contacts = {item.contact_id: item for item in previous.enemy_contacts}
        curr_contacts = {item.contact_id: item for item in current.enemy_contacts}
        for contact_id, contact in curr_contacts.items():
            if contact_id not in prev_contacts:
                changes.append(f"new_contact_{contact_id}_in_{contact.estimated_sector or contact.last_known_sector or 'unknown_sector'}")
                continue
            prev = prev_contacts[contact_id]
            if not prev.stale and contact.stale:
                changes.append(f"contact_{contact_id}_became_stale")
            if prev.confidence is not contact.confidence:
                changes.append(f"contact_{contact_id}_confidence_{prev.confidence.value}_to_{contact.confidence.value}")
        prev_control_points = {item.id: item.status for item in previous.scenario_state.known_control_points}
        for item in current.scenario_state.known_control_points:
            prev_status = prev_control_points.get(item.id)
            if prev_status is not None and prev_status != item.status:
                changes.append(f"control_point_{item.id}_status_{prev_status}_to_{item.status}")
        prev_reserves = {item.id for item in previous.resource_state.available_reserves}
        curr_reserves = {item.id for item in current.resource_state.available_reserves}
        for reserve_id in sorted(curr_reserves - prev_reserves):
            changes.append(f"reserve_{reserve_id}_became_available")
        prev_sector_threat = {item.sector_id: item.enemy_threat for item in previous.sector_summary}
        for sector in current.sector_summary:
            if prev_sector_threat.get(sector.sector_id) not in {None, sector.enemy_threat}:
                changes.append(
                    f"sector_{sector.sector_id}_threat_{prev_sector_threat[sector.sector_id]}_to_{sector.enemy_threat}"
                )
        return tuple(changes[:6])

    def _requests_for_decision(self, observation: CommanderObservation) -> tuple[str, ...]:
        requests: list[str] = []
        high_threat_sectors = [sector for sector in observation.sector_summary if sector.enemy_threat in {"high", "medium"}]
        available_reserves = list(observation.resource_state.available_reserves)
        if available_reserves and high_threat_sectors:
            requests.append(
                f"decide_whether_to_deploy_{available_reserves[0].id}_to_{high_threat_sectors[0].sector_id}"
            )
        threatened_high_value = [
            force for force in observation.friendly_forces if force.high_value and force.assigned_sector in {sector.sector_id for sector in high_threat_sectors}
        ]
        if threatened_high_value:
            requests.append(f"decide_whether_to_reposition_{threatened_high_value[0].force_id}")
        if high_threat_sectors:
            requests.append(f"decide_whether_to_adjust_priority_{high_threat_sectors[0].sector_id}")
        if not requests:
            requests.append("decide_whether_to_hold_current_plan")
        return tuple(requests[:3])

    def _owner_status(self, owner: Coalition | None, coalition: Coalition) -> str:
        if owner is None:
            return "unowned"
        if owner is coalition:
            return "friendly"
        return "enemy"

    def _sector_control_status(self, control_points, coalition: Coalition) -> str:
        if not control_points:
            return "unowned"
        owners = {point.owner for point in control_points}
        if owners == {coalition}:
            return "friendly"
        if len(owners) > 1 or None in owners:
            return "contested"
        return "enemy"

    def _band_from_count(self, count: int, high_value_count: int = 0) -> str:
        score = count + high_value_count
        if score >= 3:
            return "high"
        if score >= 1:
            return "medium"
        return "low"

    def _max_confidence(self, tracks) -> str:
        if any(track.confidence is ConfidenceBand.HIGH for track in tracks):
            return "high"
        if any(track.confidence is ConfidenceBand.MEDIUM for track in tracks):
            return "medium"
        if tracks:
            return "low"
        return "none"

    def _activity_level(self, evidence_records, sector_id: str) -> str:
        count = sum(1 for evidence in evidence_records if evidence.sector_id == sector_id)
        if count >= 3:
            return "active"
        if count >= 1:
            return "watchful"
        return "quiet"

    def _strategic_importance(self, sector_id: str) -> str:
        sector = next(item for item in self.scenario.sectors if item.id == sector_id)
        if any(tag in sector.tags for tag in ("airbase", "frontline", "contested", "air_corridor")):
            return "high"
        if any(control_point.sector_id == sector_id and control_point.strategic_value == "high" for control_point in self.scenario.control_points):
            return "high"
        if sector.role in {"front", "contested"}:
            return "medium"
        return "low"

    def _threat_estimate(self, classification: str | None, confidence: ConfidenceBand) -> str:
        lower = (classification or "").lower()
        if any(token in lower for token in ("sam", "air_defense", "fires", "radar")):
            return "high"
        if confidence is ConfidenceBand.HIGH:
            return "medium"
        return "low"

    def _jsonable(self, payload: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(payload, default=str))


__all__ = ["ObservationBuilder"]
