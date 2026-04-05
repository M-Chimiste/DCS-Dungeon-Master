"""CLI-first operator control, inspection, and replay export services."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from dcs_dungeon_master.core.enums import Coalition, RunLifecycleStatus
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.core.models import (
    CoalitionInspectionView,
    LoopRunResult,
    OperatorCommandResult,
    ReplayBundleManifest,
    ReplayExportResult,
    RunComparisonResult,
    RunControlState,
    RunFailureEntry,
    RunSummary,
    RunTimelineEntry,
    RunWarningEntry,
)
from dcs_dungeon_master.persistence import SQLiteStateStore


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


@dataclass(slots=True)
class OperatorControlService:
    store: SQLiteStateStore
    sensor_fusion: Any | None = None
    world_repository: Any | None = None

    @property
    def status(self) -> str:
        return "operator-control-ready"

    def get_status(self, run_id: str | None = None) -> RunControlState:
        return self.store.get_run_control_state(run_id)

    def start_run(self, run_id: str, *, now: datetime | None = None) -> OperatorCommandResult:
        return self._transition(run_id, RunLifecycleStatus.RUNNING, changed_at=now or datetime.now(UTC))

    def pause_run(self, run_id: str, *, now: datetime | None = None) -> OperatorCommandResult:
        return self._transition(run_id, RunLifecycleStatus.PAUSED, changed_at=now or datetime.now(UTC))

    def resume_run(self, run_id: str, *, now: datetime | None = None) -> OperatorCommandResult:
        return self._transition(run_id, RunLifecycleStatus.RUNNING, changed_at=now or datetime.now(UTC))

    def stop_run(self, run_id: str, *, reason: str | None = None, now: datetime | None = None) -> OperatorCommandResult:
        return self._transition(
            run_id,
            RunLifecycleStatus.STOPPED,
            changed_at=now or datetime.now(UTC),
            reason=reason or "stopped_by_operator",
        )

    def fail_run(self, run_id: str, *, reason: str, now: datetime | None = None) -> OperatorCommandResult:
        return self._transition(
            run_id,
            RunLifecycleStatus.FAILED,
            changed_at=now or datetime.now(UTC),
            reason=reason,
        )

    def ensure_cycle_allowed(self, run_id: str) -> RunControlState:
        state = self.get_status(run_id)
        if state.status is RunLifecycleStatus.CREATED:
            self.start_run(run_id)
            return self.get_status(run_id)
        if state.status is RunLifecycleStatus.PAUSED:
            raise PersistenceError(f"Run '{run_id}' is paused and cannot advance until resumed.")
        if state.status in {RunLifecycleStatus.STOPPED, RunLifecycleStatus.FAILED}:
            raise PersistenceError(f"Run '{run_id}' is terminal with status '{state.status.value}'.")
        return state

    def summarize_run(self, run_id: str | None = None) -> RunSummary:
        resolved_run_id = run_id or self.store.get_latest_run_id()
        run = self.get_status(resolved_run_id)
        observations = self.store.list_observations(resolved_run_id)
        invocations = self.store.list_model_invocations(resolved_run_id)
        validations = self.store.list_action_validation_batches(resolved_run_id)
        executions = self.store.list_execution_batches(resolved_run_id)
        cycles = self.store.list_decision_cycles(resolved_run_id)

        def count_by_coalition(items: tuple[Any, ...]) -> dict[str, int]:
            counts = {Coalition.RED.value: 0, Coalition.BLUE.value: 0}
            for item in items:
                coalition = getattr(item, "coalition", None)
                if coalition is not None:
                    counts[coalition.value] += 1
            return counts

        cycle_classification_counts: dict[str, int] = {}
        for cycle in cycles:
            cycle_classification_counts[cycle.classification] = cycle_classification_counts.get(cycle.classification, 0) + 1

        model_status_counts: dict[str, int] = {}
        for invocation in invocations:
            model_status_counts[invocation.status] = model_status_counts.get(invocation.status, 0) + 1

        execution_status_counts: dict[str, int] = {}
        for batch in executions:
            key = batch.status.value
            execution_status_counts[key] = execution_status_counts.get(key, 0) + 1

        failures, warnings = self._classify_issues(resolved_run_id, invocations, executions)
        errors: list[str] = [entry.detail for entry in failures[:3]]
        for invocation in reversed(invocations):
            if len(errors) >= 3:
                break

        latest_decision_cycle = max((cycle.decision_cycle for cycle in cycles), default=0)
        coalition_views = tuple(self.inspect_coalition(resolved_run_id, coalition) for coalition in Coalition)
        failure_counts = self._count_issue_types(failures)
        warning_counts = self._count_issue_types(warnings)
        return RunSummary(
            run=run,
            latest_decision_cycle=latest_decision_cycle,
            observation_count=len(observations),
            model_invocation_count=len(invocations),
            validation_count=len(validations),
            execution_count=len(executions),
            decision_cycle_count=len(cycles),
            observation_count_by_coalition=count_by_coalition(observations),
            model_invocation_count_by_coalition=count_by_coalition(invocations),
            validation_count_by_coalition=count_by_coalition(validations),
            execution_count_by_coalition=count_by_coalition(executions),
            cycle_classification_counts=cycle_classification_counts,
            model_status_counts=model_status_counts,
            execution_status_counts=execution_status_counts,
            failure_counts=failure_counts,
            warning_counts=warning_counts,
            last_error_summary=tuple(errors),
            failures=tuple(failures),
            warnings=tuple(warnings),
            coalition_views=coalition_views,
        )

    def list_timeline(self, run_id: str | None = None) -> tuple[RunTimelineEntry, ...]:
        resolved_run_id = run_id or self.store.get_latest_run_id()
        cycles = self.store.list_decision_cycles(resolved_run_id)
        invocations = {item.id: item for item in self.store.list_model_invocations(resolved_run_id)}
        executions = {item.id: item for item in self.store.list_execution_batches(resolved_run_id)}
        failure_map = self._timeline_failures(resolved_run_id)
        return tuple(
            RunTimelineEntry(
                run_id=cycle.run_id,
                decision_cycle=cycle.decision_cycle,
                snapshot_time=cycle.snapshot_time,
                classification=cycle.classification,
                red_observation_id=cycle.red_observation_id,
                blue_observation_id=cycle.blue_observation_id,
                red_invocation_status=invocations[cycle.red_invocation_id].status if cycle.red_invocation_id in invocations else None,
                blue_invocation_status=invocations[cycle.blue_invocation_id].status if cycle.blue_invocation_id in invocations else None,
                red_execution_status=executions[cycle.red_execution_batch_id].status.value if cycle.red_execution_batch_id in executions else None,
                blue_execution_status=executions[cycle.blue_execution_batch_id].status.value if cycle.blue_execution_batch_id in executions else None,
                red_execution_lifecycle_state=executions[cycle.red_execution_batch_id].lifecycle_state.value if cycle.red_execution_batch_id in executions else None,
                blue_execution_lifecycle_state=executions[cycle.blue_execution_batch_id].lifecycle_state.value if cycle.blue_execution_batch_id in executions else None,
                failures=failure_map.get(cycle.decision_cycle, ()),
                summary=cycle.summary,
            )
            for cycle in cycles
        )

    def inspect_coalition(self, run_id: str | None, coalition: Coalition) -> CoalitionInspectionView:
        resolved_run_id = run_id or self.store.get_latest_run_id()

        def maybe(callable_obj: Any) -> Any | None:
            try:
                return callable_obj()
            except PersistenceError:
                return None

        observation = maybe(lambda: self.store.get_latest_observation(resolved_run_id, coalition))
        invocation = maybe(lambda: self.store.get_latest_model_invocation(resolved_run_id, coalition))
        validation = maybe(lambda: self.store.get_latest_action_validation_batch(resolved_run_id, coalition))
        execution = maybe(lambda: self.store.get_latest_execution_batch(resolved_run_id, coalition))
        current_notes = tuple(
            {
                "entity_type": note.entity_type,
                "entity_id": note.entity_id,
                "decision_cycle": note.decision_cycle,
                "action_id": note.action_id,
                "note": note.note,
                "lifecycle_state": note.lifecycle_state.value,
                "updated_at": note.updated_at,
                "metadata": note.metadata,
            }
            for note in self.store.list_current_execution_notes(resolved_run_id, coalition)
        )

        latest_observation = None
        if observation is not None:
            latest_observation = {
                "observation_id": observation.id,
                "decision_cycle": observation.decision_cycle,
                "generated_at": observation.generated_at,
                "enemy_contact_count": len(observation.observation.enemy_contacts),
                "friendly_force_count": len(observation.observation.friendly_forces),
                "recent_change_count": len(observation.observation.recent_changes),
                "narrative": observation.narrative,
            }

        latest_invocation = None
        if invocation is not None:
            latest_invocation = {
                "invocation_id": invocation.id,
                "decision_cycle": invocation.decision_cycle,
                "backend_name": invocation.backend_name,
                "status": invocation.status,
                "parse_status": invocation.parse_status,
                "parsed_action_count": len(invocation.parsed_actions),
                "validation_batch_id": invocation.validation_batch_id,
                "error_detail": invocation.error_detail,
            }

        latest_validation = None
        if validation is not None:
            accepted = sum(1 for result in validation.results if result.status.value in {"accepted", "partially_accepted"})
            rejected = sum(1 for result in validation.results if result.status.value == "rejected")
            latest_validation = {
                "validation_batch_id": validation.id,
                "decision_cycle": validation.decision_cycle,
                "result_count": len(validation.results),
                "accepted_count": accepted,
                "rejected_count": rejected,
                "soft_cap_exceeded": validation.soft_cap_exceeded,
                "latest_observation_id": validation.latest_observation_id,
            }

        latest_execution = None
        if execution is not None:
            latest_execution = {
                "execution_batch_id": execution.id,
                "decision_cycle": execution.decision_cycle,
                "status": execution.status.value,
                "lifecycle_state": execution.lifecycle_state.value,
                "result_count": len(execution.results),
                "failure_count": sum(1 for result in execution.results if result.status.value == "failed"),
                "summary": execution.summary,
            }

        return CoalitionInspectionView(
            run_id=resolved_run_id,
            coalition=coalition,
            latest_observation=latest_observation,
            latest_model_invocation=latest_invocation,
            latest_action_validation=latest_validation,
            latest_execution=latest_execution,
            current_execution_notes=current_notes,
        )

    def export_replay_bundle(self, run_id: str | None, output_dir: str | Path) -> ReplayExportResult:
        resolved_run_id = run_id or self.store.get_latest_run_id()
        output_path = Path(output_dir)
        if output_path.exists() and any(output_path.iterdir()):
            raise PersistenceError(f"Replay export target '{output_path}' must be empty.")
        output_path.mkdir(parents=True, exist_ok=True)

        run = self.get_status(resolved_run_id)
        summary = self.summarize_run(resolved_run_id)
        timeline = self.list_timeline(resolved_run_id)
        red_inspection = self.inspect_coalition(resolved_run_id, Coalition.RED)
        blue_inspection = self.inspect_coalition(resolved_run_id, Coalition.BLUE)
        world_state = (
            self.world_repository.get_snapshot(resolved_run_id)
            if self.world_repository is not None
            else self.store.get_world_state_snapshot(resolved_run_id)
        )
        fusion_snapshots = self.store.list_fusion_snapshot_records(resolved_run_id)
        fusion_red = tuple(snapshot for snapshot in fusion_snapshots if snapshot.coalition is Coalition.RED)
        fusion_blue = tuple(snapshot for snapshot in fusion_snapshots if snapshot.coalition is Coalition.BLUE)
        observations = self.store.list_observations(resolved_run_id)
        invocations = self.store.list_model_invocations(resolved_run_id)
        validations = self.store.list_action_validation_batches(resolved_run_id)
        executions = self.store.list_execution_batches(resolved_run_id)
        events = self.store.list_events(resolved_run_id)
        standing_orders = self.store.get_active_execution_standing_orders(resolved_run_id)
        standing_order_history = self.store.list_execution_standing_order_history(resolved_run_id)
        evaluation_summary = None
        cycle_evaluations = ()
        fairness_findings = ()
        matrix_report = None
        try:
            evaluation_summary = self.store.get_evaluation_run_summary(resolved_run_id)
            cycle_evaluations = self.store.list_evaluation_cycle_metrics(resolved_run_id)
            fairness_findings = self.store.list_fairness_findings(resolved_run_id)
        except PersistenceError:
            evaluation_summary = None
        profile_name = (run.evaluation_metadata or {}).get("profile_name") if run.evaluation_metadata else None
        if profile_name:
            try:
                matrix_report = self.store.get_matrix_run_report(profile_name)
            except PersistenceError:
                matrix_report = None

        files = (
            "run.json",
            "scenario.json",
            "run_summary.json",
            "coalition_inspection_red.json",
            "coalition_inspection_blue.json",
            "world_state.json",
            "fusion_red.json",
            "fusion_blue.json",
            "standing_orders.json",
            "standing_order_history.jsonl",
            "decision_timeline.jsonl",
            "observations.jsonl",
            "model_invocations.jsonl",
            "action_validation_batches.jsonl",
            "execution_batches.jsonl",
            "events.jsonl",
            "manifest.json",
        )
        file_list = list(files)
        if evaluation_summary is not None:
            file_list.extend(
                [
                    "evaluation_summary.json",
                    "cycle_evaluations.jsonl",
                    "fairness_findings.jsonl",
                ]
            )
        if matrix_report is not None:
            file_list.append("matrix_report.json")
        files = tuple(file_list)

        self._write_json(output_path / "run.json", run)
        self._write_json(
            output_path / "scenario.json",
            {
                "scenario_id": run.scenario_id,
                "scenario_version": run.scenario_version,
                "scenario_name": run.scenario_name,
                "theater": run.theater,
            },
        )
        self._write_json(output_path / "run_summary.json", summary)
        self._write_json(output_path / "coalition_inspection_red.json", red_inspection)
        self._write_json(output_path / "coalition_inspection_blue.json", blue_inspection)
        self._write_json(output_path / "world_state.json", world_state)
        self._write_json(output_path / "fusion_red.json", fusion_red)
        self._write_json(output_path / "fusion_blue.json", fusion_blue)
        self._write_json(output_path / "standing_orders.json", standing_orders)
        self._write_jsonl(output_path / "standing_order_history.jsonl", standing_order_history)
        self._write_jsonl(output_path / "decision_timeline.jsonl", timeline)
        self._write_jsonl(output_path / "observations.jsonl", observations)
        self._write_jsonl(output_path / "model_invocations.jsonl", invocations)
        self._write_jsonl(output_path / "action_validation_batches.jsonl", validations)
        self._write_jsonl(output_path / "execution_batches.jsonl", executions)
        self._write_jsonl(output_path / "events.jsonl", events)
        if evaluation_summary is not None:
            self._write_json(output_path / "evaluation_summary.json", evaluation_summary)
            self._write_jsonl(output_path / "cycle_evaluations.jsonl", cycle_evaluations)
            self._write_jsonl(output_path / "fairness_findings.jsonl", fairness_findings)
        if matrix_report is not None:
            self._write_json(output_path / "matrix_report.json", matrix_report)

        manifest = ReplayBundleManifest(
            bundle_version="phase1_run_bundle_v1",
            run_id=resolved_run_id,
            scenario_id=run.scenario_id,
            exported_at=datetime.now(UTC),
            file_inventory=files,
            comparison_metadata={
                "scenario_id": run.scenario_id,
                "mode": run.mode,
                "status": run.status.value,
                "red_backend_name": run.red_backend_name,
                "blue_backend_name": run.blue_backend_name,
                "decision_cycle_count": summary.decision_cycle_count,
                "latest_decision_cycle": summary.latest_decision_cycle,
                "cycle_classification_counts": summary.cycle_classification_counts,
                "model_status_counts": summary.model_status_counts,
                "execution_status_counts": summary.execution_status_counts,
                "export_manifest_version": "phase1_run_bundle_v1",
            },
        )
        self._write_json(output_path / "manifest.json", manifest)
        return ReplayExportResult(
            run_id=resolved_run_id,
            output_dir=str(output_path),
            manifest_path=str(output_path / "manifest.json"),
            file_count=len(files),
        )

    def compare_runs(self, left_run_id: str, right_run_id: str) -> RunComparisonResult:
        left = self.summarize_run(left_run_id)
        right = self.summarize_run(right_run_id)
        return RunComparisonResult(
            left_run_id=left_run_id,
            right_run_id=right_run_id,
            scenario_ids=(left.run.scenario_id, right.run.scenario_id),
            theaters=(left.run.theater, right.run.theater),
            modes=(left.run.mode, right.run.mode),
            statuses=(left.run.status.value, right.run.status.value),
            backend_assignments={
                "red_backend_name": (left.run.red_backend_name, right.run.red_backend_name),
                "blue_backend_name": (left.run.blue_backend_name, right.run.blue_backend_name),
            },
            decision_cycle_counts=(left.decision_cycle_count, right.decision_cycle_count),
            latest_decision_cycles=(left.latest_decision_cycle, right.latest_decision_cycle),
            cycle_classification_counts=self._paired_counts(left.cycle_classification_counts, right.cycle_classification_counts),
            model_status_counts=self._paired_counts(left.model_status_counts, right.model_status_counts),
            execution_status_counts=self._paired_counts(left.execution_status_counts, right.execution_status_counts),
            failure_counts=self._paired_counts(left.failure_counts, right.failure_counts),
        )

    def _transition(
        self,
        run_id: str,
        target_status: RunLifecycleStatus,
        *,
        changed_at: datetime,
        reason: str | None = None,
    ) -> OperatorCommandResult:
        current = self.get_status(run_id)
        allowed = {
            RunLifecycleStatus.CREATED: {RunLifecycleStatus.RUNNING},
            RunLifecycleStatus.RUNNING: {RunLifecycleStatus.PAUSED, RunLifecycleStatus.STOPPED, RunLifecycleStatus.FAILED},
            RunLifecycleStatus.PAUSED: {RunLifecycleStatus.RUNNING, RunLifecycleStatus.STOPPED},
            RunLifecycleStatus.STOPPED: set(),
            RunLifecycleStatus.FAILED: set(),
        }
        if target_status not in allowed[current.status]:
            raise PersistenceError(
                f"Invalid run transition for '{run_id}': {current.status.value} -> {target_status.value}."
            )
        updated = self.store.update_run_status(run_id, target_status, changed_at=changed_at, terminal_reason=reason)
        detail = (
            reason
            if reason is not None
            else f"Transitioned run '{run_id}' from {current.status.value} to {updated.status.value}."
        )
        return OperatorCommandResult(
            run_id=run_id,
            previous_status=current.status,
            current_status=updated.status,
            detail=detail,
            changed_at=changed_at,
        )

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(_normalize(payload), indent=2, sort_keys=True), encoding="utf-8")

    def _write_jsonl(self, path: Path, rows: tuple[Any, ...] | list[Any]) -> None:
        serialized = "\n".join(json.dumps(_normalize(row), sort_keys=True) for row in rows)
        path.write_text(f"{serialized}\n" if serialized else "", encoding="utf-8")

    def _classify_issues(self, run_id: str, invocations: tuple[Any, ...], executions: tuple[Any, ...]) -> tuple[list[RunFailureEntry], list[RunWarningEntry]]:
        failures: list[RunFailureEntry] = []
        warnings: list[RunWarningEntry] = []
        for invocation in invocations:
            if invocation.status != "succeeded":
                target = failures if invocation.status in {"transport_failed", "parse_failed"} else warnings
                target.append(
                    (RunFailureEntry if target is failures else RunWarningEntry)(
                        subsystem="model_adapter",
                        coalition=invocation.coalition,
                        decision_cycle=invocation.decision_cycle,
                        detail=f"{invocation.status}:{invocation.error_detail or 'no detail'}",
                    )
                )
        for batch in executions:
            if batch.status.value == "failed":
                failures.append(
                    RunFailureEntry(
                        subsystem="execution",
                        coalition=batch.coalition,
                        decision_cycle=batch.decision_cycle,
                        detail=f"batch_failed:{batch.id}",
                    )
                )
            elif batch.status.value == "partially_succeeded":
                warnings.append(
                    RunWarningEntry(
                        subsystem="execution",
                        coalition=batch.coalition,
                        decision_cycle=batch.decision_cycle,
                        detail=f"batch_partial:{batch.id}",
                    )
                )
        return failures, warnings

    def _count_issue_types(self, entries: list[RunFailureEntry] | list[RunWarningEntry]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in entries:
            key = entry.subsystem
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _timeline_failures(self, run_id: str) -> dict[int, tuple[str, ...]]:
        failures, warnings = self._classify_issues(
            run_id,
            self.store.list_model_invocations(run_id),
            self.store.list_execution_batches(run_id),
        )
        grouped: dict[int, list[str]] = {}
        for entry in (*failures, *warnings):
            if entry.decision_cycle is None:
                continue
            grouped.setdefault(entry.decision_cycle, []).append(f"{entry.subsystem}:{entry.detail}")
        return {key: tuple(value) for key, value in grouped.items()}

    def _paired_counts(self, left: dict[str, int], right: dict[str, int]) -> dict[str, tuple[int, int]]:
        keys = sorted(set(left) | set(right))
        return {key: (left.get(key, 0), right.get(key, 0)) for key in keys}


__all__ = ["OperatorControlService"]
