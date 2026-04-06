"""JSON API service for Campaign Studio and Live Ops."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.app import DEFAULT_CONFIG_PATH
from dcs_dungeon_master.core.config import AppConfig, load_config
from dcs_dungeon_master.core.enums import Coalition, ModelHostingMode, RunLifecycleStatus
from dcs_dungeon_master.core.exceptions import PersistenceError, ScenarioNotFoundError
from dcs_dungeon_master.core.models import (
    CampaignControlPointView,
    CampaignSectorView,
    CommanderForcePolicyView,
    CommanderPreviewSnapshot,
    LiveOpsSnapshot,
    OperatorMapLayerSet,
    ResolvedCoalitionRouting,
    ResolvedRunRouting,
    RunScopedBackendDefinition,
    ScenarioDraftCreateRequest,
    ScenarioDraftPatch,
)
from dcs_dungeon_master.evaluation import build_evaluation_metadata
from dcs_dungeon_master.execution import ExecutionEngine
from dcs_dungeon_master.integration.olympus import OlympusClient
from dcs_dungeon_master.map_assets import MapAssetService
from dcs_dungeon_master.model_adapter import build_model_registry
from dcs_dungeon_master.operator_control import OperatorControlService
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.run_continuation import RunContinuationService
from dcs_dungeon_master.scenario_state.registry import (
    get_scenario_definition,
    list_scenarios,
    load_scenario_definition_data,
    scenario_definition_to_dict,
    serialize_scenario_definition_toml,
)
from dcs_dungeon_master.setup_wizard import SetupWizardService
from dcs_dungeon_master.terrain import TerrainService
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
    config: AppConfig
    store: SQLiteStateStore
    operator: OperatorControlService
    run_continuation: RunContinuationService
    setup_wizard: SetupWizardService
    draft_service: ScenarioDraftService
    registry_path: Path
    map_asset_service: MapAssetService | None = None
    terrain_service: TerrainService | None = None

    @classmethod
    def from_config_path(cls, config_path: str | Path = DEFAULT_CONFIG_PATH) -> "WebUiService":
        config = load_config(config_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        store.initialize_schema()
        map_asset_service = MapAssetService(config.map_assets)
        terrain_service = TerrainService(config.air_ops)
        reference_service = PydcsReferenceService(
            map_asset_service=map_asset_service,
            terrain_service=terrain_service,
        )
        draft_service = ScenarioDraftService(store, index_path=config.scenario.registry_path, reference_service=reference_service)
        operator = OperatorControlService(store)
        continuation = RunContinuationService(store, operator)
        setup_wizard = SetupWizardService(config_path=config_path)
        return cls(
            config=config,
            store=store,
            operator=operator,
            run_continuation=continuation,
            setup_wizard=setup_wizard,
            draft_service=draft_service,
            registry_path=Path(config.scenario.registry_path),
            map_asset_service=map_asset_service,
            terrain_service=terrain_service,
        )

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

    def save_air_package_preset(self, scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scenario, scenario_path = self._load_editable_scenario(scenario_id)
        scenario_payload = scenario_definition_to_dict(scenario)
        preset_payload = payload.get("preset", payload)
        if not isinstance(preset_payload, dict):
            raise PersistenceError("preset payload must be an object.")
        preset_id = str(preset_payload.get("id", "")).strip()
        if not preset_id:
            preset_id = self._unique_id(
                [str(item.get("id")) for item in scenario_payload.get("air_package_presets", [])],
                "air_preset",
            )
        normalized_preset = {
            "id": preset_id,
            "coalition": str(preset_payload.get("coalition", "")).strip(),
            "name": str(preset_payload.get("name", "")).strip(),
            "description": preset_payload.get("description"),
            "inventory_id": str(preset_payload.get("inventory_id", "")).strip(),
            "package_type": str(preset_payload.get("package_type", "")).strip(),
            "aircraft_count": int(preset_payload.get("aircraft_count", 0)),
            "route_legs": tuple(
                dict(item) for item in preset_payload.get("route_legs", ()) if isinstance(item, dict)
            ),
            "target_reference_type": preset_payload.get("target_reference_type"),
            "target_reference_id": preset_payload.get("target_reference_id"),
            "posture": str(preset_payload.get("posture", "push")).strip() or "push",
            "roe": str(preset_payload.get("roe", "tight")).strip() or "tight",
        }
        if not normalized_preset["coalition"] or not normalized_preset["name"] or not normalized_preset["inventory_id"]:
            raise PersistenceError("Preset coalition, name, and inventory_id are required.")
        presets = [dict(item) for item in scenario_payload.get("air_package_presets", []) if item.get("id") != preset_id]
        presets.append(normalized_preset)
        scenario_payload["air_package_presets"] = presets
        validated = load_scenario_definition_data(scenario_payload, context=f"Scenario '{scenario_id}'")
        scenario_path.write_text(serialize_scenario_definition_toml(validated), encoding="utf-8")
        saved = next(item for item in scenario_definition_to_dict(validated)["air_package_presets"] if item["id"] == preset_id)
        return {"saved": True, "preset": saved, "scenario": scenario_definition_to_dict(validated)}

    def delete_air_package_preset(self, scenario_id: str, preset_id: str) -> dict[str, Any]:
        scenario, scenario_path = self._load_editable_scenario(scenario_id)
        scenario_payload = scenario_definition_to_dict(scenario)
        before_count = len(scenario_payload.get("air_package_presets", []))
        scenario_payload["air_package_presets"] = [
            item for item in scenario_payload.get("air_package_presets", []) if item.get("id") != preset_id
        ]
        if len(scenario_payload["air_package_presets"]) == before_count:
            raise PersistenceError(f"Unknown air package preset '{preset_id}'.")
        validated = load_scenario_definition_data(scenario_payload, context=f"Scenario '{scenario_id}'")
        scenario_path.write_text(serialize_scenario_definition_toml(validated), encoding="utf-8")
        return {"deleted": True, "preset_id": preset_id, "scenario": scenario_definition_to_dict(validated)}

    def create_scenario_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload.get("blank_scenario"), dict):
            blank = payload["blank_scenario"]
            request = ScenarioDraftCreateRequest(
                mode="blank",
                scenario_id=str(blank.get("scenario_id", "")).strip() or None,
                name=str(blank.get("name", "")).strip() or None,
                theater=str(blank.get("theater", "")).strip() or None,
                summary=str(blank.get("summary", "")).strip() or None,
                version=str(blank.get("version", "1")),
            )
        else:
            request = ScenarioDraftCreateRequest(
                mode="template",
                source_scenario_id=str(payload.get("source_scenario_id", "")).strip() or None,
            )
        return {"draft": _json_ready(self.draft_service.create_draft_from_request(request))}

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

    def export_scenario_draft(self, draft_id: str) -> dict[str, Any]:
        return {"export": _json_ready(self.draft_service.export_draft_toml(draft_id))}

    def create_scenario_object(self, draft_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        object_type = payload.get("object_type")
        if not isinstance(object_type, str) or not object_type.strip():
            raise PersistenceError("object_type is required.")
        result = self.draft_service.create_object(draft_id, object_type.strip(), payload)
        return _json_ready(result)

    def duplicate_scenario_object(self, draft_id: str, object_type: str, object_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _json_ready(self.draft_service.duplicate_object(draft_id, object_type, object_id, payload))

    def delete_scenario_object(self, draft_id: str, object_type: str, object_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _json_ready(self.draft_service.delete_object(draft_id, object_type, object_id, payload))

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

    def get_setup_status(self) -> dict[str, Any]:
        return {"setup": _json_ready(self.setup_wizard.probe())}

    def probe_setup(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "setup": _json_ready(
                self.setup_wizard.probe(
                    saved_games_path=payload.get("saved_games_path"),
                    olympus_url=payload.get("olympus_url"),
                    grpc_host=payload.get("grpc_host"),
                    grpc_port=int(payload["grpc_port"]) if payload.get("grpc_port") not in {None, ""} else None,
                )
            )
        }

    def write_setup_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = payload.get("output") or "config/local.toml"
        return {
            "write_result": _json_ready(
                self.setup_wizard.write_local_config(
                    output_path=output,
                    saved_games_path=payload.get("saved_games_path"),
                    olympus_url=payload.get("olympus_url"),
                    grpc_host=payload.get("grpc_host"),
                    grpc_port=int(payload["grpc_port"]) if payload.get("grpc_port") not in {None, ""} else None,
                )
            )
        }

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
                    "routing": _json_ready(state.routing),
                }
            )
        return {"runs": _json_ready(runs)}

    def list_resumable_runs(self) -> dict[str, Any]:
        return {"runs": _json_ready(self.run_continuation.list_resumable_runs())}

    def get_latest_run(self) -> dict[str, Any]:
        return {"run": _json_ready(self.run_continuation.get_latest_run())}

    def open_run(self, run_id: str) -> dict[str, Any]:
        return {"run": _json_ready(self.run_continuation.open_run(run_id))}

    def continue_run(self, run_id: str) -> dict[str, Any]:
        return {"run": _json_ready(self.run_continuation.continue_run(run_id))}

    def list_model_catalog(self) -> dict[str, Any]:
        registry = build_model_registry(self.config)
        try:
            return {"catalog": _json_ready(registry.list_catalog())}
        finally:
            registry.close()

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        scenario_id = payload.get("scenario_id") or self.config.scenario.id
        mode = str(payload.get("mode", "dry"))
        if mode not in {"dry", "live"}:
            raise PersistenceError("mode must be 'dry' or 'live'.")
        scenario = get_scenario_definition(str(scenario_id), self.registry_path)
        routing_payload = payload.get("routing", {})
        if routing_payload is not None and not isinstance(routing_payload, dict):
            raise PersistenceError("routing must be an object when provided.")
        routing, run_scoped_backends = self._resolve_run_routing(routing_payload or {})
        run_id = self.store.create_run_from_scenario(
            scenario,
            mode=mode,
            config_digest=hashlib.sha256(json.dumps(self.config.to_dict(), sort_keys=True).encode("utf-8")).hexdigest(),
            config_snapshot=self.config.to_dict(),
            red_backend_name=routing.red.primary_backend_name,
            blue_backend_name=routing.blue.primary_backend_name,
            routing=routing,
            run_scoped_backends=run_scoped_backends,
            evaluation_metadata=build_evaluation_metadata(
                self.config,
                red_backend_name=routing.red.primary_backend_name,
                blue_backend_name=routing.blue.primary_backend_name,
                run_scoped_backends=run_scoped_backends,
            ),
        )
        return {"run": _json_ready(self.store.get_run_control_state(run_id))}

    def get_run_routing(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run_control_state(run_id)
        return {
            "routing": _json_ready(run.routing),
            "run_scoped_backends": _json_ready(run.run_scoped_backends),
        }

    def update_run_routing(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run_control_state(run_id)
        if run.status is not RunLifecycleStatus.CREATED:
            raise PersistenceError("Run routing can only be updated while the run is still created.")
        routing_payload = payload.get("routing", payload)
        if not isinstance(routing_payload, dict):
            raise PersistenceError("routing must be an object.")
        routing, run_scoped_backends = self._resolve_run_routing(routing_payload)
        updated = self.store.update_run_routing(run_id, routing, run_scoped_backends=run_scoped_backends)
        return {"run": _json_ready(updated)}

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
        overlay = next(
            (self._attachment_public_url(item.uri) for item in artifact.observation.attachments if item.role == "map_overlay"),
            None,
        )
        map_image_uris = tuple(
            uri
            for uri in (
                self._attachment_public_url(item.uri)
                for item in artifact.observation.attachments
                if item.role in {"map_overlay", "map_theater_context", "map_front_aoi"}
            )
            if uri is not None
        )
        preview = CommanderPreviewSnapshot(
            run_id=run_id,
            coalition=coalition,
            observation_id=artifact.id or 0,
            decision_cycle=artifact.decision_cycle,
            generated_at=artifact.generated_at,
            narrative=artifact.narrative,
            observation=artifact.observation,
            map_overlay_uri=overlay,
            map_image_uris=map_image_uris,
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

    def submit_operator_actions(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.operator.get_status(run_id)
        if run.status in {RunLifecycleStatus.STOPPED, RunLifecycleStatus.FAILED}:
            raise PersistenceError(f"Run '{run_id}' is terminal with status '{run.status.value}'.")
        coalition_raw = payload.get("coalition")
        if not isinstance(coalition_raw, str) or not coalition_raw.strip():
            raise PersistenceError("coalition is required.")
        coalition = Coalition(coalition_raw.strip())
        actions = payload.get("actions")
        if not isinstance(actions, list) or not actions:
            raise PersistenceError("actions must be a non-empty array.")
        scenario = get_scenario_definition(run.scenario_id, self.registry_path)
        decision_cycle = max(self.operator.summarize_run(run_id).latest_decision_cycle, 1)
        submitted_at = datetime.now(UTC)
        validator = ActionValidator(
            self.store,
            scenario,
            map_asset_service=self.map_asset_service,
            terrain_service=self.terrain_service,
            air_ops=self.config.air_ops,
        )
        validation_batch = validator.validate_payload(
            run_id,
            coalition,
            decision_cycle,
            {"actions": actions},
            persist=True,
            submitted_at=submitted_at,
        )
        execution_batch = None
        if any(result.status.value in {"accepted", "partially_accepted"} for result in validation_batch.results):
            execution_engine = self._build_execution_engine(run.mode == "dry", scenario)
            try:
                execution_batch = execution_engine.execute_validation_batch(
                    run_id,
                    coalition,
                    decision_cycle,
                    validation_batch,
                    model_invocation_id=None,
                    observation_id=None,
                    simulate_only=run.mode == "dry",
                    now=submitted_at,
                )
            finally:
                execution_engine.olympus.close()
        self.store.record_operator_action_submission(
            run_id,
            coalition=coalition,
            decision_cycle=decision_cycle,
            submitted_at=submitted_at,
            action_ids=tuple(result.action_id for result in validation_batch.results),
            validation_batch_id=validation_batch.id,
            execution_batch_id=execution_batch.id if execution_batch is not None else None,
        )
        return {
            "submitted_at": submitted_at.isoformat(),
            "run_id": run_id,
            "mode": run.mode,
            "coalition": coalition.value,
            "decision_cycle": decision_cycle,
            "validation": _json_ready(validation_batch),
            "execution": _json_ready(execution_batch) if execution_batch is not None else None,
            "air_packages": _json_ready(self.store.list_air_packages(run_id, coalition)),
            "operator_action_summary": {
                "accepted_count": sum(result.status.value == "accepted" for result in validation_batch.results),
                "partially_accepted_count": sum(
                    result.status.value == "partially_accepted" for result in validation_batch.results
                ),
                "rejected_count": sum(result.status.value == "rejected" for result in validation_batch.results),
                "warnings": [
                    _json_ready(message)
                    for result in validation_batch.results
                    for message in result.messages
                    if getattr(message, "level", None) == "warning"
                ],
            },
            "ops": _json_ready(self.get_live_ops_snapshot(run_id)["ops"]),
        }

    def _resolve_run_routing(
        self,
        payload: dict[str, Any],
    ) -> tuple[ResolvedRunRouting, tuple[RunScopedBackendDefinition, ...]]:
        backend_by_name = {backend.name: backend for backend in self.config.models}
        catalog_by_id = {entry.id: entry for entry in self.config.model_catalog if entry.enabled}
        scoped_backends: list[RunScopedBackendDefinition] = []

        def make_scoped_backend(field_name: str, raw: dict[str, Any]) -> RunScopedBackendDefinition:
            display_name = raw.get("display_name") or raw.get("model") or field_name
            endpoint = raw.get("endpoint")
            model = raw.get("model")
            hosting_mode = raw.get("hosting_mode", "hosted")
            if not isinstance(endpoint, str) or not endpoint.strip():
                raise PersistenceError(f"{field_name}.endpoint is required for an ad-hoc backend override.")
            if not isinstance(model, str) or not model.strip():
                raise PersistenceError(f"{field_name}.model is required for an ad-hoc backend override.")
            try:
                ModelHostingMode(str(hosting_mode))
            except ValueError as exc:
                raise PersistenceError(f"{field_name}.hosting_mode must be one of: local, lan, hosted.") from exc
            backend_name = f"adhoc_{field_name}_{len(scoped_backends) + 1}"
            scoped = RunScopedBackendDefinition(
                backend_name=backend_name,
                display_name=str(display_name),
                endpoint=endpoint.strip(),
                model=model.strip(),
                hosting_mode=str(hosting_mode),
                multimodal=bool(raw.get("multimodal", False)),
                api_key_env_var=raw.get("api_key_env_var"),
                timeout_sec=float(raw.get("timeout_sec", 30.0)),
                max_retries=int(raw.get("max_retries", 1)),
                temperature=float(raw.get("temperature", 0.2)),
                max_output_tokens=raw.get("max_output_tokens"),
                system_prompt_variant=raw.get("system_prompt_variant"),
                source_label=str(raw.get("source_label", "ad-hoc")),
            )
            scoped_backends.append(scoped)
            return scoped

        def resolve_selector(
            field_name: str,
            selector: Any,
            adhoc: Any,
            *,
            default: tuple[str | None, str | None, str | None],
            required: bool = True,
        ) -> tuple[str | None, str | None, str | None]:
            if isinstance(adhoc, dict) and adhoc:
                scoped = make_scoped_backend(field_name, adhoc)
                return (scoped.backend_name, scoped.backend_name, "ad_hoc")
            if isinstance(selector, str) and selector.strip():
                value = selector.strip()
                if value in catalog_by_id:
                    entry = catalog_by_id[value]
                    return (entry.backend_name, value, "catalog")
                if value in backend_by_name and backend_by_name[value].enabled:
                    return (value, None, "config_backend")
                raise PersistenceError(f"{field_name} references unknown catalog entry or backend '{value}'.")
            if default[0] is None and required:
                raise PersistenceError(f"{field_name} is required.")
            return (default[0], default[1], default[2] or "default")

        same_for_both = bool(payload.get("same_for_both", True))
        shared_primary = payload.get("shared_primary_catalog_id") or payload.get("default_catalog_id")
        shared_fallback = payload.get("shared_fallback_catalog_id")
        shared_primary_adhoc = payload.get("shared_primary_adhoc")
        shared_fallback_adhoc = payload.get("shared_fallback_adhoc")
        red_primary_selector = payload.get("red_primary_catalog_id")
        blue_primary_selector = payload.get("blue_primary_catalog_id")
        red_fallback_selector = payload.get("red_fallback_catalog_id")
        blue_fallback_selector = payload.get("blue_fallback_catalog_id")

        default_primary = resolve_selector(
            "shared_primary",
            shared_primary,
            shared_primary_adhoc,
            default=(self.config.model_routing.red_backend, self.config.model_routing.default_catalog_id, "config"),
        )
        default_fallback = resolve_selector(
            "shared_fallback",
            shared_fallback,
            shared_fallback_adhoc,
            default=(self.config.model_routing.red_fallback_backend, self.config.model_routing.default_fallback_catalog_id, "config"),
            required=False,
        ) if shared_fallback or shared_fallback_adhoc or self.config.model_routing.red_fallback_backend else (None, None, None)

        if same_for_both:
            red_primary = default_primary
            blue_primary = default_primary
            red_fallback = default_fallback
            blue_fallback = default_fallback
        else:
            red_primary = resolve_selector("red_primary", red_primary_selector, payload.get("red_primary_adhoc"), default=default_primary)
            blue_primary = resolve_selector("blue_primary", blue_primary_selector, payload.get("blue_primary_adhoc"), default=default_primary)
            red_fallback = resolve_selector(
                "red_fallback",
                red_fallback_selector,
                payload.get("red_fallback_adhoc"),
                default=default_fallback,
                required=False,
            )
            blue_fallback = resolve_selector(
                "blue_fallback",
                blue_fallback_selector,
                payload.get("blue_fallback_adhoc"),
                default=default_fallback,
                required=False,
            )

        routing = ResolvedRunRouting(
            red=ResolvedCoalitionRouting(
                coalition=Coalition.RED,
                primary_backend_name=red_primary[0],
                fallback_backend_name=red_fallback[0] if red_fallback[0] else None,
                primary_catalog_id=red_primary[1],
                fallback_catalog_id=red_fallback[1] if red_fallback[1] else None,
                primary_source=red_primary[2],
                fallback_source=red_fallback[2] if red_fallback[0] else None,
            ),
            blue=ResolvedCoalitionRouting(
                coalition=Coalition.BLUE,
                primary_backend_name=blue_primary[0],
                fallback_backend_name=blue_fallback[0] if blue_fallback[0] else None,
                primary_catalog_id=blue_primary[1],
                fallback_catalog_id=blue_fallback[1] if blue_fallback[1] else None,
                primary_source=blue_primary[2],
                fallback_source=blue_fallback[2] if blue_fallback[0] else None,
            ),
            shared_primary_catalog_id=default_primary[1],
            shared_fallback_catalog_id=default_fallback[1] if default_fallback[0] else None,
            same_primary_for_both=red_primary[0] == blue_primary[0],
            same_fallback_for_both=(red_fallback[0] or None) == (blue_fallback[0] or None),
        )
        return (routing, tuple(scoped_backends))

    def dispatch(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        body = body or {}
        segments = [segment for segment in path.strip("/").split("/") if segment]
        if not segments or segments[0] != "api":
            return (404, {"error": "Unknown route."})
        try:
            if method == "GET" and segments == ["api", "setup", "status"]:
                return (200, self.get_setup_status())
            if method == "POST" and segments == ["api", "setup", "probe"]:
                return (200, self.probe_setup(body))
            if method == "POST" and segments == ["api", "setup", "write-config"]:
                return (200, self.write_setup_config(body))
            if method == "GET" and segments == ["api", "scenarios", "drafts"]:
                return (200, self.list_scenario_drafts())
            if method == "GET" and segments == ["api", "scenarios"]:
                return (200, self.list_scenarios())
            if method == "POST" and segments == ["api", "scenarios", "drafts"]:
                return (200, self.create_scenario_draft(body))
            if method == "GET" and len(segments) == 4 and segments[1:3] == ["scenarios", "drafts"]:
                return (200, self.get_scenario_draft(segments[3]))
            if method == "PUT" and len(segments) == 4 and segments[1:3] == ["scenarios", "drafts"]:
                return (200, self.update_scenario_draft(segments[3], body))
            if method == "GET" and len(segments) == 5 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "export.toml":
                return (200, self.export_scenario_draft(segments[3]))
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
            if method == "POST" and len(segments) == 5 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "objects":
                return (200, self.create_scenario_object(segments[3], body))
            if method == "DELETE" and len(segments) == 7 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "objects":
                return (200, self.delete_scenario_object(segments[3], segments[5], segments[6], body))
            if method == "POST" and len(segments) == 8 and segments[1:3] == ["scenarios", "drafts"] and segments[4] == "objects" and segments[7] == "duplicate":
                return (200, self.duplicate_scenario_object(segments[3], segments[5], segments[6], body))
            if method == "GET" and len(segments) == 3 and segments[1] == "scenarios":
                return (200, self.get_scenario(segments[2]))
            if method == "POST" and len(segments) == 4 and segments[1] == "scenarios" and segments[3] == "air-package-presets":
                return (200, self.save_air_package_preset(segments[2], body))
            if method == "DELETE" and len(segments) == 5 and segments[1] == "scenarios" and segments[3] == "air-package-presets":
                return (200, self.delete_air_package_preset(segments[2], segments[4]))
            if method == "GET" and segments == ["api", "theaters"]:
                return (200, self.list_theaters())
            if method == "GET" and len(segments) == 4 and segments[1] == "theaters" and segments[3] == "reference":
                return (200, self.get_theater_reference(segments[2]))
            if method == "GET" and segments == ["api", "runs"]:
                return (200, self.list_runs())
            if method == "GET" and segments == ["api", "runs", "resumable"]:
                return (200, self.list_resumable_runs())
            if method == "GET" and segments == ["api", "runs", "latest"]:
                return (200, self.get_latest_run())
            if method == "POST" and segments == ["api", "runs"]:
                return (200, self.create_run(body))
            if method == "GET" and segments == ["api", "model-catalog"]:
                return (200, self.list_model_catalog())
            if len(segments) >= 4 and segments[1] == "runs":
                run_id = segments[2]
                tail = segments[3:]
                if method == "GET" and tail == ["open"]:
                    return (200, self.open_run(run_id))
                if method == "POST" and tail == ["continue"]:
                    return (200, self.continue_run(run_id))
                if method == "GET" and tail == ["status"]:
                    return (200, self.get_run_status(run_id))
                if method == "GET" and tail == ["routing"]:
                    return (200, self.get_run_routing(run_id))
                if method == "PATCH" and tail == ["routing"]:
                    return (200, self.update_run_routing(run_id, body))
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
                if method == "POST" and tail == ["operator-actions"]:
                    return (200, self.submit_operator_actions(run_id, body))
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
        run = self.operator.get_status(run_id)
        scenario = get_scenario_definition(run.scenario_id, self.registry_path)
        world = self.store.get_world_state_snapshot(run_id)
        red_knowledge = self._optional(lambda: self.store.get_knowledge_state(Coalition.RED, run_id))
        blue_knowledge = self._optional(lambda: self.store.get_knowledge_state(Coalition.BLUE, run_id))
        latest_observations = self.store.list_observations(run_id)
        reference = self.draft_service.reference_service.reference_for_scenario(scenario)
        attachments = [
            {
                "observation_id": artifact.id,
                "coalition": artifact.coalition.value,
                "decision_cycle": artifact.decision_cycle,
                "attachments": [
                    _json_ready(
                        {
                            **_json_ready(item),
                            "uri": self._attachment_public_url(item.uri),
                        }
                    )
                    for item in artifact.observation.attachments
                ],
            }
            for artifact in latest_observations[-2:]
        ]
        return OperatorMapLayerSet(
            basemap=_json_ready(reference.basemap),
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
                for sector in scenario.sectors
            ),
            control_points=tuple(
                {
                    "id": point.control_point_id,
                    "name": next((item.name for item in scenario.control_points if item.id == point.control_point_id), point.control_point_id),
                    "control_point_id": point.control_point_id,
                    "owner": point.owner.value if point.owner is not None else None,
                    "sector_id": point.sector_id,
                    "source": point.source,
                    "kind": next((item.kind for item in scenario.control_points if item.id == point.control_point_id), None),
                    "lat": next((item.lat for item in scenario.control_points if item.id == point.control_point_id), None),
                    "lng": next((item.lng for item in scenario.control_points if item.id == point.control_point_id), None),
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
                for zone in scenario.zones
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
            air_packages=tuple(
                {
                    "package_id": item.package_id,
                    "package_type": item.package_type,
                    "aircraft_type": item.aircraft_type,
                    "coalition": item.coalition.value,
                    "status": item.status,
                    "posture": item.posture,
                    "roe": item.roe,
                    "origin_control_point_id": item.origin_control_point_id,
                    "current_sector_id": item.current_sector_id,
                    "route_legs": tuple(_json_ready(leg) for leg in item.normalized_route_legs or item.route_legs),
                }
                for item in self.store.list_air_packages(run_id)
            ),
            red_knowledge_tracks=tuple(_json_ready(track) for track in (red_knowledge.contact_tracks if red_knowledge else ())),
            blue_knowledge_tracks=tuple(_json_ready(track) for track in (blue_knowledge.contact_tracks if blue_knowledge else ())),
            current_execution_notes=tuple(_json_ready(note) for note in self.store.list_current_execution_notes(run_id)),
            attachments=tuple(attachments),
            landmarks=_json_ready(reference.landmarks),
            terrain_summary=_json_ready(reference.terrain_summary),
        )

    def _optional(self, callback):
        try:
            return callback()
        except PersistenceError:
            return None

    def _attachment_public_url(self, uri: str | None) -> str | None:
        if uri is None:
            return None
        output_root = Path(self.config.multimodal.output_dir).resolve()
        candidate = Path(uri).resolve()
        try:
            relative = candidate.relative_to(output_root)
        except Exception:  # noqa: BLE001
            return None
        return f"/attachments/{relative.as_posix()}"

    def _load_editable_scenario(self, scenario_id: str):
        for entry in list_scenarios(self.registry_path):
            if entry.id == scenario_id:
                return (get_scenario_definition(scenario_id, self.registry_path), entry.path)
        raise ScenarioNotFoundError(f"Unknown scenario id: {scenario_id}")

    def _unique_id(self, existing_ids: list[str], prefix: str) -> str:
        existing = set(existing_ids)
        index = 1
        while True:
            candidate = f"{prefix}_{index}"
            if candidate not in existing:
                return candidate
            index += 1

    def _build_execution_engine(self, simulate_only: bool, scenario):
        transport = None
        if simulate_only:
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"accepted": True, "path": request.url.path}))
        olympus = OlympusClient(self.config.dcs.olympus, transport=transport)
        return ExecutionEngine(self.store, scenario, olympus)
