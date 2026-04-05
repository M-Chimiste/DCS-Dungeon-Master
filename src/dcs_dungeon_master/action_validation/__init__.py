"""Phase 1 action validation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from dcs_dungeon_master.core.enums import (
    ActionType,
    Coalition,
    DestinationType,
    GroupPosture,
    RejectionCode,
    SectorPriority,
    ValidationStatus,
)
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.core.models import (
    ActionBatch,
    ActionValidationAuditEntry,
    ActionValidationAuditReport,
    ActionRequest,
    ActionValidationBatch,
    ActionValidationResult,
    ActiveGroupState,
    CoalitionState,
    ControlPointState,
    NormalizedActionRequest,
    ObservationArtifact,
    ReserveGroupState,
    ScenarioDefinition,
    ScenarioZone,
    SectorState,
    ValidationContext,
    ValidationAuditEvidence,
    ValidationMessage,
    WorldControlPointState,
    WorldGroupState,
)
from dcs_dungeon_master.persistence import SQLiteStateStore


_DEPLOY_ROLES = {"defensive", "screening", "support", "fires", "reserve"}
_REINFORCEMENT_ROLES = {"defend", "screen", "support", "fallback_cover"}
_HOLD_SCOPES = {"global", "sector", "group"}
_SOFT_NON_HOLD_CAP = 3
_UNAVAILABLE_ACTIVE_STATUSES = {"destroyed", "withdrawn", "unavailable"}
_UNAVAILABLE_RESERVE_STATUSES = {"committed", "unavailable", "depleted"}


@dataclass(slots=True)
class _WorkingResult:
    action_id: str
    action_type: ActionType | None
    status: ValidationStatus
    rejection_code: RejectionCode | None = None
    message: str | None = None
    normalized_params: dict[str, Any] | None = None
    messages: list[ValidationMessage] | None = None
    validated_stages: list[str] | None = None

    def __post_init__(self) -> None:
        if self.normalized_params is None:
            self.normalized_params = {}
        if self.messages is None:
            self.messages = []
        if self.validated_stages is None:
            self.validated_stages = []

    def reject(self, code: RejectionCode, message: str) -> None:
        self.status = ValidationStatus.REJECTED
        self.rejection_code = code
        self.message = message

    def add_message(self, level: str, code: str, text: str) -> None:
        self.messages.append(ValidationMessage(level=level, code=code, text=text))

    def freeze(self) -> ActionValidationResult:
        return ActionValidationResult(
            action_id=self.action_id,
            action_type=self.action_type,
            status=self.status,
            rejection_code=self.rejection_code,
            message=self.message,
            normalized_params=dict(self.normalized_params),
            messages=tuple(self.messages),
            validated_stages=tuple(self.validated_stages),
        )


@dataclass(slots=True, frozen=True)
class _Destination:
    destination_type: DestinationType
    destination_id: str
    sector_id: str


class ActionValidator:
    """Validates Phase 1 action payloads against scenario and coalition state."""

    def __init__(self, store: SQLiteStateStore, scenario: ScenarioDefinition, *, soft_non_hold_cap: int = _SOFT_NON_HOLD_CAP):
        self.store = store
        self.scenario = scenario
        self.soft_non_hold_cap = soft_non_hold_cap
        self._sector_by_id = {sector.id: sector for sector in scenario.sectors}
        self._control_point_by_id = {control_point.id: control_point for control_point in scenario.control_points}
        self._zone_by_id = {zone.id: zone for zone in scenario.zones}

    @property
    def status(self) -> str:
        return "action-validator-ready"

    def validate_payload(
        self,
        run_id: str,
        coalition: Coalition,
        decision_cycle: int,
        payload: list[dict[str, Any]] | dict[str, Any],
        *,
        persist: bool = True,
        submitted_at: datetime | None = None,
    ) -> ActionValidationBatch:
        raw_actions = self._extract_actions(payload)
        context = self._build_context(run_id, coalition, decision_cycle)
        working: list[tuple[_WorkingResult, NormalizedActionRequest | None, dict[str, Any]]] = []

        for raw_action in raw_actions:
            result, normalized = self._validate_one(raw_action, context)
            working.append((result, normalized, raw_action))

        self._apply_conflicts(working)

        non_hold_action_count = sum(
            1
            for result, normalized, raw_action in working
            if (normalized.action_type if normalized else self._parse_action_type(raw_action.get("action_type"))) != ActionType.HOLD_ACTION
        )
        batch_messages: list[ValidationMessage] = []
        if non_hold_action_count > self.soft_non_hold_cap:
            warning = ValidationMessage(
                level="warning",
                code="soft_batch_cap_exceeded",
                text=(
                    f"Batch contains {non_hold_action_count} non-hold actions. "
                    f"Soft cap is {self.soft_non_hold_cap}; actions remain individually valid."
                ),
            )
            batch_messages.append(warning)

        latest_observation = context.latest_observation
        batch = ActionValidationBatch(
            id=None,
            run_id=run_id,
            coalition=coalition,
            decision_cycle=decision_cycle,
            submitted_at=submitted_at or datetime.now(UTC),
            raw_payload=tuple(raw_actions),
            results=tuple(result.freeze() for result, _, _ in working),
            non_hold_action_count=non_hold_action_count,
            soft_cap_exceeded=non_hold_action_count > self.soft_non_hold_cap,
            batch_messages=tuple(batch_messages),
            latest_observation_id=latest_observation.id if latest_observation else None,
        )
        if persist:
            batch_id = self.store.save_action_validation_batch(batch)
            batch = replace(batch, id=batch_id)
        return batch

    def summarize_latest(self, run_id: str, coalition: Coalition) -> dict[str, Any]:
        batch = self.store.get_latest_action_validation_batch(run_id, coalition)
        accepted = sum(1 for result in batch.results if result.status == ValidationStatus.ACCEPTED)
        partial = sum(1 for result in batch.results if result.status == ValidationStatus.PARTIALLY_ACCEPTED)
        rejected = sum(1 for result in batch.results if result.status == ValidationStatus.REJECTED)
        return {
            "batch_id": batch.id,
            "run_id": batch.run_id,
            "coalition": batch.coalition.value,
            "decision_cycle": batch.decision_cycle,
            "submitted_at": batch.submitted_at.isoformat(),
            "non_hold_action_count": batch.non_hold_action_count,
            "soft_cap_exceeded": batch.soft_cap_exceeded,
            "batch_messages": [self._serialize_message(message) for message in batch.batch_messages],
            "accepted_count": accepted,
            "partially_accepted_count": partial,
            "rejected_count": rejected,
            "results": [
                {
                    "action_id": result.action_id,
                    "action_type": result.action_type.value if result.action_type else None,
                    "status": result.status.value,
                    "rejection_code": result.rejection_code.value if result.rejection_code else None,
                    "message": result.message,
                    "normalized_params": result.normalized_params,
                    "messages": [self._serialize_message(message) for message in result.messages],
                    "validated_stages": list(result.validated_stages),
                }
                for result in batch.results
            ],
        }

    def build_audit_report(self, run_id: str, coalition: Coalition) -> ActionValidationAuditReport:
        batch = self.store.get_latest_action_validation_batch(run_id, coalition)
        context = self._build_context(run_id, coalition, batch.decision_cycle)
        entries = tuple(
            self._build_audit_entry(raw_action, result, context, batch.latest_observation_id)
            for raw_action, result in zip(batch.raw_payload, batch.results, strict=False)
        )
        return ActionValidationAuditReport(
            batch_id=batch.id,
            run_id=batch.run_id,
            coalition=batch.coalition,
            decision_cycle=batch.decision_cycle,
            latest_observation_id=batch.latest_observation_id,
            entries=entries,
        )

    def _extract_actions(self, payload: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            actions = payload
        elif isinstance(payload, dict):
            actions = payload.get("actions", [])
        else:
            raise ValueError("Action payload must be a JSON array or an object with an 'actions' array.")
        if not isinstance(actions, list):
            raise ValueError("Action payload must contain an 'actions' array.")
        return [item if isinstance(item, dict) else {"invalid_payload": item} for item in actions]

    def _build_context(self, run_id: str, coalition: Coalition, decision_cycle: int) -> ValidationContext:
        coalition_state = next(
            state for state in self.store.get_coalition_states(run_id) if state.coalition == coalition
        )
        try:
            latest_observation = self.store.get_latest_observation(run_id, coalition)
        except PersistenceError:
            latest_observation = None
        return ValidationContext(
            run_id=run_id,
            coalition=coalition,
            decision_cycle=decision_cycle,
            scenario=self.scenario,
            coalition_state=coalition_state,
            active_groups=self.store.get_active_groups(run_id),
            reserve_groups=self.store.get_reserve_groups(run_id),
            world_state=self.store.get_world_state_snapshot(run_id),
            latest_observation=latest_observation,
        )

    def _build_audit_entry(
        self,
        raw_action: dict[str, Any],
        result: ActionValidationResult,
        context: ValidationContext,
        latest_observation_id: int | None,
    ) -> ActionValidationAuditEntry:
        params = dict(raw_action.get("params", {})) if isinstance(raw_action.get("params"), dict) else {}
        for key, value in raw_action.items():
            if key not in {"action_id", "id", "action_type", "coalition", "reason", "params"}:
                params.setdefault(key, value)
        params.update(result.normalized_params)
        evidence: list[ValidationAuditEvidence] = []
        if latest_observation_id is not None:
            evidence.append(
                ValidationAuditEvidence(
                    source_type="latest_observation",
                    identifier=str(latest_observation_id),
                    detail="Validation linked to the latest persisted coalition observation for this command stream.",
                )
            )
        if sector_id := params.get("sector_id") or params.get("target_sector_id"):
            evidence.append(
                ValidationAuditEvidence(
                    source_type="scenario_known",
                    identifier=str(sector_id),
                    detail="Sector legality was checked against the authored scenario graph and restrictions.",
                )
            )
        if destination_id := params.get("destination_id") or params.get("fallback_destination_id"):
            destination_type = params.get("destination_type") or params.get("fallback_destination_type")
            source_type = "scenario_known" if destination_type in {"sector", "zone", "control_point"} else "coalition_owned"
            evidence.append(
                ValidationAuditEvidence(
                    source_type=source_type,
                    identifier=str(destination_id),
                    detail=(
                        "Destination legality was checked using scenario-authored sectors/zones/control points."
                        if source_type == "scenario_known"
                        else "Destination legality was checked against coalition-owned state."
                    ),
                )
            )
        if control_point_id := params.get("control_point_id"):
            evidence.append(
                ValidationAuditEvidence(
                    source_type="scenario_known",
                    identifier=str(control_point_id),
                    detail="Control-point identity was resolved from the active authored scenario.",
                )
            )
        if group_id := params.get("group_id"):
            evidence.append(
                ValidationAuditEvidence(
                    source_type="coalition_owned",
                    identifier=str(group_id),
                    detail="Group ownership, mobility, and availability were checked only against this coalition's controllable groups.",
                )
            )
        for group_id in params.get("group_ids", []) if isinstance(params.get("group_ids"), list) else []:
            evidence.append(
                ValidationAuditEvidence(
                    source_type="coalition_owned",
                    identifier=str(group_id),
                    detail="Reinforcement group membership was checked against coalition-owned active groups.",
                )
            )
        if reserve_group_id := params.get("reserve_group_id"):
            evidence.append(
                ValidationAuditEvidence(
                    source_type="coalition_owned",
                    identifier=str(reserve_group_id),
                    detail="Reserve ownership, availability, and budget fit were checked against coalition state only.",
                )
            )
        evidence.append(
            ValidationAuditEvidence(
                source_type="anti_cheat_boundary",
                identifier=None,
                detail="No hidden enemy world-state facts were required to accept or reject this action.",
            )
        )
        return ActionValidationAuditEntry(
            action_id=result.action_id,
            action_type=result.action_type,
            status=result.status,
            rejection_code=result.rejection_code,
            message=result.message,
            latest_observation_id=latest_observation_id,
            evidence=tuple(evidence),
        )

    def _validate_one(
        self,
        raw_action: dict[str, Any],
        context: ValidationContext,
    ) -> tuple[_WorkingResult, NormalizedActionRequest | None]:
        action_id = self._read_action_id(raw_action)
        working = _WorkingResult(action_id=action_id, action_type=None, status=ValidationStatus.REJECTED)
        parsed = self._parse_request(raw_action, context.coalition, working)
        if parsed is None:
            return working, None
        working.action_type = parsed.action_type
        for stage in (
            self._validate_coalition,
            self._validate_scenario,
            self._validate_resources,
            self._validate_execution_readiness,
        ):
            if working.status == ValidationStatus.REJECTED:
                break
            stage(parsed, context, working)
        if working.status != ValidationStatus.REJECTED:
            if working.status == ValidationStatus.REJECTED:
                pass
            elif working.messages and working.status == ValidationStatus.ACCEPTED:
                working.status = ValidationStatus.PARTIALLY_ACCEPTED
            if working.status == ValidationStatus.REJECTED:
                working.normalized_params = {}
            elif not working.message:
                working.message = "Action validated for dry-run execution."
        return working, parsed if working.status != ValidationStatus.REJECTED else parsed

    def _parse_request(
        self,
        raw_action: dict[str, Any],
        coalition: Coalition,
        working: _WorkingResult,
    ) -> NormalizedActionRequest | None:
        if "invalid_payload" in raw_action:
            working.reject(RejectionCode.INVALID_FIELD_VALUE, "Each action entry must be a JSON object.")
            return None
        if not isinstance(raw_action.get("action_id", raw_action.get("id")), str) or not raw_action.get(
            "action_id",
            raw_action.get("id"),
        ).strip():
            working.reject(RejectionCode.MISSING_REQUIRED_FIELD, "Action field 'action_id' is required.")
            return None
        raw_action_type = raw_action.get("action_type")
        if not isinstance(raw_action_type, str) or not raw_action_type.strip():
            working.reject(RejectionCode.MISSING_REQUIRED_FIELD, "Action field 'action_type' is required.")
            return None
        try:
            action_type = ActionType(raw_action_type)
        except ValueError:
            working.reject(RejectionCode.UNKNOWN_ACTION_TYPE, f"Unknown action type '{raw_action_type}'.")
            return None
        reason = raw_action.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            working.action_type = action_type
            working.reject(RejectionCode.MISSING_REQUIRED_FIELD, "Action field 'reason' is required.")
            return None
        declared_coalition = raw_action.get("coalition")
        if declared_coalition is not None and declared_coalition != coalition.value:
            working.action_type = action_type
            working.reject(
                RejectionCode.COALITION_MISMATCH,
                f"Action coalition '{declared_coalition}' does not match command stream '{coalition.value}'.",
            )
            return None
        params: dict[str, Any] = {}
        if isinstance(raw_action.get("params"), dict):
            params.update(raw_action["params"])
        for key, value in raw_action.items():
            if key not in {"action_id", "id", "action_type", "coalition", "reason", "params"}:
                params[key] = value
        action_id = self._read_action_id(raw_action)
        parsed = NormalizedActionRequest(action_id=action_id, action_type=action_type, reason=reason.strip(), params=params)
        schema_error = self._validate_schema(parsed, working)
        if schema_error:
            return None
        return parsed

    def _validate_schema(self, parsed: NormalizedActionRequest, working: _WorkingResult) -> bool:
        params = dict(parsed.params)
        try:
            if parsed.action_type == ActionType.SET_SECTOR_PRIORITY:
                sector_id = self._require_str(params, "sector_id")
                priority = self._parse_priority(self._require_str(params, "priority"))
                working.normalized_params = {"sector_id": sector_id, "priority": priority.value}
            elif parsed.action_type == ActionType.DEPLOY_RESERVE_GROUP:
                reserve_group_id = self._require_str(params, "reserve_group_id")
                target_sector_id = self._require_str(params, "target_sector_id")
                role = self._require_str(params, "role")
                if role not in _DEPLOY_ROLES:
                    raise ValueError("role")
                working.normalized_params = {
                    "reserve_group_id": reserve_group_id,
                    "target_sector_id": target_sector_id,
                    "role": role,
                }
            elif parsed.action_type == ActionType.REPOSITION_GROUP:
                group_id = self._require_str(params, "group_id")
                destination_type = self._parse_destination_type(
                    self._require_str(params, "destination_type"),
                    "destination_type",
                )
                destination_id = self._require_str(params, "destination_id")
                working.normalized_params = {
                    "group_id": group_id,
                    "destination_type": destination_type.value,
                    "destination_id": destination_id,
                }
            elif parsed.action_type == ActionType.SET_GROUP_POSTURE:
                group_id = self._require_str(params, "group_id")
                posture = self._parse_posture(self._require_str(params, "posture"))
                working.normalized_params = {"group_id": group_id, "posture": posture.value}
            elif parsed.action_type == ActionType.REINFORCE_CONTROL_POINT:
                control_point_id = self._require_str(params, "control_point_id")
                group_ids_raw = params.get("group_ids")
                if not isinstance(group_ids_raw, list) or any(
                    not isinstance(group_id, str) or not group_id.strip() for group_id in group_ids_raw
                ):
                    raise ValueError("group_ids")
                if not 1 <= len(group_ids_raw) <= 3:
                    raise ValueError("group_ids")
                reinforcement_role = self._require_str(params, "reinforcement_role")
                if reinforcement_role not in _REINFORCEMENT_ROLES:
                    raise ValueError("reinforcement_role")
                unique_group_ids = tuple(dict.fromkeys(group_ids_raw))
                working.normalized_params = {
                    "control_point_id": control_point_id,
                    "group_ids": list(unique_group_ids),
                    "reinforcement_role": reinforcement_role,
                }
                if len(unique_group_ids) != len(group_ids_raw):
                    working.status = ValidationStatus.PARTIALLY_ACCEPTED
                    working.add_message(
                        "warning",
                        "duplicate_group_ids_normalized",
                        "Duplicate group IDs were removed from reinforce_control_point.",
                    )
            elif parsed.action_type == ActionType.WITHDRAW_GROUP:
                group_id = self._require_str(params, "group_id")
                fallback_type = self._parse_destination_type(
                    self._require_str(params, "fallback_destination_type"),
                    "fallback_destination_type",
                )
                fallback_id = self._require_str(params, "fallback_destination_id")
                working.normalized_params = {
                    "group_id": group_id,
                    "fallback_destination_type": fallback_type.value,
                    "fallback_destination_id": fallback_id,
                }
            elif parsed.action_type == ActionType.HOLD_ACTION:
                scope = self._require_str(params, "scope")
                if scope not in _HOLD_SCOPES:
                    raise ValueError("scope")
                normalized = {"scope": scope}
                if scope == "sector":
                    normalized["sector_id"] = self._require_str(params, "sector_id")
                if scope == "group":
                    normalized["group_id"] = self._require_str(params, "group_id")
                working.normalized_params = normalized
        except KeyError as exc:
            working.reject(RejectionCode.MISSING_REQUIRED_FIELD, f"Action field '{exc.args[0]}' is required.")
            return True
        except ValueError as exc:
            bad_field = exc.args[0] if exc.args else "field"
            code = RejectionCode.INVALID_FIELD_VALUE if bad_field != "action_type" else RejectionCode.UNKNOWN_ACTION_TYPE
            working.reject(code, f"Action field '{bad_field}' has an invalid value.")
            return True
        if working.status == ValidationStatus.REJECTED:
            working.status = ValidationStatus.ACCEPTED
        working.validated_stages.append("schema")
        working.action_type = parsed.action_type
        return False

    def _validate_coalition(
        self,
        parsed: NormalizedActionRequest,
        context: ValidationContext,
        working: _WorkingResult,
    ) -> None:
        active_by_id = {group.id: group for group in context.active_groups}
        reserve_by_id = {group.id: group for group in context.reserve_groups}
        world_group_by_id = {group.id: group for group in context.world_state.groups}
        world_control_by_id = {point.control_point_id: point for point in context.world_state.control_points}
        params = working.normalized_params

        def require_owned_group(group_id: str) -> ActiveGroupState:
            group = active_by_id.get(group_id)
            if group is None:
                raise _ValidationAbort(RejectionCode.UNKNOWN_ENTITY, f"Unknown active group '{group_id}'.")
            if group.coalition != context.coalition:
                raise _ValidationAbort(RejectionCode.ENTITY_NOT_OWNED, f"Group '{group_id}' is not owned by {context.coalition.value}.")
            world_group = world_group_by_id.get(group_id)
            if world_group is not None and world_group.coalition not in {None, context.coalition}:
                raise _ValidationAbort(RejectionCode.ENTITY_NOT_OWNED, f"Group '{group_id}' is not owned by {context.coalition.value}.")
            return group

        try:
            if parsed.action_type == ActionType.DEPLOY_RESERVE_GROUP:
                reserve = reserve_by_id.get(params["reserve_group_id"])
                if reserve is None:
                    raise _ValidationAbort(
                        RejectionCode.UNKNOWN_ENTITY,
                        f"Unknown reserve group '{params['reserve_group_id']}'.",
                    )
                if reserve.coalition != context.coalition:
                    raise _ValidationAbort(
                        RejectionCode.ENTITY_NOT_OWNED,
                        f"Reserve '{reserve.id}' is not owned by {context.coalition.value}.",
                    )
            elif parsed.action_type in {ActionType.REPOSITION_GROUP, ActionType.SET_GROUP_POSTURE, ActionType.WITHDRAW_GROUP}:
                require_owned_group(params["group_id"])
            elif parsed.action_type == ActionType.REINFORCE_CONTROL_POINT:
                control_point = self._control_point_by_id.get(params["control_point_id"])
                if control_point is None:
                    raise _ValidationAbort(
                        RejectionCode.UNKNOWN_ENTITY,
                        f"Unknown control point '{params['control_point_id']}'.",
                    )
                owner = world_control_by_id.get(control_point.id, WorldControlPointState(control_point.id, control_point.owner, control_point.sector_id)).owner
                if owner not in {None, context.coalition}:
                    raise _ValidationAbort(
                        RejectionCode.ENTITY_NOT_OWNED,
                        f"Control point '{control_point.id}' is not friendly or contested for {context.coalition.value}.",
                    )
                for group_id in params["group_ids"]:
                    require_owned_group(group_id)
            elif parsed.action_type == ActionType.HOLD_ACTION and params["scope"] == "group":
                require_owned_group(params["group_id"])
        except _ValidationAbort as exc:
            working.reject(exc.code, exc.message)
            return
        working.validated_stages.append("coalition")

    def _validate_scenario(
        self,
        parsed: NormalizedActionRequest,
        context: ValidationContext,
        working: _WorkingResult,
    ) -> None:
        params = working.normalized_params
        world_group_by_id = {group.id: group for group in context.world_state.groups}
        world_control_by_id = {point.control_point_id: point for point in context.world_state.control_points}
        try:
            if parsed.action_type == ActionType.SET_SECTOR_PRIORITY:
                self._require_sector(params["sector_id"])
            elif parsed.action_type == ActionType.DEPLOY_RESERVE_GROUP:
                reserve = self._require_reserve(context.reserve_groups, params["reserve_group_id"])
                self._require_sector(params["target_sector_id"])
                self._ensure_sector_allowed_for_reserve(context, reserve, params["target_sector_id"])
                self._ensure_sector_not_restricted(context.coalition, params["target_sector_id"])
                self._ensure_role_compatible(params["role"], reserve.group_type)
            elif parsed.action_type == ActionType.REPOSITION_GROUP:
                group = self._require_active_group(context.active_groups, params["group_id"])
                destination = self._resolve_destination(
                    DestinationType(params["destination_type"]),
                    params["destination_id"],
                )
                self._ensure_group_can_reach(group, world_group_by_id.get(group.id), destination)
                self._ensure_sector_not_restricted(context.coalition, destination.sector_id)
            elif parsed.action_type == ActionType.SET_GROUP_POSTURE:
                self._require_active_group(context.active_groups, params["group_id"])
            elif parsed.action_type == ActionType.REINFORCE_CONTROL_POINT:
                control_point = self._require_control_point(params["control_point_id"])
                owner = world_control_by_id.get(control_point.id)
                if owner is not None and owner.owner not in {None, context.coalition}:
                    raise _ValidationAbort(
                        RejectionCode.DESTINATION_RESTRICTED,
                        f"Control point '{control_point.id}' is not legal for reinforcement.",
                    )
                for group_id in params["group_ids"]:
                    group = self._require_active_group(context.active_groups, group_id)
                    self._ensure_group_can_reach_control_point(group, world_group_by_id.get(group.id), control_point)
            elif parsed.action_type == ActionType.WITHDRAW_GROUP:
                group = self._require_active_group(context.active_groups, params["group_id"])
                destination = self._resolve_destination(
                    DestinationType(params["fallback_destination_type"]),
                    params["fallback_destination_id"],
                )
                self._ensure_group_can_reach(group, world_group_by_id.get(group.id), destination)
                self._ensure_sector_not_restricted(context.coalition, destination.sector_id)
            elif parsed.action_type == ActionType.HOLD_ACTION and params["scope"] == "sector":
                self._require_sector(params["sector_id"])
        except _ValidationAbort as exc:
            working.reject(exc.code, exc.message)
            return
        working.validated_stages.append("scenario")

    def _validate_resources(
        self,
        parsed: NormalizedActionRequest,
        context: ValidationContext,
        working: _WorkingResult,
    ) -> None:
        params = working.normalized_params
        try:
            if parsed.action_type == ActionType.DEPLOY_RESERVE_GROUP:
                reserve = self._require_reserve(context.reserve_groups, params["reserve_group_id"])
                if not reserve.available or reserve.status in _UNAVAILABLE_RESERVE_STATUSES:
                    raise _ValidationAbort(
                        RejectionCode.RESERVE_NOT_AVAILABLE,
                        f"Reserve '{reserve.id}' is not available for deployment.",
                    )
                if context.coalition_state.budget_remaining < reserve.cost:
                    raise _ValidationAbort(
                        RejectionCode.INSUFFICIENT_BUDGET,
                        f"Reserve '{reserve.id}' costs {reserve.cost}, exceeding the remaining budget.",
                    )
            elif parsed.action_type in {
                ActionType.REPOSITION_GROUP,
                ActionType.SET_GROUP_POSTURE,
                ActionType.REINFORCE_CONTROL_POINT,
                ActionType.WITHDRAW_GROUP,
            }:
                group_ids = (
                    params["group_ids"]
                    if parsed.action_type == ActionType.REINFORCE_CONTROL_POINT
                    else [params["group_id"]]
                )
                for group_id in group_ids:
                    group = self._require_active_group(context.active_groups, group_id)
                    if group.status in _UNAVAILABLE_ACTIVE_STATUSES:
                        raise _ValidationAbort(
                            RejectionCode.ENTITY_UNAVAILABLE,
                            f"Group '{group.id}' is unavailable for actioning.",
                        )
        except _ValidationAbort as exc:
            working.reject(exc.code, exc.message)
            return
        working.validated_stages.append("resource")

    def _validate_execution_readiness(
        self,
        parsed: NormalizedActionRequest,
        context: ValidationContext,
        working: _WorkingResult,
    ) -> None:
        params = working.normalized_params
        try:
            if parsed.action_type in {ActionType.REPOSITION_GROUP, ActionType.WITHDRAW_GROUP}:
                group = self._require_active_group(context.active_groups, params["group_id"])
                if not group.mobile:
                    raise _ValidationAbort(
                        RejectionCode.SCENARIO_RULE_VIOLATION,
                        f"Group '{group.id}' is not mobile and cannot be moved.",
                    )
            if parsed.action_type == ActionType.REINFORCE_CONTROL_POINT:
                for group_id in params["group_ids"]:
                    group = self._require_active_group(context.active_groups, group_id)
                    if not group.mobile and group.control_point_id != params["control_point_id"]:
                        raise _ValidationAbort(
                            RejectionCode.SCENARIO_RULE_VIOLATION,
                            f"Group '{group.id}' cannot reinforce away from its current position.",
                        )
        except _ValidationAbort as exc:
            working.reject(exc.code, exc.message)
            return
        working.validated_stages.append("execution_readiness")

    def _apply_conflicts(
        self,
        working: list[tuple[_WorkingResult, NormalizedActionRequest | None, dict[str, Any]]],
    ) -> None:
        seen_reserves: dict[str, str] = {}
        sector_priorities: dict[str, tuple[str, str]] = {}
        movement_claims: dict[str, tuple[str, ActionType]] = {}
        posture_claims: dict[str, str] = {}

        for result, normalized, _ in working:
            if normalized is None or result.status == ValidationStatus.REJECTED:
                continue
            params = result.normalized_params
            if normalized.action_type == ActionType.DEPLOY_RESERVE_GROUP:
                reserve_id = params["reserve_group_id"]
                owner = seen_reserves.get(reserve_id)
                if owner is not None:
                    result.reject(
                        RejectionCode.ACTION_CONFLICT,
                        f"Reserve '{reserve_id}' is already committed by action '{owner}' in this batch.",
                    )
                    continue
                seen_reserves[reserve_id] = normalized.action_id
            elif normalized.action_type == ActionType.SET_SECTOR_PRIORITY:
                sector_id = params["sector_id"]
                priority = params["priority"]
                prior = sector_priorities.get(sector_id)
                if prior is not None and prior[1] != priority:
                    result.reject(
                        RejectionCode.ACTION_CONFLICT,
                        f"Sector '{sector_id}' already has a conflicting priority change in this batch.",
                    )
                    continue
                sector_priorities[sector_id] = (normalized.action_id, priority)
            elif normalized.action_type in {ActionType.REPOSITION_GROUP, ActionType.WITHDRAW_GROUP}:
                group_id = params["group_id"]
                prior_posture = posture_claims.get(group_id)
                if prior_posture is not None:
                    result.reject(
                        RejectionCode.ACTION_CONFLICT,
                        f"Group '{group_id}' already has a posture change in action '{prior_posture}'.",
                    )
                    continue
                prior = movement_claims.get(group_id)
                if prior is not None:
                    result.reject(
                        RejectionCode.ACTION_CONFLICT,
                        f"Group '{group_id}' already has a conflicting movement action '{prior[0]}'.",
                    )
                    continue
                movement_claims[group_id] = (normalized.action_id, normalized.action_type)
            elif normalized.action_type == ActionType.REINFORCE_CONTROL_POINT:
                for group_id in params["group_ids"]:
                    prior = movement_claims.get(group_id)
                    if prior is not None:
                        result.reject(
                            RejectionCode.ACTION_CONFLICT,
                            f"Group '{group_id}' already has a conflicting movement action '{prior[0]}'.",
                        )
                        break
                if result.status != ValidationStatus.REJECTED:
                    for group_id in params["group_ids"]:
                        movement_claims[group_id] = (normalized.action_id, normalized.action_type)
            elif normalized.action_type == ActionType.SET_GROUP_POSTURE:
                group_id = params["group_id"]
                prior = movement_claims.get(group_id)
                if prior is not None and prior[1] in {ActionType.REPOSITION_GROUP, ActionType.WITHDRAW_GROUP}:
                    result.reject(
                        RejectionCode.ACTION_CONFLICT,
                        f"Group '{group_id}' already has a conflicting movement action '{prior[0]}'.",
                    )
                    continue
                posture_claims[group_id] = normalized.action_id

    def _resolve_destination(self, destination_type: DestinationType, destination_id: str) -> _Destination:
        if destination_type == DestinationType.SECTOR:
            sector = self._require_sector(destination_id)
            return _Destination(destination_type=destination_type, destination_id=sector.id, sector_id=sector.id)
        if destination_type == DestinationType.CONTROL_POINT:
            control_point = self._require_control_point(destination_id)
            return _Destination(
                destination_type=destination_type,
                destination_id=control_point.id,
                sector_id=control_point.sector_id,
            )
        zone = self._zone_by_id.get(destination_id)
        if zone is None:
            raise _ValidationAbort(
                RejectionCode.DESTINATION_INVALID,
                f"Unknown zone destination '{destination_id}' for the active scenario.",
            )
        return _Destination(destination_type=destination_type, destination_id=zone.id, sector_id=zone.sector_id)

    def _ensure_group_can_reach(
        self,
        group: ActiveGroupState,
        world_group: WorldGroupState | None,
        destination: _Destination,
    ) -> None:
        source_sector = world_group.sector_id if world_group and world_group.sector_id else group.sector_id
        if source_sector is None:
            return
        if not self._is_same_or_adjacent(source_sector, destination.sector_id):
            raise _ValidationAbort(
                RejectionCode.DESTINATION_RESTRICTED,
                f"Destination '{destination.destination_id}' is not adjacent to current sector '{source_sector}'.",
            )

    def _ensure_group_can_reach_control_point(
        self,
        group: ActiveGroupState,
        world_group: WorldGroupState | None,
        control_point: ControlPointState,
    ) -> None:
        source_sector = world_group.sector_id if world_group and world_group.sector_id else group.sector_id
        if source_sector is None:
            return
        if not self._is_same_or_adjacent(source_sector, control_point.sector_id):
            raise _ValidationAbort(
                RejectionCode.DESTINATION_RESTRICTED,
                f"Control point '{control_point.id}' is not adjacent to group '{group.id}'.",
            )

    def _ensure_sector_allowed_for_reserve(
        self,
        context: ValidationContext,
        reserve: ReserveGroupState,
        sector_id: str,
    ) -> None:
        if sector_id not in reserve.allowed_sector_ids:
            raise _ValidationAbort(
                RejectionCode.DESTINATION_RESTRICTED,
                f"Sector '{sector_id}' is not in reserve '{reserve.id}' allowed deployment sectors.",
            )
        self._ensure_sector_not_restricted(context.coalition, sector_id)

    def _ensure_sector_not_restricted(self, coalition: Coalition, sector_id: str) -> None:
        for restriction in self.scenario.deployment_restrictions:
            if restriction.coalition not in {None, coalition}:
                continue
            if sector_id not in restriction.sector_ids:
                continue
            if restriction.restriction_type == "no_deploy_zone":
                raise _ValidationAbort(
                    RejectionCode.DESTINATION_RESTRICTED,
                    f"Sector '{sector_id}' is restricted for coalition '{coalition.value}'.",
                )

    def _ensure_role_compatible(self, role: str, group_type: str) -> None:
        if role == "fires" and "fires" not in group_type:
            raise _ValidationAbort(
                RejectionCode.SCENARIO_RULE_VIOLATION,
                f"Role '{role}' is not compatible with group type '{group_type}'.",
            )

    def _is_same_or_adjacent(self, source_sector_id: str, destination_sector_id: str) -> bool:
        if source_sector_id == destination_sector_id:
            return True
        source_sector = self._sector_by_id.get(source_sector_id)
        return destination_sector_id in (source_sector.neighbor_ids if source_sector else ())

    def _require_sector(self, sector_id: str) -> SectorState:
        sector = self._sector_by_id.get(sector_id)
        if sector is None:
            raise _ValidationAbort(RejectionCode.UNKNOWN_ENTITY, f"Unknown sector '{sector_id}'.")
        return sector

    def _require_control_point(self, control_point_id: str) -> ControlPointState:
        control_point = self._control_point_by_id.get(control_point_id)
        if control_point is None:
            raise _ValidationAbort(RejectionCode.UNKNOWN_ENTITY, f"Unknown control point '{control_point_id}'.")
        return control_point

    def _require_active_group(
        self,
        active_groups: tuple[ActiveGroupState, ...],
        group_id: str,
    ) -> ActiveGroupState:
        for group in active_groups:
            if group.id == group_id:
                return group
        raise _ValidationAbort(RejectionCode.UNKNOWN_ENTITY, f"Unknown active group '{group_id}'.")

    def _require_reserve(
        self,
        reserve_groups: tuple[ReserveGroupState, ...],
        reserve_id: str,
    ) -> ReserveGroupState:
        for reserve in reserve_groups:
            if reserve.id == reserve_id:
                return reserve
        raise _ValidationAbort(RejectionCode.UNKNOWN_ENTITY, f"Unknown reserve group '{reserve_id}'.")

    def _read_action_id(self, raw_action: dict[str, Any]) -> str:
        action_id = raw_action.get("action_id", raw_action.get("id"))
        if isinstance(action_id, str) and action_id.strip():
            return action_id
        return "unknown_action"

    def _require_str(self, data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            if value is None:
                raise KeyError(key)
            raise ValueError(key)
        return value

    def _parse_action_type(self, value: Any) -> ActionType | None:
        if not isinstance(value, str):
            return None
        try:
            return ActionType(value)
        except ValueError:
            return None

    def _serialize_message(self, message: ValidationMessage) -> dict[str, str]:
        return {"level": message.level, "code": message.code, "text": message.text}

    def _parse_priority(self, value: str) -> SectorPriority:
        try:
            return SectorPriority(value)
        except ValueError as exc:
            raise ValueError("priority") from exc

    def _parse_destination_type(self, value: str, field_name: str) -> DestinationType:
        try:
            return DestinationType(value)
        except ValueError as exc:
            raise ValueError(field_name) from exc

    def _parse_posture(self, value: str) -> GroupPosture:
        try:
            return GroupPosture(value)
        except ValueError as exc:
            raise ValueError("posture") from exc


@dataclass(slots=True, frozen=True)
class _ValidationAbort(Exception):
    code: RejectionCode
    message: str
