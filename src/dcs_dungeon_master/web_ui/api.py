"""JSON API service for Campaign Studio and Live Ops."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any

from dcs_dungeon_master.app import DEFAULT_CONFIG_PATH
from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import Coalition
from dcs_dungeon_master.core.exceptions import PersistenceError, ScenarioNotFoundError
from dcs_dungeon_master.core.models import (
    CampaignControlPointView,
    CampaignSectorView,
    CommanderForcePolicyView,
    CommanderPreviewSnapshot,
    LiveOpsSnapshot,
    OperatorMapLayerSet,
    ScenarioDraftPatch,
)
from dcs_dungeon_master.operator_control import OperatorControlService
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition, list_scenarios, scenario_definition_to_dict
from dcs_dungeon_master.web_ui.pydcs_reference import PydcsReferenceService
from dcs_dungeon_master.web_ui.scenario_drafts import ScenarioDraftService


def _json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


@dataclass(slots=True)
class WebUiService:
    store: SQLiteStateStore
    operator: OperatorControlService
    draft_service: ScenarioDraftService
    registry_path: Path

    @classmethod
    def from_config_path(cls, config_path: str | Path = DEFAULT_CONFIG_PATH) -> "WebUiService":
        config = load_config(config_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        store.initialize_schema()
        reference_service = PydcsReferenceService()
        draft_service = ScenarioDraftService(store, index_path=config.scenario.registry_path, reference_service=reference_service)
        operator = OperatorControlService(store)
        return cls(store=store, operator=operator, draft_service=draft_service, registry_path=Path(config.scenario.registry_path))

    @property
    def status(self) -> str:
        return "web-ui-api-ready"

    def list_scenarios(self) -> dict[str, Any]:
        drafts_by_source: dict[str, int] = {}
        for draft in self.draft_service.list_drafts():
            drafts_by_source[draft.source_scenario_id] = drafts_by_source.get(draft.source_scenario_id, 0) + 1
        return {
            "scenarios": [
                {
                    "id": entry.id,
                    "name": entry.name,
                    "theater": entry.theater,
                    "summary": entry.summary,
                    "path": str(entry.path),
                    "draft_count": drafts_by_source.get(entry.id, 0),
                }
                for entry in self.draft_service.list_scenarios()
            ],
            "drafts": [_json_ready(item) for item in self.draft_service.list_drafts()],
        }

    def get_scenario(self, scenario_id: str) -> dict[str, Any]:
        scenario = get_scenario_definition(scenario_id, self.registry_path)
        return {
            "scenario": scenario_definition_to_dict(scenario),
            "map_reference": _json_ready(self.draft_service.reference_service.reference_for_scenario(scenario)),
            "sectors": _json_ready(self._campaign_sector_views(scenario)),
            "control_points": _json_ready(self._campaign_control_point_views(scenario)),
            "force_policies": _json_ready(self._force_policy_views(scenario)),
        }

    def create_scenario_draft(self, source_scenario_id: str) -> dict[str, Any]:
        return {"draft": _json_ready(self.draft_service.create_draft(source_scenario_id))}

    def list_scenario_drafts(self) -> dict[str, Any]:
        return {"drafts": _json_ready(self.draft_service.list_drafts())}

    def get_scenario_draft(self, draft_id: str) -> dict[str, Any]:
        return {"draft": _json_ready(self.draft_service.get_draft(draft_id))}

    def update_scenario_draft(self, draft_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        patch = ScenarioDraftPatch(
            scenario=payload.get("scenario", payload),
            pydcs_mapping=payload.get("pydcs_mapping", {}),
        )
        return {"draft": _json_ready(self.draft_service.update_draft(draft_id, patch))}

    def validate_scenario_draft(self, draft_id: str) -> dict[str, Any]:
        return self.draft_service.validate_draft(draft_id)

    def rename_scenario_draft(self, draft_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        display_name = payload.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise PersistenceError("display_name is required to rename a scenario draft.")
        return {"draft": _json_ready(self.draft_service.rename_draft(draft_id, display_name))}

    def duplicate_scenario_draft(self, draft_id: str) -> dict[str, Any]:
        return {"draft": _json_ready(self.draft_service.duplicate_draft(draft_id))}

    def revert_scenario_draft(self, draft_id: str) -> dict[str, Any]:
        return {"draft": _json_ready(self.draft_service.revert_draft(draft_id))}

    def delete_scenario_draft(self, draft_id: str) -> dict[str, Any]:
        return self.draft_service.delete_draft(draft_id)

    def save_scenario_draft(self, draft_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scenario_id = payload.get("scenario_id")
        name = payload.get("name")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise PersistenceError("scenario_id is required to save a scenario draft.")
        if not isinstance(name, str) or not name.strip():
            raise PersistenceError("name is required to save a scenario draft.")
        return self.draft_service.save_as_scenario(
            draft_id,
            scenario_id=scenario_id.strip(),
            name=name.strip(),
            overwrite=bool(payload.get("overwrite", False)),
        )

    def list_theaters(self) -> dict[str, Any]:
        by_theater: dict[str, dict[str, Any]] = {}
        for entry in list_scenarios(self.registry_path):
            bucket = by_theater.setdefault(entry.theater, {"theater_id": entry.theater, "scenario_ids": [], "count": 0})
            bucket["scenario_ids"].append(entry.id)
            bucket["count"] += 1
        return {"theaters": tuple(by_theater[theater] for theater in sorted(by_theater))}

    def get_theater_reference(self, theater_id: str) -> dict[str, Any]:
        entries = [entry for entry in list_scenarios(self.registry_path) if entry.theater == theater_id]
        if not entries:
            raise ScenarioNotFoundError(f"Unknown theater id: {theater_id}")
        scenario = get_scenario_definition(entries[0].id, self.registry_path)
        return {"reference": _json_ready(self.draft_service.reference_service.reference_for_scenario(scenario))}

    def list_runs(self) -> dict[str, Any]:
        runs = []
        for state in self.store.list_run_control_states():
            latest_cycle = 0
            try:
                latest_cycle = self.operator.summarize_run(state.run_id).latest_decision_cycle
            except PersistenceError:
                latest_cycle = 0
            runs.append(
                {
                    "run_id": state.run_id,
                    "scenario_id": state.scenario_id,
                    "scenario_name": state.scenario_name,
                    "theater": state.theater,
                    "mode": state.mode,
                    "status": state.status.value,
                    "created_at": state.created_at,
                    "latest_decision_cycle": latest_cycle,
                    "red_backend_name": state.red_backend_name,
                    "blue_backend_name": state.blue_backend_name,
                }
            )
        return {"runs": _json_ready(runs)}

    def get_run_status(self, run_id: str) -> dict[str, Any]:
        return {"run": _json_ready(self.operator.get_status(run_id))}

    def transition_run(self, run_id: str, action: str) -> dict[str, Any]:
        if action == "start":
            result = self.operator.start_run(run_id)
        elif action == "pause":
            result = self.operator.pause_run(run_id)
        elif action == "resume":
            result = self.operator.resume_run(run_id)
        elif action == "stop":
            result = self.operator.stop_run(run_id)
        else:
            raise PersistenceError(f"Unsupported run action: {action}")
        return {"result": _json_ready(result)}

    def get_run_summary(self, run_id: str) -> dict[str, Any]:
        return {"summary": _json_ready(self.operator.summarize_run(run_id))}

    def get_run_timeline(self, run_id: str) -> dict[str, Any]:
        return {"timeline": _json_ready(self.operator.list_timeline(run_id))}

    def get_live_ops_snapshot(self, run_id: str) -> dict[str, Any]:
        run = self.operator.get_status(run_id)
        summary = self.operator.summarize_run(run_id)
        timeline = self.operator.list_timeline(run_id)
        red_inspection = self.operator.inspect_coalition(run_id, Coalition.RED)
        blue_inspection = self.operator.inspect_coalition(run_id, Coalition.BLUE)
        world = self.store.get_world_state_snapshot(run_id)
        red_knowledge = self._optional(lambda: self.store.get_knowledge_state(Coalition.RED, run_id))
        blue_knowledge = self._optional(lambda: self.store.get_knowledge_state(Coalition.BLUE, run_id))
        operator_layers = self._operator_map_layers(run_id)
        snapshot = LiveOpsSnapshot(
            run=run,
            summary=summary,
            timeline=timeline,
            operator_map_layers=operator_layers,
            red_inspection=red_inspection,
            blue_inspection=blue_inspection,
            red_knowledge=red_knowledge,
            blue_knowledge=blue_knowledge,
            world_state=world,
        )
        return {"ops": _json_ready(snapshot)}

    def get_commander_preview(self, run_id: str, coalition: Coalition) -> dict[str, Any]:
        artifact = self.store.get_latest_observation(run_id, coalition)
        overlay = next((item.uri for item in artifact.observation.attachments if item.role == "map_overlay"), None)
        preview = CommanderPreviewSnapshot(
            run_id=run_id,
            coalition=coalition,
            observation_id=artifact.id or 0,
            decision_cycle=artifact.decision_cycle,
            generated_at=artifact.generated_at,
            narrative=artifact.narrative,
            observation=artifact.observation,
            map_overlay_uri=overlay,
        )
        return {"preview": _json_ready(preview)}

    def get_fusion(self, run_id: str, coalition: Coalition) -> dict[str, Any]:
        return {"fusion": _json_ready(self.store.get_knowledge_state(coalition, run_id))}

    def get_world_state(self, run_id: str) -> dict[str, Any]:
        return {"world_state": _json_ready(self.store.get_world_state_snapshot(run_id))}

    def get_execution(self, run_id: str) -> dict[str, Any]:
        return {"execution_batches": _json_ready(self.store.list_execution_batches(run_id))}

    def get_validation(self, run_id: str) -> dict[str, Any]:
        return {"validation_batches": _json_ready(self.store.list_action_validation_batches(run_id))}

    def get_model_invocations(self, run_id: str) -> dict[str, Any]:
        return {"model_invocations": _json_ready(self.store.list_model_invocations(run_id))}

    def dispatch(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        body = body or {}
        segments = [segment for segment in path.strip("/").split("/") if segment]
        if not segments or segments[0] != "api":
            return (404, {"error": "Unknown route."})
        try:
            if method == "GET" and segments == ["api", "scenarios", "drafts"]:
                return (200, self.list_scenario_drafts())
            if method == "GET" and segments == ["api", "scenarios"]:
                return (200, self.list_scenarios())
            if method == "POST" and segments == ["api", "scenarios", "drafts"]:
                return (200, self.create_scenario_draft(str(body.get("source_scenario_id", ""))))
            if method == "GET" and len(segments) == 4 and segments[1:3] == ["scenarios", "drafts"]:
                return (200, self.get_scenario_draft(segments[3]))
            if method == "PUT" and len(segments) == 4 and segments[1:3] == ["scenarios", "drafts"]:
                return (200, self.update_scenario_draft(segments[3], body))
            if method == "PATCH" and len(segments) == 5 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "rename":
                return (200, self.rename_scenario_draft(segments[3], body))
            if method == "POST" and len(segments) == 5 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "duplicate":
                return (200, self.duplicate_scenario_draft(segments[3]))
            if method == "POST" and len(segments) == 5 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "revert":
                return (200, self.revert_scenario_draft(segments[3]))
            if method == "DELETE" and len(segments) == 4 and segments[1:3] == ["scenarios", "drafts"]:
                return (200, self.delete_scenario_draft(segments[3]))
            if method == "POST" and len(segments) == 5 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "validate":
                return (200, self.validate_scenario_draft(segments[3]))
            if method == "POST" and len(segments) == 5 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "save-as":
                return (200, self.save_scenario_draft(segments[3], body))
            if method == "GET" and len(segments) == 3 and segments[1] == "scenarios":
                return (200, self.get_scenario(segments[2]))
            if method == "GET" and segments == ["api", "theaters"]:
                return (200, self.list_theaters())
            if method == "GET" and len(segments) == 4 and segments[1] == "theaters" and segments[3] == "reference":
                return (200, self.get_theater_reference(segments[2]))
            if method == "GET" and segments == ["api", "runs"]:
                return (200, self.list_runs())
            if len(segments) >= 4 and segments[1] == "runs":
                run_id = segments[2]
                tail = segments[3:]
                if method == "GET" and tail == ["status"]:
                    return (200, self.get_run_status(run_id))
                if method == "POST" and tail in (["start"], ["pause"], ["resume"], ["stop"]):
                    return (200, self.transition_run(run_id, tail[0]))
                if method == "GET" and tail == ["summary"]:
                    return (200, self.get_run_summary(run_id))
                if method == "GET" and tail == ["timeline"]:
                    return (200, self.get_run_timeline(run_id))
                if method == "GET" and tail == ["ops"]:
                    return (200, self.get_live_ops_snapshot(run_id))
                if method == "GET" and len(tail) == 2 and tail[0] == "commander-preview":
                    return (200, self.get_commander_preview(run_id, Coalition(tail[1])))
                if method == "GET" and len(tail) == 2 and tail[0] == "fusion":
                    return (200, self.get_fusion(run_id, Coalition(tail[1])))
                if method == "GET" and tail == ["world-state"]:
                    return (200, self.get_world_state(run_id))
                if method == "GET" and tail == ["execution"]:
                    return (200, self.get_execution(run_id))
                if method == "GET" and tail == ["validation"]:
                    return (200, self.get_validation(run_id))
                if method == "GET" and tail == ["model-invocations"]:
                    return (200, self.get_model_invocations(run_id))
        except (PersistenceError, ScenarioNotFoundError, ValueError) as exc:
            return (400, {"error": str(exc)})
        return (404, {"error": "Unknown route."})

    def _campaign_sector_views(self, scenario) -> tuple[CampaignSectorView, ...]:
        force_ids_by_sector: dict[str, list[str]] = {}
        for group in scenario.active_groups:
            if group.sector_id:
                force_ids_by_sector.setdefault(group.sector_id, []).append(group.id)
        intended_owner_by_sector: dict[str, str | None] = {}
        for control_point in scenario.control_points:
            if control_point.sector_id not in intended_owner_by_sector and control_point.owner is not None:
                intended_owner_by_sector[control_point.sector_id] = control_point.owner.value
        return tuple(
            CampaignSectorView(
                id=sector.id,
                name=sector.name,
                role=sector.role,
                center_lat=sector.center_lat,
                center_lng=sector.center_lng,
                radius_nm=sector.radius_nm,
                neighbor_ids=sector.neighbor_ids,
                tags=sector.tags,
                intended_owner=intended_owner_by_sector.get(sector.id),
                assigned_force_ids=tuple(sorted(force_ids_by_sector.get(sector.id, ()))),
            )
            for sector in scenario.sectors
        )

    def _campaign_control_point_views(self, scenario) -> tuple[CampaignControlPointView, ...]:
        force_ids_by_control: dict[str, list[str]] = {}
        for group in scenario.active_groups:
            if group.control_point_id:
                force_ids_by_control.setdefault(group.control_point_id, []).append(group.id)
        return tuple(
            CampaignControlPointView(
                id=control_point.id,
                name=control_point.name,
                sector_id=control_point.sector_id,
                kind=control_point.kind,
                owner=control_point.owner.value if control_point.owner is not None else None,
                strategic_value=control_point.strategic_value,
                lat=control_point.lat,
                lng=control_point.lng,
                assigned_force_ids=tuple(sorted(force_ids_by_control.get(control_point.id, ()))),
            )
            for control_point in scenario.control_points
        )

    def _force_policy_views(self, scenario) -> tuple[CommanderForcePolicyView, ...]:
        policies = []
        for coalition_state in scenario.coalitions:
            policies.append(
                CommanderForcePolicyView(
                    coalition=coalition_state.coalition,
                    budget_remaining=coalition_state.budget_remaining,
                    objectives=coalition_state.objectives,
                    active_groups=tuple(
                        {
                            "id": group.id,
                            "group_type": group.group_type,
                            "posture": group.posture.value,
                            "sector_id": group.sector_id,
                            "control_point_id": group.control_point_id,
                            "mobile": group.mobile,
                            "status": group.status,
                        }
                        for group in scenario.active_groups
                        if group.coalition is coalition_state.coalition
                    ),
                    reserve_groups=tuple(
                        {
                            "id": group.id,
                            "group_type": group.group_type,
                            "allowed_sector_ids": group.allowed_sector_ids,
                            "available": group.available,
                            "cost": group.cost,
                            "status": group.status,
                        }
                        for group in scenario.reserve_groups
                        if group.coalition is coalition_state.coalition
                    ),
                    deployment_restrictions=tuple(
                        {
                            "id": restriction.id,
                            "restriction_type": restriction.restriction_type,
                            "description": restriction.description,
                            "sector_ids": restriction.sector_ids,
                            "control_point_ids": restriction.control_point_ids,
                            "adjacency_limited": restriction.adjacency_limited,
                        }
                        for restriction in scenario.deployment_restrictions
                        if restriction.coalition in {None, coalition_state.coalition}
                    ),
                    standing_orders=tuple(order.text for order in coalition_state.standing_orders if order.active),
                )
            )
        return tuple(policies)

    def _operator_map_layers(self, run_id: str) -> OperatorMapLayerSet:
        world = self.store.get_world_state_snapshot(run_id)
        red_knowledge = self._optional(lambda: self.store.get_knowledge_state(Coalition.RED, run_id))
        blue_knowledge = self._optional(lambda: self.store.get_knowledge_state(Coalition.BLUE, run_id))
        latest_observations = self.store.list_observations(run_id)
        attachments = [
            {
                "observation_id": artifact.id,
                "coalition": artifact.coalition.value,
                "decision_cycle": artifact.decision_cycle,
                "attachments": [_json_ready(item) for item in artifact.observation.attachments],
            }
            for artifact in latest_observations[-2:]
        ]
        return OperatorMapLayerSet(
            sectors=tuple(
                {
                    "id": sector.id,
                    "name": sector.name,
                    "role": sector.role,
                    "center_lat": sector.center_lat,
                    "center_lng": sector.center_lng,
                    "radius_nm": sector.radius_nm,
                    "neighbor_ids": sector.neighbor_ids,
                }
                for sector in get_scenario_definition(self.operator.get_status(run_id).scenario_id, self.registry_path).sectors
            ),
            control_points=tuple(
                {
                    "control_point_id": point.control_point_id,
                    "owner": point.owner.value if point.owner is not None else None,
                    "sector_id": point.sector_id,
                    "source": point.source,
                }
                for point in world.control_points
            ),
            zones=tuple(
                {
                    "id": zone.id,
                    "name": zone.name,
                    "sector_id": zone.sector_id,
                    "center_lat": zone.center_lat,
                    "center_lng": zone.center_lng,
                    "radius_nm": zone.radius_nm,
                }
                for zone in get_scenario_definition(self.operator.get_status(run_id).scenario_id, self.registry_path).zones
            ),
            world_groups=tuple(
                {
                    "id": group.id,
                    "name": group.name,
                    "coalition": group.coalition.value if group.coalition is not None else None,
                    "group_type": group.group_type,
                    "sector_id": group.sector_id,
                    "lat": group.lat,
                    "lng": group.lng,
                    "high_value": group.high_value,
                }
                for group in world.groups
            ),
            red_knowledge_tracks=tuple(_json_ready(track) for track in (red_knowledge.contact_tracks if red_knowledge else ())),
            blue_knowledge_tracks=tuple(_json_ready(track) for track in (blue_knowledge.contact_tracks if blue_knowledge else ())),
            current_execution_notes=tuple(_json_ready(note) for note in self.store.list_current_execution_notes(run_id)),
            attachments=tuple(attachments),
        )

    def _optional(self, callback):
        try:
            return callback()
        except PersistenceError:
            return None
