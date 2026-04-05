"""Execution engine and live command loop for Phase 1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.core.enums import (
    ActionType,
    Coalition,
    DestinationType,
    ExecutionLifecycleState,
    ExecutionStatus,
    GroupPosture,
    RunLifecycleStatus,
    ValidationStatus,
)
from dcs_dungeon_master.core.models import (
    ActiveGroupState,
    ActionValidationBatch,
    DecisionCycleResult,
    ExecutionBatchResult,
    ExecutionCapabilityMap,
    ExecutionCommand,
    ExecutionPlan,
    ExecutionResult,
    ModelInvocationResult,
    ObservationArtifact,
    LoopRunResult,
    ScenarioDefinition,
    StandingOrderRecord,
    WorldGroupState,
)
from dcs_dungeon_master.integration.olympus import OlympusClient
from dcs_dungeon_master.model_adapter import ModelAdapterRegistry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.integration.ingest import IntegrationIngestCoordinator


@dataclass(slots=True)
class ExecutionEngine:
    store: SQLiteStateStore
    scenario: ScenarioDefinition
    olympus: OlympusClient
    capability_map: ExecutionCapabilityMap = ExecutionCapabilityMap(
        primary_transport="olympus",
        secondary_transport="dcs_grpc",
        supported_action_types=tuple(ActionType),
    )
    _sector_by_id: dict[str, Any] = field(init=False, repr=False)
    _zone_by_id: dict[str, Any] = field(init=False, repr=False)
    _control_point_by_id: dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._sector_by_id = {sector.id: sector for sector in self.scenario.sectors}
        self._zone_by_id = {zone.id: zone for zone in self.scenario.zones}
        self._control_point_by_id = {point.id: point for point in self.scenario.control_points}

    @property
    def status(self) -> str:
        return "execution-ready"

    def execute_validation_batch(
        self,
        run_id: str,
        coalition: Coalition,
        decision_cycle: int,
        validation_batch: ActionValidationBatch,
        *,
        model_invocation_id: int | None,
        observation_id: int | None,
        now: datetime | None = None,
    ) -> ExecutionBatchResult:
        started_at = now or datetime.now(UTC)
        pending_batch = ExecutionBatchResult(
            id=None,
            run_id=run_id,
            coalition=coalition,
            decision_cycle=decision_cycle,
            observation_id=observation_id,
            model_invocation_id=model_invocation_id,
            validation_batch_id=validation_batch.id,
            started_at=started_at,
            completed_at=None,
            lifecycle_state=ExecutionLifecycleState.PENDING,
            status=ExecutionStatus.NO_CHANGE,
            results=(),
            summary=("pending_execution",),
        )
        batch_id = self.store.create_execution_batch(pending_batch)
        self.store.finalize_execution_batch(
            ExecutionBatchResult(
                id=batch_id,
                run_id=run_id,
                coalition=coalition,
                decision_cycle=decision_cycle,
                observation_id=observation_id,
                model_invocation_id=model_invocation_id,
                validation_batch_id=validation_batch.id,
                started_at=started_at,
                completed_at=None,
                lifecycle_state=ExecutionLifecycleState.IN_FLIGHT,
                status=ExecutionStatus.NO_CHANGE,
                results=(),
                summary=("execution_in_flight",),
            )
        )
        active_orders = self.store.get_active_execution_standing_orders(run_id, coalition)
        results: list[ExecutionResult] = []
        try:
            for _, validation_result in zip(validation_batch.raw_payload, validation_batch.results, strict=False):
                if validation_result.status == ValidationStatus.REJECTED:
                    continue
                plan = self._build_execution_plan(
                    run_id,
                    coalition,
                    validation_result.action_type,
                    validation_result.action_id,
                    validation_result.normalized_params,
                    started_at,
                )
                if self._is_duplicate_plan(plan, active_orders):
                    result = ExecutionResult(
                        action_id=plan.action_id,
                        action_type=plan.action_type,
                        status=ExecutionStatus.NO_CHANGE,
                        execution_summary="Equivalent standing order already active; simulator write skipped.",
                        command_count=0,
                        standing_order_delta=tuple(order.id for order in plan.standing_orders),
                        resulting_entities=plan.resulting_entities,
                    )
                else:
                    result = self._execute_plan(plan, started_at)
                results.append(result)
                if result.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.PARTIALLY_SUCCEEDED, ExecutionStatus.NO_CHANGE}:
                    state_updates = self._effective_state_updates(plan, result)
                    applied_orders = self._effective_standing_orders(plan, result)
                    self.store.apply_execution_state_updates(
                        run_id,
                        coalition,
                        validation_result.action_id,
                        validation_result.action_type,
                        result.status,
                        state_updates=state_updates,
                        standing_orders=applied_orders,
                        executed_at=started_at,
                        summary=result.execution_summary,
                        decision_cycle=decision_cycle,
                    )
                    if applied_orders:
                        active_orders = self.store.get_active_execution_standing_orders(run_id, coalition)
        except Exception:
            aborted = ExecutionBatchResult(
                id=batch_id,
                run_id=run_id,
                coalition=coalition,
                decision_cycle=decision_cycle,
                observation_id=observation_id,
                model_invocation_id=model_invocation_id,
                validation_batch_id=validation_batch.id,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                lifecycle_state=ExecutionLifecycleState.ABORTED,
                status=ExecutionStatus.FAILED,
                results=tuple(results),
                summary=("execution_aborted",),
            )
            self.store.finalize_execution_batch(aborted)
            raise

        completed_at = datetime.now(UTC)
        batch_status = self._classify_batch(results)
        summary = tuple(f"{result.action_id}:{result.status.value}" for result in results) or ("no_accepted_actions",)
        batch = ExecutionBatchResult(
            id=batch_id,
            run_id=run_id,
            coalition=coalition,
            decision_cycle=decision_cycle,
            observation_id=observation_id,
            model_invocation_id=model_invocation_id,
            validation_batch_id=validation_batch.id,
            started_at=started_at,
            completed_at=completed_at,
            lifecycle_state=ExecutionLifecycleState.COMPLETED,
            status=batch_status,
            results=tuple(results),
            summary=summary,
        )
        return replace(batch, id=self.store.finalize_execution_batch(batch))

    def summarize_latest(self, run_id: str, coalition: Coalition) -> dict[str, Any]:
        batch = self.store.get_latest_execution_batch(run_id, coalition)
        return {
            "execution_batch_id": batch.id,
            "run_id": batch.run_id,
            "coalition": batch.coalition.value,
            "decision_cycle": batch.decision_cycle,
            "status": batch.status.value,
            "result_count": len(batch.results),
            "summary": list(batch.summary),
            "results": [
                {
                    "action_id": result.action_id,
                    "action_type": result.action_type.value if result.action_type else None,
                    "status": result.status.value,
                    "execution_summary": result.execution_summary,
                    "command_count": result.command_count,
                    "resource_delta": result.resource_delta,
                    "standing_order_delta": list(result.standing_order_delta),
                    "resulting_entities": list(result.resulting_entities),
                    "error_detail": result.error_detail,
                }
                for result in batch.results
            ],
        }

    def summarize_standing_orders(self, run_id: str, coalition: Coalition) -> dict[str, Any]:
        orders = self.store.get_active_execution_standing_orders(run_id, coalition)
        return {
            "run_id": run_id,
            "coalition": coalition.value,
            "standing_order_count": len(orders),
            "standing_orders": [
                {
                    "id": order.id,
                    "action_type": order.action_type.value,
                    "entity_type": order.entity_type,
                    "entity_id": order.entity_id,
                    "text": order.text,
                    "fingerprint": order.fingerprint,
                    "last_updated_at": order.last_updated_at.isoformat() if order.last_updated_at else None,
                    "metadata": order.metadata,
                }
                for order in orders
            ],
        }

    def list_execution_records(self, run_id: str) -> tuple[ExecutionBatchResult, ...]:
        return self.store.list_execution_batches(run_id)

    def _build_execution_plan(
        self,
        run_id: str,
        coalition: Coalition,
        action_type: ActionType | None,
        action_id: str,
        params: dict[str, Any],
        now: datetime,
    ) -> ExecutionPlan:
        if action_type is None:
            return ExecutionPlan(action_id=action_id, coalition=coalition, action_type=ActionType.HOLD_ACTION)
        if action_type == ActionType.HOLD_ACTION:
            return ExecutionPlan(
                action_id=action_id,
                coalition=coalition,
                action_type=action_type,
                summary="Hold action executed as an explicit no-op.",
            )
        if action_type == ActionType.SET_SECTOR_PRIORITY:
            sector_id = params["sector_id"]
            priority = params["priority"]
            order = self._standing_order(
                run_id,
                coalition,
                action_type,
                "sector",
                sector_id,
                f"Maintain {priority} priority on sector {sector_id}.",
                params,
                now,
            )
            return ExecutionPlan(
                action_id=action_id,
                coalition=coalition,
                action_type=action_type,
                standing_orders=(order,),
                summary=f"Sector priority for {sector_id} updated to {priority}.",
            )
        if action_type == ActionType.DEPLOY_RESERVE_GROUP:
            reserve = next(item for item in self.store.get_reserve_groups(run_id, coalition) if item.id == params["reserve_group_id"])
            target_sector = self._resolve_sector(params["target_sector_id"])
            deployed_group_id = f"deployed_{reserve.id}"
            command = self._build_command(
                f"{action_id}_deploy",
                "/olympus/commands/reserve/deploy",
                {
                    "coalition": coalition.value,
                    "reserve_group_id": reserve.id,
                    "deployment_group_id": deployed_group_id,
                    "target_sector_id": target_sector.id,
                    "role": params["role"],
                },
            )
            world_group = self._world_group_for_position(
                group_id=deployed_group_id,
                source_id=reserve.id,
                coalition=coalition,
                group_type=reserve.group_type,
                sector_id=target_sector.id,
                control_point_id=None,
                posture=GroupPosture.DEFENSIVE,
                mobile=True,
                now=now,
            )
            active_group = ActiveGroupState(
                id=deployed_group_id,
                coalition=coalition,
                group_type=reserve.group_type,
                posture=GroupPosture.DEFENSIVE,
                sector_id=target_sector.id,
                mobile=True,
                status="ready",
            )
            order = self._standing_order(
                run_id,
                coalition,
                action_type,
                "group",
                deployed_group_id,
                f"Maintain deployed reserve {deployed_group_id} in sector {target_sector.id} as {params['role']}.",
                params,
                now,
            )
            return ExecutionPlan(
                action_id=action_id,
                coalition=coalition,
                action_type=action_type,
                commands=(command,),
                standing_orders=(order,),
                resource_delta={"budget_delta": -reserve.cost},
                resulting_entities=(deployed_group_id,),
                state_updates={
                    "budget_delta": -reserve.cost,
                    "reserve_groups": (
                        {
                            "reserve_group_id": reserve.id,
                            "available": False,
                            "status": "committed",
                        },
                    ),
                    "active_groups": (asdict(active_group),),
                    "world_groups": (asdict(world_group),),
                },
                summary=f"Reserve {reserve.id} deployed into sector {target_sector.id}.",
            )
        if action_type == ActionType.REPOSITION_GROUP:
            group = next(item for item in self.store.get_active_groups(run_id) if item.id == params["group_id"])
            sector_id, control_point_id = self._resolve_destination(params["destination_type"], params["destination_id"])
            order = self._standing_order(
                run_id,
                coalition,
                action_type,
                "group",
                group.id,
                f"Reposition {group.id} to {params['destination_type']} {params['destination_id']}.",
                params,
                now,
            )
            return self._movement_plan(
                action_id,
                coalition,
                action_type,
                group,
                sector_id,
                control_point_id,
                order,
                "/olympus/commands/groups/reposition",
                params,
                now,
            )
        if action_type == ActionType.SET_GROUP_POSTURE:
            group = next(item for item in self.store.get_active_groups(run_id) if item.id == params["group_id"])
            posture = GroupPosture(params["posture"])
            order = self._standing_order(
                run_id,
                coalition,
                action_type,
                "group",
                group.id,
                f"Maintain posture {posture.value} for group {group.id}.",
                params,
                now,
            )
            command = self._build_command(
                f"{action_id}_posture",
                "/olympus/commands/groups/posture",
                {"coalition": coalition.value, "group_id": group.id, "posture": posture.value},
            )
            active_group = ActiveGroupState(
                id=group.id,
                coalition=group.coalition,
                group_type=group.group_type,
                posture=posture,
                sector_id=group.sector_id,
                mobile=group.mobile,
                status=group.status,
                control_point_id=group.control_point_id,
                attrition_count=group.attrition_count,
                replacement_pool=group.replacement_pool,
            )
            return ExecutionPlan(
                action_id=action_id,
                coalition=coalition,
                action_type=action_type,
                commands=(command,),
                standing_orders=(order,),
                resulting_entities=(group.id,),
                state_updates={"active_groups": (asdict(active_group),)},
                summary=f"Group {group.id} posture updated to {posture.value}.",
            )
        if action_type == ActionType.REINFORCE_CONTROL_POINT:
            control_point = self._control_point_by_id[params["control_point_id"]]
            group_lookup = {item.id: item for item in self.store.get_active_groups(run_id)}
            commands: list[ExecutionCommand] = []
            active_updates: list[dict[str, Any]] = []
            world_updates: list[dict[str, Any]] = []
            orders: list[StandingOrderRecord] = []
            for index, group_id in enumerate(params["group_ids"]):
                group = group_lookup[group_id]
                commands.append(
                    self._build_command(
                        f"{action_id}_reinforce_{index}",
                        "/olympus/commands/control-points/reinforce",
                        {
                            "coalition": coalition.value,
                            "group_id": group.id,
                            "control_point_id": control_point.id,
                            "sector_id": control_point.sector_id,
                            "reinforcement_role": params["reinforcement_role"],
                        },
                    )
                )
                active_updates.append(
                    asdict(
                        ActiveGroupState(
                            id=group.id,
                            coalition=group.coalition,
                            group_type=group.group_type,
                            posture=group.posture,
                            sector_id=control_point.sector_id,
                            mobile=group.mobile,
                            status=group.status,
                            control_point_id=control_point.id,
                            attrition_count=group.attrition_count,
                            replacement_pool=group.replacement_pool,
                        )
                    )
                )
                world_updates.append(
                    asdict(self._world_group_for_position(
                        group_id=group.id,
                        source_id=group.id,
                        coalition=group.coalition,
                        group_type=group.group_type,
                        sector_id=control_point.sector_id,
                        control_point_id=control_point.id,
                        posture=group.posture,
                        mobile=group.mobile,
                        now=now,
                    ))
                )
                orders.append(
                    self._standing_order(
                        run_id,
                        coalition,
                        action_type,
                        "group",
                        group.id,
                        f"Reinforce control point {control_point.id} with group {group.id}.",
                        {**params, "group_id": group.id},
                        now,
                    )
                )
            return ExecutionPlan(
                action_id=action_id,
                coalition=coalition,
                action_type=action_type,
                commands=tuple(commands),
                standing_orders=tuple(orders),
                resulting_entities=tuple(params["group_ids"]),
                state_updates={"active_groups": tuple(active_updates), "world_groups": tuple(world_updates)},
                summary=f"Groups committed to reinforce control point {control_point.id}.",
            )
        if action_type == ActionType.WITHDRAW_GROUP:
            group = next(item for item in self.store.get_active_groups(run_id) if item.id == params["group_id"])
            sector_id, control_point_id = self._resolve_destination(
                params["fallback_destination_type"],
                params["fallback_destination_id"],
            )
            order = self._standing_order(
                run_id,
                coalition,
                action_type,
                "group",
                group.id,
                f"Withdraw {group.id} to {params['fallback_destination_type']} {params['fallback_destination_id']}.",
                params,
                now,
            )
            return self._movement_plan(
                action_id,
                coalition,
                action_type,
                group,
                sector_id,
                control_point_id,
                order,
                "/olympus/commands/groups/withdraw",
                params,
                now,
                posture=GroupPosture.FALLBACK,
            )
        return ExecutionPlan(action_id=action_id, coalition=coalition, action_type=action_type)

    def _movement_plan(
        self,
        action_id: str,
        coalition: Coalition,
        action_type: ActionType,
        group: ActiveGroupState,
        sector_id: str,
        control_point_id: str | None,
        order: StandingOrderRecord,
        path: str,
        params: dict[str, Any],
        now: datetime,
        *,
        posture: GroupPosture | None = None,
    ) -> ExecutionPlan:
        updated_posture = posture or group.posture
        command = self._build_command(
            f"{action_id}_move",
            path,
            {
                "coalition": coalition.value,
                "group_id": group.id,
                "destination_sector_id": sector_id,
                "control_point_id": control_point_id,
                "destination_type": params.get("destination_type") or params.get("fallback_destination_type"),
                "destination_id": params.get("destination_id") or params.get("fallback_destination_id"),
            },
        )
        world_group = self._world_group_for_position(
            group_id=group.id,
            source_id=group.id,
            coalition=group.coalition,
            group_type=group.group_type,
            sector_id=sector_id,
            control_point_id=control_point_id,
            posture=updated_posture,
            mobile=group.mobile,
            now=now,
        )
        active_group = ActiveGroupState(
            id=group.id,
            coalition=group.coalition,
            group_type=group.group_type,
            posture=updated_posture,
            sector_id=sector_id,
            mobile=group.mobile,
            status=group.status,
            control_point_id=control_point_id,
            attrition_count=group.attrition_count,
            replacement_pool=group.replacement_pool,
        )
        return ExecutionPlan(
            action_id=action_id,
            coalition=coalition,
            action_type=action_type,
            commands=(command,),
            standing_orders=(order,),
            resulting_entities=(group.id,),
            state_updates={"active_groups": (asdict(active_group),), "world_groups": (asdict(world_group),)},
            summary=f"Group {group.id} moved to sector {sector_id}.",
        )

    def _execute_plan(self, plan: ExecutionPlan, now: datetime) -> ExecutionResult:
        if plan.action_type == ActionType.HOLD_ACTION:
            return ExecutionResult(
                action_id=plan.action_id,
                action_type=plan.action_type,
                status=ExecutionStatus.NO_CHANGE,
                execution_summary=plan.summary or "Hold action required no simulator changes.",
                command_count=0,
            )

        if plan.standing_orders and not plan.commands:
            return ExecutionResult(
                action_id=plan.action_id,
                action_type=plan.action_type,
                status=ExecutionStatus.SUCCEEDED,
                execution_summary=plan.summary or "Standing orders updated without direct simulator writes.",
                command_count=0,
                resource_delta=dict(plan.resource_delta),
                standing_order_delta=tuple(order.id for order in plan.standing_orders),
                resulting_entities=plan.resulting_entities,
            )

        applied_commands: list[ExecutionCommand] = []
        errors: list[str] = []
        for command in plan.commands:
            try:
                response = self.olympus.send_write_request(
                    self.olympus.build_write_request(command.path, command.payload, method=command.method)
                )
                accepted = response.get("accepted", True) if isinstance(response, dict) else True
                if not accepted:
                    raise RuntimeError(str(response))
                applied_commands.append(command)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                break

        if not applied_commands:
            return ExecutionResult(
                action_id=plan.action_id,
                action_type=plan.action_type,
                status=ExecutionStatus.FAILED,
                execution_summary=f"Execution failed for action {plan.action_id}.",
                command_count=0,
                error_detail="; ".join(errors) if errors else "No simulator commands were applied.",
            )

        if len(applied_commands) < len(plan.commands):
            return ExecutionResult(
                action_id=plan.action_id,
                action_type=plan.action_type,
                status=ExecutionStatus.PARTIALLY_SUCCEEDED,
                execution_summary=f"Execution partially succeeded for action {plan.action_id}.",
                command_count=len(applied_commands),
                resource_delta=dict(plan.resource_delta),
                standing_order_delta=tuple(order.id for order in plan.standing_orders),
                resulting_entities=self._partial_entities(plan, applied_commands),
                error_detail="; ".join(errors),
                applied_commands=tuple(applied_commands),
            )

        return ExecutionResult(
            action_id=plan.action_id,
            action_type=plan.action_type,
            status=ExecutionStatus.SUCCEEDED,
            execution_summary=plan.summary or f"Execution succeeded for action {plan.action_id}.",
            command_count=len(applied_commands),
            resource_delta=dict(plan.resource_delta),
            standing_order_delta=tuple(order.id for order in plan.standing_orders),
            resulting_entities=plan.resulting_entities,
            applied_commands=tuple(applied_commands),
        )

    def _effective_state_updates(self, plan: ExecutionPlan, result: ExecutionResult) -> dict[str, Any]:
        if result.status != ExecutionStatus.PARTIALLY_SUCCEEDED:
            return plan.state_updates
        if plan.action_type != ActionType.REINFORCE_CONTROL_POINT:
            return plan.state_updates
        applied_group_ids = tuple(command.payload["group_id"] for command in result.applied_commands)
        active_updates = tuple(
            item for item in plan.state_updates.get("active_groups", ()) if item["id"] in applied_group_ids
        )
        world_updates = tuple(
            item for item in plan.state_updates.get("world_groups", ()) if item["id"] in applied_group_ids
        )
        return {"active_groups": active_updates, "world_groups": world_updates}

    def _effective_standing_orders(self, plan: ExecutionPlan, result: ExecutionResult) -> tuple[StandingOrderRecord, ...]:
        if result.status != ExecutionStatus.PARTIALLY_SUCCEEDED:
            return plan.standing_orders
        applied_group_ids = {command.payload.get("group_id") for command in result.applied_commands}
        return tuple(order for order in plan.standing_orders if order.entity_id in applied_group_ids)

    def _partial_entities(self, plan: ExecutionPlan, applied_commands: list[ExecutionCommand]) -> tuple[str, ...]:
        if plan.action_type == ActionType.REINFORCE_CONTROL_POINT:
            return tuple(command.payload["group_id"] for command in applied_commands)
        return plan.resulting_entities

    def _classify_batch(self, results: list[ExecutionResult]) -> ExecutionStatus:
        if not results:
            return ExecutionStatus.NO_CHANGE
        statuses = {result.status for result in results}
        if statuses == {ExecutionStatus.NO_CHANGE}:
            return ExecutionStatus.NO_CHANGE
        if ExecutionStatus.FAILED in statuses and len(statuses) == 1:
            return ExecutionStatus.FAILED
        if ExecutionStatus.FAILED in statuses:
            return ExecutionStatus.PARTIALLY_SUCCEEDED
        if ExecutionStatus.PARTIALLY_SUCCEEDED in statuses:
            return ExecutionStatus.PARTIALLY_SUCCEEDED
        return ExecutionStatus.SUCCEEDED

    def _is_duplicate_plan(self, plan: ExecutionPlan, active_orders: tuple[StandingOrderRecord, ...]) -> bool:
        existing_fingerprints = {order.fingerprint for order in active_orders}
        return bool(plan.standing_orders) and all(order.fingerprint in existing_fingerprints for order in plan.standing_orders)

    def _resolve_destination(self, destination_type: str, destination_id: str) -> tuple[str, str | None]:
        parsed_type = DestinationType(destination_type)
        if parsed_type == DestinationType.SECTOR:
            return destination_id, None
        if parsed_type == DestinationType.ZONE:
            return self._zone_by_id[destination_id].sector_id, None
        control_point = self._control_point_by_id[destination_id]
        return control_point.sector_id, control_point.id

    def _resolve_sector(self, sector_id: str):
        return self._sector_by_id[sector_id]

    def _standing_order(
        self,
        run_id: str,
        coalition: Coalition,
        action_type: ActionType,
        entity_type: str,
        entity_id: str | None,
        text: str,
        params: dict[str, Any],
        now: datetime,
    ) -> StandingOrderRecord:
        fingerprint = hashlib.sha1(
            json.dumps({"action_type": action_type.value, "entity_id": entity_id, "params": params}, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        suffix = entity_id or "global"
        return StandingOrderRecord(
            id=f"exec_{coalition.value}_{action_type.value}_{suffix}",
            run_id=run_id,
            coalition=coalition,
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            fingerprint=fingerprint,
            text=text,
            active=True,
            last_updated_at=now,
            metadata={"params": params},
        )

    def _build_command(self, command_id: str, path: str, payload: dict[str, Any], *, method: str = "POST") -> ExecutionCommand:
        return ExecutionCommand(command_id=command_id, transport="olympus", method=method, path=path, payload=payload)

    def _world_group_for_position(
        self,
        *,
        group_id: str,
        source_id: str,
        coalition: Coalition,
        group_type: str,
        sector_id: str,
        control_point_id: str | None,
        posture: GroupPosture,
        mobile: bool,
        now: datetime,
    ) -> WorldGroupState:
        sector = self._sector_by_id[sector_id]
        return WorldGroupState(
            id=group_id,
            source_id=source_id,
            coalition=coalition,
            group_type=group_type,
            category="execution_managed",
            name=group_id,
            sector_id=sector_id,
            control_point_id=control_point_id,
            lat=sector.center_lat,
            lng=sector.center_lng,
            source="execution",
            last_updated_at=now,
            high_value="air_defense" in group_type or "fires" in group_type,
            mobile=mobile,
        )


@dataclass(slots=True)
class LiveCommandLoopRunner:
    store: SQLiteStateStore
    observation_builder: ObservationBuilder
    action_validator: ActionValidator
    model_registry: ModelAdapterRegistry
    execution_engine: ExecutionEngine
    ingest_coordinator: IntegrationIngestCoordinator | None = None

    @property
    def status(self) -> str:
        return "live-loop-ready"

    def run_live_cycle(
        self,
        run_id: str,
        decision_cycle: int,
        *,
        now: datetime | None = None,
        seconds_since_last_cycle: int = 30,
    ) -> DecisionCycleResult:
        if self.ingest_coordinator is not None:
            self.ingest_coordinator.ingest_once(run_id, occurred_at=now)
        multimodal_submission = {
            coalition: self.model_registry.capability_for(self.model_registry.resolve_backend_name(coalition)).supports_multimodal
            for coalition in Coalition
        }
        red_observation, blue_observation = self.observation_builder.build_observation_pair(
            run_id,
            decision_cycle,
            now=now,
            seconds_since_last_cycle=seconds_since_last_cycle,
            persist=True,
            multimodal_submission=multimodal_submission,
        )
        red_invocation, red_execution = self._invoke_validate_execute(run_id, Coalition.RED, red_observation)
        blue_invocation, blue_execution = self._invoke_validate_execute(run_id, Coalition.BLUE, blue_observation)
        cycle = DecisionCycleResult(
            id=None,
            run_id=run_id,
            decision_cycle=decision_cycle,
            snapshot_time=red_observation.generated_at,
            red_observation_id=red_observation.id,
            blue_observation_id=blue_observation.id,
            red_invocation_id=red_invocation.id,
            blue_invocation_id=blue_invocation.id,
            red_execution_batch_id=red_execution.id if red_execution else None,
            blue_execution_batch_id=blue_execution.id if blue_execution else None,
            classification=self._classify_cycle(red_execution, blue_execution),
            summary=(
                f"red_model:{red_invocation.status}",
                f"red_exec:{red_execution.status.value if red_execution else 'none'}",
                f"blue_model:{blue_invocation.status}",
                f"blue_exec:{blue_execution.status.value if blue_execution else 'none'}",
            ),
        )
        return replace(cycle, id=self.store.save_decision_cycle_result(cycle))

    def run_live_loop(
        self,
        run_id: str,
        *,
        start_decision_cycle: int,
        cycles: int,
        seconds_since_last_cycle: int = 30,
    ) -> LoopRunResult:
        results: list[DecisionCycleResult] = []
        for offset in range(cycles):
            state = self.store.get_run_control_state(run_id)
            if state.status is RunLifecycleStatus.CREATED:
                self.store.update_run_status(run_id, RunLifecycleStatus.RUNNING, changed_at=datetime.now(UTC))
            elif state.status is RunLifecycleStatus.PAUSED:
                return LoopRunResult(
                    run_id=run_id,
                    start_decision_cycle=start_decision_cycle,
                    requested_cycles=cycles,
                    completed_cycles=len(results),
                    stopped_early=True,
                    terminal_run_status=state.status,
                    results=tuple(results),
                )
            elif state.status in {RunLifecycleStatus.STOPPED, RunLifecycleStatus.FAILED}:
                raise PersistenceError(f"Run '{run_id}' is terminal with status '{state.status.value}'.")
            results.append(
                self.run_live_cycle(
                    run_id,
                    start_decision_cycle + offset,
                    seconds_since_last_cycle=seconds_since_last_cycle,
                )
            )
        final_state = self.store.get_run_control_state(run_id)
        return LoopRunResult(
            run_id=run_id,
            start_decision_cycle=start_decision_cycle,
            requested_cycles=cycles,
            completed_cycles=len(results),
            stopped_early=len(results) != cycles,
            terminal_run_status=final_state.status,
            results=tuple(results),
        )

    def _invoke_validate_execute(
        self,
        run_id: str,
        coalition: Coalition,
        artifact: ObservationArtifact,
    ) -> tuple[ModelInvocationResult, ExecutionBatchResult | None]:
        from dcs_dungeon_master.model_adapter import DryDecisionLoopRunner

        dry_runner = DryDecisionLoopRunner(
            self.store,
            self.observation_builder,
            self.action_validator,
            self.model_registry,
            ingest_coordinator=None,
        )
        invocation = dry_runner._invoke_and_validate(run_id, coalition, artifact)  # noqa: SLF001
        if invocation.validation_batch_id is None:
            return invocation, None
        validation_batch = self.store.get_latest_action_validation_batch(run_id, coalition)
        execution_batch = self.execution_engine.execute_validation_batch(
            run_id,
            coalition,
            artifact.decision_cycle,
            validation_batch,
            model_invocation_id=invocation.id,
            observation_id=artifact.id,
        )
        return invocation, execution_batch

    def _classify_cycle(
        self,
        red_execution: ExecutionBatchResult | None,
        blue_execution: ExecutionBatchResult | None,
    ) -> str:
        def ok(batch: ExecutionBatchResult | None) -> bool:
            return batch is not None and batch.status in {
                ExecutionStatus.SUCCEEDED,
                ExecutionStatus.PARTIALLY_SUCCEEDED,
                ExecutionStatus.NO_CHANGE,
            }

        red_ok = ok(red_execution)
        blue_ok = ok(blue_execution)
        if red_ok and blue_ok:
            return "both_succeeded"
        if red_ok or blue_ok:
            return "one_succeeded_one_failed"
        return "both_failed"


@dataclass(slots=True)
class ExecutionEngineStub:
    status: str = "stub-ready"


__all__ = ["ExecutionEngine", "ExecutionEngineStub", "LiveCommandLoopRunner"]
