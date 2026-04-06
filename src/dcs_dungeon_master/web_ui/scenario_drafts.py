"""Scenario draft persistence, validation, and save-as helpers for Campaign Studio."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from dcs_dungeon_master.core.enums import Coalition
from dcs_dungeon_master.core.exceptions import ConfigError, PersistenceError
from dcs_dungeon_master.core.models import (
    CoalitionState,
    MapReferenceLayer,
    ScenarioDefinition,
    ScenarioDraftCreateRequest,
    ScenarioDraftExportResult,
    ScenarioDraftPatch,
    ScenarioDraftView,
)
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import (
    DEFAULT_SCENARIO_INDEX_PATH,
    ScenarioRegistryEntry,
    get_scenario_definition,
    list_scenarios,
    load_registry,
    load_scenario_definition_data,
    scenario_definition_to_dict,
    serialize_registry,
    serialize_scenario_definition_toml,
)
from dcs_dungeon_master.web_ui.pydcs_reference import PydcsReferenceService

_BLANK_SOURCE_PREFIX = "__blank__:"


class ScenarioDraftService:
    """Draft workflow over the authored scenario package format."""

    def __init__(
        self,
        store: SQLiteStateStore,
        *,
        index_path: str | Path = DEFAULT_SCENARIO_INDEX_PATH,
        reference_service: PydcsReferenceService | None = None,
    ) -> None:
        self.store = store
        self.index_path = Path(index_path)
        self.reference_service = reference_service or PydcsReferenceService()

    def list_scenarios(self) -> tuple[ScenarioRegistryEntry, ...]:
        return list_scenarios(self.index_path)

    def create_draft(self, source_scenario_id: str) -> ScenarioDraftView:
        return self.create_draft_from_request(
            ScenarioDraftCreateRequest(mode="template", source_scenario_id=source_scenario_id)
        )

    def create_draft_from_request(self, request: ScenarioDraftCreateRequest) -> ScenarioDraftView:
        if request.mode == "template":
            if not request.source_scenario_id:
                raise PersistenceError("source_scenario_id is required for template draft creation.")
            scenario = get_scenario_definition(request.source_scenario_id, self.index_path)
            draft = ScenarioDraftView(
                draft_id=f"draft_{uuid4().hex[:12]}",
                source_scenario_id=request.source_scenario_id,
                source_scenario_name=scenario.name,
                scenario_id=scenario.id,
                name=scenario.name,
                display_name=f"{scenario.name} Draft",
                theater=scenario.theater,
                updated_at=datetime.now(UTC),
                scenario=scenario_definition_to_dict(scenario),
                dirty=False,
                map_reference=self.reference_service.reference_for_scenario(scenario),
                pydcs_mapping={},
            )
            self.store.save_scenario_draft(draft)
            return self._hydrate_draft(draft)

        if request.mode == "blank":
            return self.create_blank_draft(
                scenario_id=request.scenario_id,
                name=request.name,
                theater=request.theater,
                summary=request.summary,
                version=request.version,
            )

        raise PersistenceError(f"Unsupported draft creation mode: {request.mode}")

    def create_blank_draft(
        self,
        *,
        scenario_id: str | None,
        name: str | None,
        theater: str | None,
        summary: str | None,
        version: str = "1",
    ) -> ScenarioDraftView:
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise PersistenceError("scenario_id is required for blank scenario creation.")
        if not isinstance(name, str) or not name.strip():
            raise PersistenceError("name is required for blank scenario creation.")
        if not isinstance(theater, str) or not theater.strip():
            raise PersistenceError("theater is required for blank scenario creation.")
        payload = self._blank_scenario_payload(
            scenario_id=scenario_id.strip(),
            name=name.strip(),
            theater=theater.strip(),
            summary=(summary or f"Blank authoring scaffold for {theater.strip()}.").strip(),
            version=(version or "1").strip() or "1",
        )
        draft = ScenarioDraftView(
            draft_id=f"draft_{uuid4().hex[:12]}",
            source_scenario_id=f"{_BLANK_SOURCE_PREFIX}{payload['theater']}",
            source_scenario_name="Blank Scenario",
            scenario_id=payload["id"],
            name=payload["name"],
            display_name=f"{payload['name']} Draft",
            theater=payload["theater"],
            updated_at=datetime.now(UTC),
            scenario=payload,
            dirty=False,
            map_reference=self._blank_reference(payload["theater"], payload["id"], payload["name"], payload["summary"]),
            pydcs_mapping={},
        )
        self.store.save_scenario_draft(draft)
        return self._hydrate_draft(draft)

    def get_draft(self, draft_id: str) -> ScenarioDraftView:
        draft = self.store.get_scenario_draft(draft_id)
        return self._hydrate_draft(draft)

    def update_draft(self, draft_id: str, patch: ScenarioDraftPatch) -> ScenarioDraftView:
        current = self.store.get_scenario_draft(draft_id)
        merged = ScenarioDraftView(
            draft_id=current.draft_id,
            source_scenario_id=current.source_scenario_id,
            source_scenario_name=current.source_scenario_name,
            scenario_id=str(patch.scenario.get("id", current.scenario_id)),
            name=str(patch.scenario.get("name", current.name)),
            display_name=current.display_name,
            theater=str(patch.scenario.get("theater", current.theater)),
            updated_at=datetime.now(UTC),
            scenario=patch.scenario,
            dirty=False,
            map_reference=None,
            pydcs_mapping=patch.pydcs_mapping or current.pydcs_mapping,
        )
        self.store.save_scenario_draft(merged)
        return self._hydrate_draft(merged)

    def rename_draft(self, draft_id: str, display_name: str) -> ScenarioDraftView:
        current = self.store.get_scenario_draft(draft_id)
        renamed = replace(
            current,
            display_name=display_name.strip(),
            updated_at=datetime.now(UTC),
            dirty=False,
        )
        self.store.save_scenario_draft(renamed)
        return self._hydrate_draft(renamed)

    def duplicate_draft(self, draft_id: str) -> ScenarioDraftView:
        current = self.store.get_scenario_draft(draft_id)
        duplicated = ScenarioDraftView(
            draft_id=f"draft_{uuid4().hex[:12]}",
            source_scenario_id=current.source_scenario_id,
            source_scenario_name=current.source_scenario_name,
            scenario_id=current.scenario_id,
            name=current.name,
            display_name=f"{current.display_name} Copy",
            theater=current.theater,
            updated_at=datetime.now(UTC),
            scenario=current.scenario,
            dirty=False,
            map_reference=None,
            pydcs_mapping=current.pydcs_mapping,
        )
        self.store.save_scenario_draft(duplicated)
        return self._hydrate_draft(duplicated)

    def revert_draft(self, draft_id: str) -> ScenarioDraftView:
        current = self.store.get_scenario_draft(draft_id)
        if current.source_scenario_id.startswith(_BLANK_SOURCE_PREFIX):
            blank_scenario = self._blank_scenario_payload(
                scenario_id=current.scenario_id,
                name=current.name,
                theater=current.theater,
                summary=str(current.scenario.get("summary", f"Blank authoring scaffold for {current.theater}.")),
                version=str(current.scenario.get("version", "1")),
            )
            reverted = replace(
                current,
                updated_at=datetime.now(UTC),
                scenario=blank_scenario,
                dirty=False,
                map_reference=self._blank_reference(current.theater, current.scenario_id, current.name, blank_scenario["summary"]),
            )
            self.store.save_scenario_draft(reverted)
            return self._hydrate_draft(reverted)

        scenario = get_scenario_definition(current.source_scenario_id, self.index_path)
        reverted = ScenarioDraftView(
            draft_id=current.draft_id,
            source_scenario_id=current.source_scenario_id,
            source_scenario_name=scenario.name,
            scenario_id=scenario.id,
            name=scenario.name,
            display_name=current.display_name,
            theater=scenario.theater,
            updated_at=datetime.now(UTC),
            scenario=scenario_definition_to_dict(scenario),
            dirty=False,
            map_reference=None,
            pydcs_mapping=current.pydcs_mapping,
        )
        self.store.save_scenario_draft(reverted)
        return self._hydrate_draft(reverted)

    def delete_draft(self, draft_id: str) -> dict[str, Any]:
        self.store.delete_scenario_draft(draft_id)
        return {"deleted": True, "draft_id": draft_id}

    def validate_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_scenario_draft(draft_id)
        try:
            scenario = load_scenario_definition_data(draft.scenario, context=f"Scenario draft '{draft_id}'")
            return {
                "draft_id": draft_id,
                "valid": True,
                "issues": [],
                "scenario_id": scenario.id,
                "theater": scenario.theater,
            }
        except ConfigError as exc:
            return {
                "draft_id": draft_id,
                "valid": False,
                "issues": [self._issue_from_message(str(exc), draft)],
                "scenario_id": draft.scenario_id,
                "theater": draft.theater,
            }

    def save_as_scenario(
        self,
        draft_id: str,
        *,
        scenario_id: str,
        name: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        draft = self.store.get_scenario_draft(draft_id)
        payload = dict(draft.scenario)
        payload["id"] = scenario_id
        payload["name"] = name
        scenario = load_scenario_definition_data(payload, context=f"Scenario draft '{draft_id}'")

        scenario_path = self.index_path.parent / f"{scenario_id}.toml"
        if scenario_path.exists() and not overwrite:
            raise PersistenceError(f"Scenario file already exists: {scenario_path}")
        scenario_path.write_text(serialize_scenario_definition_toml(scenario), encoding="utf-8")

        existing_entries = list(load_registry(self.index_path))
        updated_entry = ScenarioRegistryEntry(
            id=scenario.id,
            name=scenario.name,
            theater=scenario.theater,
            path=scenario_path.resolve(),
            summary=scenario.summary,
        )
        replacement_index = next((idx for idx, item in enumerate(existing_entries) if item.id == scenario.id), None)
        if replacement_index is None:
            existing_entries.append(updated_entry)
        else:
            existing_entries[replacement_index] = updated_entry
        serialized_registry = serialize_registry(tuple(sorted(existing_entries, key=lambda item: item.id)))
        self.index_path.write_text(serialized_registry, encoding="utf-8")

        saved_draft = replace(
            draft,
            scenario_id=scenario.id,
            name=scenario.name,
            theater=scenario.theater,
            updated_at=datetime.now(UTC),
            scenario=scenario_definition_to_dict(scenario),
            dirty=False,
        )
        self.store.save_scenario_draft(saved_draft)
        return {
            "draft_id": draft_id,
            "saved": True,
            "scenario_id": scenario.id,
            "scenario_path": str(scenario_path),
            "registry_path": str(self.index_path),
            "overwrite": overwrite,
            "registry_entry": {
                "id": updated_entry.id,
                "name": updated_entry.name,
                "theater": updated_entry.theater,
                "summary": updated_entry.summary,
                "path": str(updated_entry.path),
            },
        }

    def export_draft_toml(self, draft_id: str) -> ScenarioDraftExportResult:
        draft = self.store.get_scenario_draft(draft_id)
        scenario = load_scenario_definition_data(draft.scenario, context=f"Scenario draft '{draft_id}'")
        return ScenarioDraftExportResult(
            draft_id=draft_id,
            filename=f"{scenario.id}.toml",
            media_type="application/toml",
            toml=serialize_scenario_definition_toml(scenario),
        )

    def create_object(self, draft_id: str, object_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        current = self.store.get_scenario_draft(draft_id)
        scenario = self._clone_scenario(current.scenario)
        created: dict[str, Any]

        if object_type == "sector":
            created = self._default_sector(current, scenario, payload)
            scenario["sectors"].append(created)
        elif object_type == "control_point":
            created = self._default_control_point(current, scenario, payload)
            scenario["control_points"].append(created)
        elif object_type == "zone":
            created = self._default_zone(current, scenario, payload)
            scenario["zones"].append(created)
        elif object_type == "active_group":
            created = self._default_active_group(current, scenario, payload)
            scenario["active_groups"].append(created)
        elif object_type == "reserve_group":
            created = self._default_reserve_group(current, scenario, payload)
            scenario["reserve_groups"].append(created)
            self._ensure_coalition_reserve_link(scenario, created["coalition"], created["id"])
        elif object_type == "deployment_restriction":
            created = self._default_restriction(current, scenario, payload)
            scenario["deployment_restrictions"].append(created)
        elif object_type == "standing_order":
            created = self._default_standing_order(current, scenario, payload)
            coalition = self._require_coalition_dict(scenario, created["coalition"])
            coalition.setdefault("standing_orders", []).append(
                {
                    "id": created["id"],
                    "text": created["text"],
                    "active": created["active"],
                }
            )
        else:
            raise PersistenceError(f"Unsupported object type: {object_type}")

        draft = self.update_draft(draft_id, ScenarioDraftPatch(scenario=scenario, pydcs_mapping=current.pydcs_mapping))
        return {"draft": draft, "created_object": {"object_type": object_type, "object": created}}

    def duplicate_object(
        self,
        draft_id: str,
        object_type: str,
        object_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        current = self.store.get_scenario_draft(draft_id)
        scenario = self._clone_scenario(current.scenario)

        if object_type == "standing_order":
            coalition = str(payload.get("coalition") or "")
            order = self._find_standing_order(scenario, coalition, object_id)
            created = {
                "id": self._unique_id(
                    [item["id"] for item in self._require_coalition_dict(scenario, coalition).get("standing_orders", [])],
                    f"{coalition}_order",
                ),
                "text": f"{order['text']} Copy".strip(),
                "active": bool(order.get("active", True)),
                "coalition": coalition,
            }
            self._require_coalition_dict(scenario, coalition).setdefault("standing_orders", []).append(
                {
                    "id": created["id"],
                    "text": created["text"],
                    "active": created["active"],
                }
            )
        else:
            collection_key = self._collection_key_for(object_type)
            source = self._find_object(scenario[collection_key], object_id, object_type)
            created = dict(source)
            created["id"] = self._unique_id([item["id"] for item in scenario[collection_key]], self._prefix_for(object_type))
            if "name" in created:
                created["name"] = f"{created['name']} Copy"
            if object_type == "sector":
                if isinstance(created.get("center_lat"), int | float):
                    created["center_lat"] = float(created["center_lat"]) + 0.12
                if isinstance(created.get("center_lng"), int | float):
                    created["center_lng"] = float(created["center_lng"]) + 0.12
                created["neighbor_ids"] = []
            if object_type == "reserve_group":
                self._ensure_coalition_reserve_link(scenario, created["coalition"], created["id"])
            scenario[collection_key].append(created)

        draft = self.update_draft(draft_id, ScenarioDraftPatch(scenario=scenario, pydcs_mapping=current.pydcs_mapping))
        return {"draft": draft, "created_object": {"object_type": object_type, "object": created}}

    def delete_object(
        self,
        draft_id: str,
        object_type: str,
        object_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        current = self.store.get_scenario_draft(draft_id)
        scenario = self._clone_scenario(current.scenario)

        if object_type == "sector":
            self._ensure_sector_delete_allowed(scenario, object_id)
            scenario["sectors"] = [item for item in scenario["sectors"] if item["id"] != object_id]
        elif object_type == "control_point":
            self._ensure_control_point_delete_allowed(scenario, object_id)
            scenario["control_points"] = [item for item in scenario["control_points"] if item["id"] != object_id]
        elif object_type == "zone":
            scenario["zones"] = [item for item in scenario["zones"] if item["id"] != object_id]
        elif object_type == "active_group":
            scenario["active_groups"] = [item for item in scenario["active_groups"] if item["id"] != object_id]
        elif object_type == "reserve_group":
            reserve = self._find_object(scenario["reserve_groups"], object_id, object_type)
            scenario["reserve_groups"] = [item for item in scenario["reserve_groups"] if item["id"] != object_id]
            coalition = self._require_coalition_dict(scenario, reserve["coalition"])
            coalition["reserve_ids"] = [item for item in coalition.get("reserve_ids", []) if item != object_id]
        elif object_type == "deployment_restriction":
            scenario["deployment_restrictions"] = [item for item in scenario["deployment_restrictions"] if item["id"] != object_id]
        elif object_type == "standing_order":
            coalition = str(payload.get("coalition") or "")
            coalition_dict = self._require_coalition_dict(scenario, coalition)
            self._find_standing_order(scenario, coalition, object_id)
            coalition_dict["standing_orders"] = [
                item for item in coalition_dict.get("standing_orders", []) if item["id"] != object_id
            ]
        else:
            raise PersistenceError(f"Unsupported object type: {object_type}")

        draft = self.update_draft(draft_id, ScenarioDraftPatch(scenario=scenario, pydcs_mapping=current.pydcs_mapping))
        return {"draft": draft, "deleted": True, "object_type": object_type, "object_id": object_id}

    def list_drafts(self) -> tuple[ScenarioDraftView, ...]:
        return tuple(self._hydrate_draft(item) for item in self.store.list_scenario_drafts())

    def _hydrate_draft(self, draft: ScenarioDraftView) -> ScenarioDraftView:
        source_name = draft.source_scenario_name
        if draft.source_scenario_id.startswith(_BLANK_SOURCE_PREFIX):
            source_name = source_name or "Blank Scenario"
        elif source_name is None:
            try:
                source_name = get_scenario_definition(draft.source_scenario_id, self.index_path).name
            except Exception:  # noqa: BLE001
                source_name = draft.source_scenario_id
        with_reference = self._with_reference(replace(draft, source_scenario_name=source_name))
        return replace(
            with_reference,
            display_name=with_reference.display_name or with_reference.name,
            dirty=False,
        )

    def _with_reference(self, draft: ScenarioDraftView) -> ScenarioDraftView:
        try:
            parsed = load_scenario_definition_data(draft.scenario, context=f"Scenario draft '{draft.draft_id}'")
            return replace(draft, map_reference=self.reference_service.reference_for_scenario(parsed))
        except ConfigError:
            if draft.source_scenario_id.startswith(_BLANK_SOURCE_PREFIX):
                return replace(
                    draft,
                    map_reference=self._blank_reference(
                        draft.theater,
                        draft.scenario_id,
                        draft.name,
                        str(draft.scenario.get("summary", f"Blank authoring scaffold for {draft.theater}.")),
                    ),
                )
            return draft

    def _issue_from_message(self, message: str, draft: ScenarioDraftView) -> dict[str, Any]:
        issue: dict[str, Any] = {
            "issue_code": "invalid_scenario_definition",
            "entity_type": "scenario",
            "entity_id": draft.scenario_id,
            "message": message,
        }
        actionable_patterns = (
            ("sector", r"'sectors' must be a non-empty array", "Add at least one sector to begin shaping the map."),
            (
                "control_point",
                r"'control_points' must be a non-empty array",
                "Add at least one control point after creating a sector.",
            ),
            ("sector", r"sector '([^']+)'", "Fix the selected sector or remove broken references to it."),
            ("control_point", r"control point '([^']+)'", "Fix the selected control point fields or references."),
            ("zone", r"zone '([^']+)'", "Fix the selected zone fields or references."),
            ("active_group", r"active group '([^']+)'", "Fix the selected active group fields or references."),
            ("reserve_group", r"reserve group '([^']+)'", "Fix the selected reserve group fields or references."),
        )
        for entity_type, pattern, suggestion in actionable_patterns:
            match = re.search(pattern, message)
            if match:
                issue["entity_type"] = entity_type
                issue["entity_id"] = match.group(1) if match.groups() else draft.scenario_id
                issue["issue_code"] = f"invalid_{entity_type}"
                issue["suggestion"] = suggestion
                break
        return issue

    def _blank_scenario_payload(
        self,
        *,
        scenario_id: str,
        name: str,
        theater: str,
        summary: str,
        version: str,
    ) -> dict[str, Any]:
        return {
            "id": scenario_id,
            "version": version,
            "name": name,
            "theater": theater,
            "summary": summary,
            "sectors": [],
            "control_points": [],
            "coalitions": [
                {
                    "coalition": "red",
                    "budget_remaining": 0,
                    "objectives": [],
                    "reserve_ids": [],
                    "standing_orders": [],
                    "attrition_pool": 0,
                    "replacement_pool": 0,
                },
                {
                    "coalition": "blue",
                    "budget_remaining": 0,
                    "objectives": [],
                    "reserve_ids": [],
                    "standing_orders": [],
                    "attrition_pool": 0,
                    "replacement_pool": 0,
                },
            ],
            "active_groups": [],
            "reserve_groups": [],
            "zones": [],
            "deployment_restrictions": [],
        }

    def _blank_reference(self, theater: str, scenario_id: str, name: str, summary: str) -> MapReferenceLayer:
        blank_definition = ScenarioDefinition(
            id=scenario_id,
            version="1",
            name=name,
            theater=theater,
            summary=summary,
            sectors=(),
            control_points=(),
            coalitions=(
                CoalitionState(coalition=Coalition.RED),
                CoalitionState(coalition=Coalition.BLUE),
            ),
            active_groups=(),
            reserve_groups=(),
            zones=(),
            deployment_restrictions=(),
        )
        fallback = next((entry for entry in list_scenarios(self.index_path) if entry.theater == theater), None)
        if fallback is None:
            return MapReferenceLayer(
                theater_id=theater,
                reference_status="unsupported",
                message="No authored fallback reference is available for this theater yet.",
                default_center_lat=25.0,
                default_center_lng=55.0,
                default_radius_nm=35.0,
            )
        return self.reference_service.reference_for_blank_scenario(
            blank_definition,
            get_scenario_definition(fallback.id, self.index_path),
        )

    def _clone_scenario(self, scenario: dict[str, Any]) -> dict[str, Any]:
        return {
            **scenario,
            "sectors": [dict(item) for item in scenario.get("sectors", [])],
            "control_points": [dict(item) for item in scenario.get("control_points", [])],
            "coalitions": [
                {
                    **dict(item),
                    "objectives": list(item.get("objectives", [])),
                    "reserve_ids": list(item.get("reserve_ids", [])),
                    "standing_orders": [dict(order) for order in item.get("standing_orders", [])],
                }
                for item in scenario.get("coalitions", [])
            ],
            "active_groups": [dict(item) for item in scenario.get("active_groups", [])],
            "reserve_groups": [dict(item) for item in scenario.get("reserve_groups", [])],
            "zones": [dict(item) for item in scenario.get("zones", [])],
            "deployment_restrictions": [dict(item) for item in scenario.get("deployment_restrictions", [])],
        }

    def _default_sector(self, draft: ScenarioDraftView, scenario: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        reference = draft.map_reference or self._blank_reference(draft.theater, draft.scenario_id, draft.name, str(scenario.get("summary", "")))
        sector_ids = [item["id"] for item in scenario["sectors"]]
        return {
            "id": self._unique_id(sector_ids, "sector"),
            "name": f"Sector {len(sector_ids) + 1}",
            "role": str(payload.get("role", "contested")),
            "neighbor_ids": [],
            "tags": [],
            "center_lat": self._numeric_value(payload.get("center_lat"), reference.default_center_lat or 25.0),
            "center_lng": self._numeric_value(payload.get("center_lng"), reference.default_center_lng or 55.0),
            "radius_nm": self._numeric_value(payload.get("radius_nm"), reference.default_radius_nm or 35.0),
        }

    def _default_control_point(self, draft: ScenarioDraftView, scenario: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        sector_id = str(payload.get("sector_id") or "")
        if not sector_id:
            raise PersistenceError("Select a sector before adding a control point.")
        sector = self._find_object(scenario["sectors"], sector_id, "sector")
        control_point_ids = [item["id"] for item in scenario["control_points"]]
        return {
            "id": self._unique_id(control_point_ids, "control_point"),
            "name": f"Control Point {len(control_point_ids) + 1}",
            "sector_id": sector_id,
            "kind": str(payload.get("kind", "forward_support")),
            "owner": payload.get("owner"),
            "strategic_value": str(payload.get("strategic_value", "medium")),
            "lat": self._numeric_value(payload.get("lat"), self._numeric_value(sector.get("center_lat"), 25.0)),
            "lng": self._numeric_value(payload.get("lng"), self._numeric_value(sector.get("center_lng"), 55.0)),
        }

    def _default_zone(self, draft: ScenarioDraftView, scenario: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        sector_id = str(payload.get("sector_id") or "")
        if not sector_id:
            raise PersistenceError("Select a sector before adding a zone.")
        sector = self._find_object(scenario["sectors"], sector_id, "sector")
        zone_ids = [item["id"] for item in scenario["zones"]]
        return {
            "id": self._unique_id(zone_ids, "zone"),
            "name": f"Zone {len(zone_ids) + 1}",
            "sector_id": sector_id,
            "center_lat": self._numeric_value(payload.get("center_lat"), self._numeric_value(sector.get("center_lat"), 25.0)),
            "center_lng": self._numeric_value(payload.get("center_lng"), self._numeric_value(sector.get("center_lng"), 55.0)),
            "radius_nm": self._numeric_value(payload.get("radius_nm"), 12.0),
            "tags": [],
        }

    def _default_active_group(self, _: ScenarioDraftView, scenario: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        coalition = str(payload.get("coalition", "red"))
        return {
            "id": self._unique_id([item["id"] for item in scenario["active_groups"]], "active_group"),
            "coalition": coalition,
            "group_type": str(payload.get("group_type", "ground_maneuver_group")),
            "posture": str(payload.get("posture", "defensive")),
            "sector_id": payload.get("sector_id"),
            "mobile": bool(payload.get("mobile", True)),
            "status": str(payload.get("status", "ready")),
            "control_point_id": payload.get("control_point_id"),
            "attrition_count": int(payload.get("attrition_count", 0)),
            "replacement_pool": int(payload.get("replacement_pool", 0)),
        }

    def _default_reserve_group(self, _: ScenarioDraftView, scenario: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        coalition = str(payload.get("coalition", "red"))
        return {
            "id": self._unique_id([item["id"] for item in scenario["reserve_groups"]], "reserve_group"),
            "coalition": coalition,
            "group_type": str(payload.get("group_type", "mechanized_reserve")),
            "available": bool(payload.get("available", True)),
            "cost": int(payload.get("cost", 4)),
            "allowed_sector_ids": list(payload.get("allowed_sector_ids", [])),
            "status": str(payload.get("status", "available")),
            "emergency": bool(payload.get("emergency", False)),
            "attrition_count": int(payload.get("attrition_count", 0)),
            "replacement_pool": int(payload.get("replacement_pool", 0)),
        }

    def _default_restriction(self, _: ScenarioDraftView, scenario: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": self._unique_id([item["id"] for item in scenario["deployment_restrictions"]], "restriction"),
            "coalition": payload.get("coalition"),
            "restriction_type": str(payload.get("restriction_type", "no_deploy_zone")),
            "description": str(payload.get("description", "Describe this restriction.")),
            "sector_ids": [],
            "control_point_ids": [],
            "adjacency_limited": bool(payload.get("adjacency_limited", False)),
        }

    def _default_standing_order(self, _: ScenarioDraftView, scenario: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        coalition = str(payload.get("coalition", "red"))
        order_ids = [item["id"] for item in self._require_coalition_dict(scenario, coalition).get("standing_orders", [])]
        return {
            "id": self._unique_id(order_ids, f"{coalition}_order"),
            "text": str(payload.get("text", f"Maintain {coalition.upper()} standing guidance.")),
            "active": bool(payload.get("active", True)),
            "coalition": coalition,
        }

    def _find_object(self, collection: list[dict[str, Any]], object_id: str, object_type: str) -> dict[str, Any]:
        for item in collection:
            if item["id"] == object_id:
                return item
        raise PersistenceError(f"Unknown {object_type} id: {object_id}")

    def _find_standing_order(self, scenario: dict[str, Any], coalition: str, order_id: str) -> dict[str, Any]:
        coalition_dict = self._require_coalition_dict(scenario, coalition)
        for order in coalition_dict.get("standing_orders", []):
            if order["id"] == order_id:
                return order
        raise PersistenceError(f"Unknown standing_order id: {order_id}")

    def _require_coalition_dict(self, scenario: dict[str, Any], coalition: str) -> dict[str, Any]:
        for item in scenario.get("coalitions", []):
            if item.get("coalition") == coalition:
                return item
        raise PersistenceError(f"Unknown coalition '{coalition}'.")

    def _ensure_coalition_reserve_link(self, scenario: dict[str, Any], coalition: str, reserve_id: str) -> None:
        coalition_dict = self._require_coalition_dict(scenario, coalition)
        reserve_ids = list(coalition_dict.get("reserve_ids", []))
        if reserve_id not in reserve_ids:
            reserve_ids.append(reserve_id)
        coalition_dict["reserve_ids"] = reserve_ids

    def _ensure_sector_delete_allowed(self, scenario: dict[str, Any], sector_id: str) -> None:
        self._find_object(scenario["sectors"], sector_id, "sector")
        dependencies: list[str] = []
        dependencies.extend(f"control_point:{item['id']}" for item in scenario["control_points"] if item.get("sector_id") == sector_id)
        dependencies.extend(f"zone:{item['id']}" for item in scenario["zones"] if item.get("sector_id") == sector_id)
        dependencies.extend(f"active_group:{item['id']}" for item in scenario["active_groups"] if item.get("sector_id") == sector_id)
        dependencies.extend(
            f"reserve_group:{item['id']}" for item in scenario["reserve_groups"] if sector_id in item.get("allowed_sector_ids", [])
        )
        dependencies.extend(
            f"deployment_restriction:{item['id']}" for item in scenario["deployment_restrictions"] if sector_id in item.get("sector_ids", [])
        )
        dependencies.extend(
            f"sector_neighbor:{item['id']}" for item in scenario["sectors"] if sector_id in item.get("neighbor_ids", [])
        )
        if dependencies:
            raise PersistenceError(
                f"Cannot delete sector '{sector_id}' because it is referenced by: {', '.join(sorted(dependencies))}."
            )

    def _ensure_control_point_delete_allowed(self, scenario: dict[str, Any], control_point_id: str) -> None:
        self._find_object(scenario["control_points"], control_point_id, "control_point")
        dependencies: list[str] = []
        dependencies.extend(
            f"active_group:{item['id']}" for item in scenario["active_groups"] if item.get("control_point_id") == control_point_id
        )
        dependencies.extend(
            f"deployment_restriction:{item['id']}"
            for item in scenario["deployment_restrictions"]
            if control_point_id in item.get("control_point_ids", [])
        )
        if dependencies:
            raise PersistenceError(
                f"Cannot delete control point '{control_point_id}' because it is referenced by: {', '.join(sorted(dependencies))}."
            )

    def _collection_key_for(self, object_type: str) -> str:
        mapping = {
            "sector": "sectors",
            "control_point": "control_points",
            "zone": "zones",
            "active_group": "active_groups",
            "reserve_group": "reserve_groups",
            "deployment_restriction": "deployment_restrictions",
        }
        try:
            return mapping[object_type]
        except KeyError as exc:
            raise PersistenceError(f"Unsupported object type: {object_type}") from exc

    def _prefix_for(self, object_type: str) -> str:
        mapping = {
            "sector": "sector",
            "control_point": "control_point",
            "zone": "zone",
            "active_group": "active_group",
            "reserve_group": "reserve_group",
            "deployment_restriction": "restriction",
        }
        return mapping[object_type]

    def _unique_id(self, existing_ids: list[str], prefix: str) -> str:
        existing = set(existing_ids)
        index = 1
        while True:
            candidate = f"{prefix}_{index}"
            if candidate not in existing:
                return candidate
            index += 1

    def _numeric_value(self, value: Any, fallback: float) -> float:
        return float(value) if isinstance(value, int | float) else float(fallback)
