"""Scenario draft persistence, validation, and save-as helpers for Campaign Studio."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from dcs_dungeon_master.core.exceptions import ConfigError, PersistenceError
from dcs_dungeon_master.core.models import ScenarioDraftPatch, ScenarioDraftView
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
        scenario = get_scenario_definition(source_scenario_id, self.index_path)
        draft = ScenarioDraftView(
            draft_id=f"draft_{uuid4().hex[:12]}",
            source_scenario_id=source_scenario_id,
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
        }

    def list_drafts(self) -> tuple[ScenarioDraftView, ...]:
        return tuple(self._hydrate_draft(item) for item in self.store.list_scenario_drafts())

    def _hydrate_draft(self, draft: ScenarioDraftView) -> ScenarioDraftView:
        source_name = draft.source_scenario_name
        if source_name is None:
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
        except ConfigError:
            return draft
        return replace(draft, map_reference=self.reference_service.reference_for_scenario(parsed))

    def _issue_from_message(self, message: str, draft: ScenarioDraftView) -> dict[str, Any]:
        issue = {
            "issue_code": "invalid_scenario_definition",
            "entity_type": "scenario",
            "entity_id": draft.scenario_id,
            "message": message,
        }
        patterns = (
            ("sector", r"sector '([^']+)'"),
            ("control_point", r"control point '([^']+)'"),
            ("zone", r"zone '([^']+)'"),
            ("active_group", r"active group '([^']+)'"),
            ("reserve_group", r"reserve group '([^']+)'"),
        )
        for entity_type, pattern in patterns:
            match = re.search(pattern, message)
            if match:
                issue["entity_type"] = entity_type
                issue["entity_id"] = match.group(1)
                issue["issue_code"] = f"invalid_{entity_type}"
                break
        return issue
