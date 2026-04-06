"""Observation builder and renderer services for Phase 1."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import base64

from dcs_dungeon_master.core.config import AirOpsConfig, MultimodalConfig
from dcs_dungeon_master.core.enums import Coalition, ConfidenceBand
from dcs_dungeon_master.core.models import (
    AirPackageState,
    CommanderObservation,
    CommanderStateView,
    EnemyContactEntry,
    FriendlyForceEntry,
    KnownControlPointView,
    KnownSectorView,
    ObservationArtifact,
    ObservationAttachment,
    ObservationAttachmentArtifact,
    ObservationMeta,
    ReserveAvailabilityView,
    ResourceStateView,
    ScenarioDefinition,
    ScenarioStateView,
    SectorSummaryEntry,
)
from dcs_dungeon_master.core.versions import OBSERVATION_SCHEMA_VERSION
from dcs_dungeon_master.map_assets import MapAssetService, TheaterAssetBundle
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.terrain import TerrainService


@dataclass(slots=True)
class ObservationBuilder:
    store: SQLiteStateStore
    scenario: ScenarioDefinition
    sensor_fusion: SensorFusionService
    multimodal: MultimodalConfig = MultimodalConfig()
    map_asset_service: MapAssetService | None = None
    terrain_service: TerrainService | None = None
    air_ops: AirOpsConfig = AirOpsConfig()

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
        submit_multimodal: bool = False,
    ) -> ObservationArtifact:
        world = self.store.get_world_state_snapshot(run_id)
        generated_at = now or world.last_ingest_at or datetime.now(UTC)
        if fusion_result is None:
            knowledge_result = self.sensor_fusion.preview(run_id, now=generated_at)
            fusion_update_id = self.store.save_fusion_update(run_id, knowledge_result) if persist else None
        else:
            knowledge_result = fusion_result
            fusion_update_id = self.store.get_latest_fusion_update_id(run_id) if persist else None
        knowledge = next(item for item in knowledge_result.knowledge_states if item.coalition is coalition)
        coalition_state = next(item for item in self.store.get_coalition_states(run_id) if item.coalition is coalition)
        active_groups = tuple(item for item in self.store.get_active_groups(run_id) if item.coalition is coalition)
        reserve_groups = self.store.get_reserve_groups(run_id, coalition)
        air_packages = self.store.list_air_packages(run_id, coalition)
        previous_artifact = self.store.get_previous_observation(run_id, coalition, decision_cycle)
        asset_bundle = self.map_asset_service.bundle_for_theater(self.scenario.theater) if self.map_asset_service is not None else None
        terrain_summary = self.terrain_service.sector_terrain_summary(asset_bundle, self.scenario.sectors) if self.terrain_service is not None else ()
        landmarks = self._combined_landmarks(asset_bundle)
        map_context = self._build_map_context(asset_bundle, coalition)

        sector_summary = self._build_sector_summary(world, knowledge, coalition, previous_artifact)
        base_observation = CommanderObservation(
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
            resource_state=self._build_resource_state(run_id, coalition_state, reserve_groups, coalition),
            sector_summary=sector_summary,
            friendly_forces=self._build_friendly_forces(active_groups, world),
            enemy_contacts=self._build_enemy_contacts(knowledge),
            air_packages=tuple(self._serialize_air_package(item) for item in air_packages),
            map_context=map_context,
            terrain_summary=terrain_summary,
            landmarks=landmarks,
            recent_changes=(),
            standing_orders=tuple(order.text for order in coalition_state.standing_orders if order.active),
            requests_for_decision=(),
            attachments=(),
        )
        observation = self._with_recent_changes_and_requests(base_observation, previous_artifact)
        attachment_artifacts = self._build_attachment_artifacts(
            run_id,
            coalition,
            decision_cycle,
            observation,
            submit_to_backend=submit_multimodal,
        )
        observation = CommanderObservation(
            meta=observation.meta,
            commander_state=observation.commander_state,
            scenario_state=observation.scenario_state,
            resource_state=observation.resource_state,
            sector_summary=observation.sector_summary,
            friendly_forces=observation.friendly_forces,
            enemy_contacts=observation.enemy_contacts,
            air_packages=observation.air_packages,
            map_context=observation.map_context,
            terrain_summary=observation.terrain_summary,
            landmarks=observation.landmarks,
            recent_changes=observation.recent_changes,
            standing_orders=observation.standing_orders,
            requests_for_decision=observation.requests_for_decision,
            attachments=tuple(
                ObservationAttachment(
                    attachment_id=item.attachment_id,
                    media_type=item.media_type,
                    role=item.role,
                    description=f"Coalition-filtered {item.role.replace('_', ' ')}",
                    uri=item.file_path,
                    source="generated",
                    coalition_filtered=True,
                    metadata=item.metadata | {"submitted_to_backend": item.submitted_to_backend},
                )
                for item in attachment_artifacts
            ),
        )
        narrative = self.render_narrative(observation)
        artifact = ObservationArtifact(
            id=None,
            run_id=run_id,
            coalition=coalition,
            decision_cycle=decision_cycle,
            generated_at=generated_at,
            previous_observation_id=previous_artifact.id if previous_artifact else None,
            fusion_update_id=fusion_update_id,
            observation=observation,
            narrative=narrative,
            attachment_artifacts=attachment_artifacts,
        )
        if persist:
            artifact = ObservationArtifact(
                id=self.store.save_observation_artifact(artifact),
                run_id=artifact.run_id,
                coalition=artifact.coalition,
                decision_cycle=artifact.decision_cycle,
                generated_at=artifact.generated_at,
                previous_observation_id=artifact.previous_observation_id,
                fusion_update_id=artifact.fusion_update_id,
                observation=artifact.observation,
                narrative=artifact.narrative,
                attachment_artifacts=artifact.attachment_artifacts,
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
        multimodal_submission: dict[Coalition, bool] | None = None,
    ) -> tuple[ObservationArtifact, ObservationArtifact]:
        world = self.store.get_world_state_snapshot(run_id)
        generated_at = now or world.last_ingest_at or datetime.now(UTC)
        fusion_result = self.sensor_fusion.preview(run_id, now=generated_at)
        if persist:
            self.store.save_fusion_update(run_id, fusion_result)
        return (
            self.build_observation(
                run_id,
                Coalition.RED,
                decision_cycle,
                now=generated_at,
                seconds_since_last_cycle=seconds_since_last_cycle,
                persist=persist,
                fusion_result=fusion_result,
                submit_multimodal=(multimodal_submission or {}).get(Coalition.RED, False),
            ),
            self.build_observation(
                run_id,
                Coalition.BLUE,
                decision_cycle,
                now=generated_at,
                seconds_since_last_cycle=seconds_since_last_cycle,
                persist=persist,
                fusion_result=fusion_result,
                submit_multimodal=(multimodal_submission or {}).get(Coalition.BLUE, False),
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
            "attachment_count": len(observation.attachments),
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

    def _build_resource_state(self, run_id: str, coalition_state, reserve_groups, coalition: Coalition) -> ResourceStateView:
        air_package_inventories = tuple(
            {
                "id": inventory.id,
                "origin_control_point_id": inventory.origin_control_point_id,
                "aircraft_type": inventory.aircraft_type,
                "aircraft_category": inventory.aircraft_category,
                "available_count": inventory.available_count,
                "package_types": inventory.package_types,
                "default_altitude_ft_msl": inventory.default_altitude_ft_msl,
            }
            for inventory in self.store.get_air_package_inventories(run_id, coalition)
        )
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
            air_package_inventories=air_package_inventories,
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
                    category=world_group.category if world_group else None,
                    control_point_id=group.control_point_id,
                    altitude_ft_msl=world_group.alt if world_group else None,
                    heading_deg=world_group.heading if world_group else None,
                    speed_kts=world_group.speed if world_group else None,
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
            air_packages=observation.air_packages,
            map_context=observation.map_context,
            terrain_summary=observation.terrain_summary,
            landmarks=observation.landmarks,
            recent_changes=recent_changes,
            standing_orders=observation.standing_orders,
            requests_for_decision=requests,
            attachments=observation.attachments,
        )

    def _build_attachment_artifacts(
        self,
        run_id: str,
        coalition: Coalition,
        decision_cycle: int,
        observation: CommanderObservation,
        *,
        submit_to_backend: bool,
    ) -> tuple[ObservationAttachmentArtifact, ...]:
        if not self.multimodal.enabled:
            return ()
        output_dir = Path(self.multimodal.output_dir) / run_id / coalition.value
        output_dir.mkdir(parents=True, exist_ok=True)
        attachments: list[ObservationAttachmentArtifact] = []
        overlay_path = output_dir / f"cycle_{decision_cycle:04d}_map_overlay.svg"
        overlay_path.write_text(self._render_overlay_svg(observation), encoding="utf-8")
        attachments.append(
            ObservationAttachmentArtifact(
                attachment_id=f"{coalition.value}_map_overlay_cycle_{decision_cycle}",
                run_id=run_id,
                coalition=coalition,
                decision_cycle=decision_cycle,
                media_type="image/svg+xml",
                role="map_overlay",
                file_path=str(overlay_path),
                submitted_to_backend=submit_to_backend,
                metadata={
                    "multimodal_enabled": True,
                    "coalition": coalition.value,
                    "phase": "image_only",
                },
            )
        )
        asset_bundle = self.map_asset_service.bundle_for_theater(self.scenario.theater) if self.map_asset_service is not None else None
        if asset_bundle is not None:
            theater_path = output_dir / f"cycle_{decision_cycle:04d}_theater_map.svg"
            theater_path.write_text(
                self._render_basemap_svg(observation, asset_bundle, title=f"{coalition.value.upper()} theater context"),
                encoding="utf-8",
            )
            attachments.append(
                ObservationAttachmentArtifact(
                    attachment_id=f"{coalition.value}_theater_map_cycle_{decision_cycle}",
                    run_id=run_id,
                    coalition=coalition,
                    decision_cycle=decision_cycle,
                    media_type="image/svg+xml",
                    role="map_theater_context",
                    file_path=str(theater_path),
                    submitted_to_backend=submit_to_backend,
                    metadata={"multimodal_enabled": True, "coalition": coalition.value, "phase": "image_plus_structured"},
                )
            )
            front_bbox = self._front_focus_bbox()
            if front_bbox is not None:
                front_path = output_dir / f"cycle_{decision_cycle:04d}_front_aoi.svg"
                front_path.write_text(
                    self._render_basemap_svg(
                        observation,
                        asset_bundle,
                        focus_bbox=front_bbox,
                        title=f"{coalition.value.upper()} front AOI",
                    ),
                    encoding="utf-8",
                )
                attachments.append(
                    ObservationAttachmentArtifact(
                        attachment_id=f"{coalition.value}_front_aoi_cycle_{decision_cycle}",
                        run_id=run_id,
                        coalition=coalition,
                        decision_cycle=decision_cycle,
                        media_type="image/svg+xml",
                        role="map_front_aoi",
                        file_path=str(front_path),
                        submitted_to_backend=submit_to_backend,
                        metadata={"multimodal_enabled": True, "coalition": coalition.value, "phase": "image_plus_structured"},
                    )
                )
        return tuple(attachments)

    def _render_overlay_svg(self, observation: CommanderObservation) -> str:
        sectors = sorted(self.scenario.sectors, key=lambda item: item.id)
        width = 640
        height = 420
        sector_x = {sector.id: 80 + index * max(1, int((width - 160) / max(len(sectors) - 1, 1))) for index, sector in enumerate(sectors)}
        sector_y = {
            sector.id: {"rear": 310, "support": 250, "front": 180, "contested": 110, "air_corridor": 70}.get(sector.role, 220)
            for sector in sectors
        }
        sector_summary = {item.sector_id: item for item in observation.sector_summary}
        circles = []
        labels = []
        for sector in sectors:
            summary = sector_summary.get(sector.id)
            fill = {
                "friendly": "#4e9a51",
                "enemy": "#c84f4f",
                "contested": "#d89b2b",
            }.get(summary.control_status if summary else "unknown", "#6b7280")
            circles.append(
                f'<circle cx="{sector_x[sector.id]}" cy="{sector_y[sector.id]}" r="30" fill="{fill}" opacity="0.75" />'
            )
            labels.append(
                f'<text x="{sector_x[sector.id]}" y="{sector_y[sector.id] + 52}" text-anchor="middle" font-size="12">{sector.name}</text>'
            )
        contacts = []
        for contact in observation.enemy_contacts:
            sector_id = contact.estimated_sector or contact.last_known_sector
            if sector_id not in sector_x:
                continue
            contacts.append(
                f'<rect x="{sector_x[sector_id] - 8}" y="{sector_y[sector_id] - 8}" width="16" height="16" fill="#111827" />'
            )
        friendlies = []
        for force in observation.friendly_forces:
            sector_id = force.assigned_sector
            if sector_id not in sector_x:
                continue
            friendlies.append(
                f'<circle cx="{sector_x[sector_id]}" cy="{sector_y[sector_id] + 14}" r="6" fill="#f9fafb" stroke="#111827" stroke-width="1" />'
            )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            '<rect width="100%" height="100%" fill="#f3f4f6" />'
            f'<text x="24" y="28" font-size="18">Coalition overlay: {observation.meta.coalition.value}</text>'
            f'{"".join(circles)}{"".join(friendlies)}{"".join(contacts)}{"".join(labels)}'
            "</svg>"
        )

    def _build_map_context(self, asset_bundle: TheaterAssetBundle | None, coalition: Coalition) -> dict[str, Any]:
        basemap = None
        if asset_bundle is not None and self.map_asset_service is not None:
            basemap_path = asset_bundle.basemap_manifest_path.parent / str(asset_bundle.basemap.get("image_path", ""))
            basemap = {
                **asset_bundle.basemap,
                "image_url": self.map_asset_service.public_url_for_path(basemap_path),
                "image_path": str(basemap_path),
            }
        return {
            "theater": self.scenario.theater,
            "coalition": coalition.value,
            "terrain_available": asset_bundle is not None and self.terrain_service is not None,
            "basemap": basemap,
            "recommended_image_roles": ["map_theater_context", "map_front_aoi", "map_overlay"],
            "water_context": [
                landmark["name"]
                for landmark in self._combined_landmarks(asset_bundle)
                if "water" in set(landmark.get("tags", ()))
            ][:3],
        }

    def _combined_landmarks(self, asset_bundle: TheaterAssetBundle | None) -> tuple[dict[str, Any], ...]:
        scenario_landmarks = tuple(
            {
                "id": landmark.id,
                "name": landmark.name,
                "lat": landmark.lat,
                "lng": landmark.lng,
                "tags": landmark.tags,
                "source": "scenario",
            }
            for landmark in self.scenario.landmarks
        )
        if asset_bundle is None:
            return scenario_landmarks
        scenario_ids = {landmark.id for landmark in self.scenario.landmarks}
        return scenario_landmarks + tuple(
            {
                **item,
                "source": item.get("source", "theater_asset"),
            }
            for item in asset_bundle.landmarks
            if str(item.get("id")) not in scenario_ids
        )

    @staticmethod
    def _serialize_air_package(package: AirPackageState) -> dict[str, Any]:
        return {
            "package_id": package.package_id,
            "package_type": package.package_type,
            "aircraft_type": package.aircraft_type,
            "aircraft_category": package.aircraft_category,
            "aircraft_count": package.aircraft_count,
            "origin_control_point_id": package.origin_control_point_id,
            "status": package.status,
            "posture": package.posture,
            "roe": package.roe,
            "target_reference_type": package.target_reference_type,
            "target_reference_id": package.target_reference_id,
            "current_sector_id": package.current_sector_id,
            "route_legs": tuple(asdict(leg) for leg in package.route_legs),
            "normalized_route_legs": tuple(asdict(leg) for leg in package.normalized_route_legs),
            "metadata": package.metadata,
        }

    def _render_basemap_svg(
        self,
        observation: CommanderObservation,
        asset_bundle: TheaterAssetBundle,
        *,
        focus_bbox: tuple[float, float, float, float] | None = None,
        title: str,
    ) -> str:
        width = 900
        height = 560
        basemap_path = asset_bundle.basemap_manifest_path.parent / str(asset_bundle.basemap.get("image_path", ""))
        media_type = str(asset_bundle.basemap.get("media_type", "image/svg+xml"))
        encoded_map = base64.b64encode(basemap_path.read_bytes()).decode("ascii")
        bounds = asset_bundle.basemap.get("bounds", {})
        west = float(bounds.get("west"))
        east = float(bounds.get("east"))
        south = float(bounds.get("south"))
        north = float(bounds.get("north"))
        if focus_bbox is not None:
            south, west, north, east = focus_bbox
        def project(lat: float, lng: float) -> tuple[float, float]:
            x = ((lng - west) / max(east - west, 0.001)) * width
            y = (1 - ((lat - south) / max(north - south, 0.001))) * height
            return (x, y)
        sector_summary = {item.sector_id: item for item in observation.sector_summary}
        sectors = []
        labels = []
        for sector in self.scenario.sectors:
            if sector.center_lat is None or sector.center_lng is None:
                continue
            x, y = project(sector.center_lat, sector.center_lng)
            summary = sector_summary.get(sector.id)
            fill = {
                "friendly": "#4e9a51",
                "enemy": "#c84f4f",
                "contested": "#d89b2b",
            }.get(summary.control_status if summary else "unknown", "#6b7280")
            sectors.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="20" fill="{fill}" opacity="0.45" stroke="#111827" stroke-width="1" />')
            labels.append(f'<text x="{x:.1f}" y="{y + 30:.1f}" text-anchor="middle" font-size="12" fill="#111827">{sector.name}</text>')
        contacts = []
        for contact in observation.enemy_contacts:
            sector_id = contact.estimated_sector or contact.last_known_sector
            sector = next((item for item in self.scenario.sectors if item.id == sector_id), None)
            if sector is None or sector.center_lat is None or sector.center_lng is None:
                continue
            x, y = project(sector.center_lat, sector.center_lng)
            contacts.append(f'<rect x="{x - 7:.1f}" y="{y - 7:.1f}" width="14" height="14" fill="#111827" />')
        friendlies = []
        for force in observation.friendly_forces:
            sector = next((item for item in self.scenario.sectors if item.id == force.assigned_sector), None)
            if sector is None or sector.center_lat is None or sector.center_lng is None:
                continue
            x, y = project(sector.center_lat, sector.center_lng)
            friendlies.append(f'<circle cx="{x:.1f}" cy="{y + 12:.1f}" r="6" fill="#f9fafb" stroke="#111827" stroke-width="1" />')
        landmarks = []
        for landmark in observation.landmarks[:10]:
            lat = landmark.get("lat")
            lng = landmark.get("lng")
            if not isinstance(lat, int | float) or not isinstance(lng, int | float):
                continue
            x, y = project(float(lat), float(lng))
            landmarks.append(f'<path d="M {x:.1f} {y - 10:.1f} L {x - 6:.1f} {y + 4:.1f} L {x + 6:.1f} {y + 4:.1f} Z" fill="#2563eb" />')
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" fill="#e5e7eb" />'
            f'<image href="data:{media_type};base64,{encoded_map}" x="0" y="0" width="{width}" height="{height}" preserveAspectRatio="none" />'
            f'<rect x="0" y="0" width="{width}" height="42" fill="rgba(255,255,255,0.8)" />'
            f'<text x="24" y="28" font-size="20" fill="#111827">{title}</text>'
            f'{"".join(sectors)}{"".join(friendlies)}{"".join(contacts)}{"".join(landmarks)}{"".join(labels)}'
            "</svg>"
        )

    def _front_focus_bbox(self) -> tuple[float, float, float, float] | None:
        front_sectors = [
            sector for sector in self.scenario.sectors
            if sector.role in {"front", "contested", "air_corridor"} and sector.center_lat is not None and sector.center_lng is not None
        ]
        if not front_sectors:
            return None
        lats = [float(sector.center_lat) for sector in front_sectors]
        lngs = [float(sector.center_lng) for sector in front_sectors]
        return (min(lats) - 0.6, min(lngs) - 0.8, max(lats) + 0.6, max(lngs) + 0.8)

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
