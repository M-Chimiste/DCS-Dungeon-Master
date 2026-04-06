"""Existing-run continuation helpers for CLI and web workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dcs_dungeon_master.core.enums import RunLifecycleStatus, StartupIntent
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.core.models import ResumableRunSummary, RunContinuationView
from dcs_dungeon_master.operator_control import OperatorControlService
from dcs_dungeon_master.persistence import SQLiteStateStore


@dataclass(slots=True)
class RunContinuationService:
    store: SQLiteStateStore
    operator: OperatorControlService

    @property
    def status(self) -> str:
        return "run-continuation-ready"

    def list_runs(self) -> tuple[ResumableRunSummary, ...]:
        return tuple(self._build_summary(run.run_id) for run in self.store.list_run_control_states())

    def list_resumable_runs(self) -> tuple[ResumableRunSummary, ...]:
        return tuple(item for item in self.list_runs() if item.resumable)

    def get_latest_run(self) -> RunContinuationView:
        return self.open_run(self.store.get_latest_run_id(), startup_intent=StartupIntent.OPEN_LATEST)

    def get_latest_resumable_run(self) -> RunContinuationView:
        resumable = self.list_resumable_runs()
        if not resumable:
            raise PersistenceError("No resumable runs are available.")
        return self.open_run(resumable[0].run_id, startup_intent=StartupIntent.OPEN_LATEST)

    def open_run(self, run_id: str, *, startup_intent: StartupIntent = StartupIntent.OPEN_EXISTING) -> RunContinuationView:
        summary = self._build_summary(run_id)
        return RunContinuationView(
            run=self.store.get_run_control_state(run_id),
            startup_intent=startup_intent,
            latest_decision_cycle=summary.latest_decision_cycle,
            last_updated_at=summary.last_updated_at,
            resumable=summary.resumable,
            last_error_summary=summary.last_error_summary,
        )

    def continue_run(self, run_id: str) -> RunContinuationView:
        current = self.store.get_run_control_state(run_id)
        if current.status is RunLifecycleStatus.CREATED:
            self.operator.start_run(run_id)
        elif current.status is RunLifecycleStatus.PAUSED:
            self.operator.resume_run(run_id)
        elif current.status in {RunLifecycleStatus.STOPPED, RunLifecycleStatus.FAILED}:
            raise PersistenceError(f"Run '{run_id}' is terminal with status '{current.status.value}' and cannot continue.")
        return self.open_run(run_id, startup_intent=StartupIntent.OPEN_EXISTING)

    def resolve_startup_intent(self, intent: StartupIntent, *, run_id: str | None = None) -> RunContinuationView | None:
        if intent is StartupIntent.NEW:
            return None
        if intent is StartupIntent.OPEN_LATEST:
            return self.get_latest_run()
        if run_id is None:
            raise PersistenceError("run_id is required when opening an existing run.")
        return self.open_run(run_id, startup_intent=intent)

    def _build_summary(self, run_id: str) -> ResumableRunSummary:
        run = self.store.get_run_control_state(run_id)
        summary = self.operator.summarize_run(run_id)
        last_updated_at = self._last_updated_at(run_id, run)
        return ResumableRunSummary(
            run_id=run.run_id,
            scenario_id=run.scenario_id,
            scenario_name=run.scenario_name,
            theater=run.theater,
            mode=run.mode,
            status=run.status,
            created_at=run.created_at,
            last_updated_at=last_updated_at,
            latest_decision_cycle=summary.latest_decision_cycle,
            resumable=run.status in {RunLifecycleStatus.CREATED, RunLifecycleStatus.RUNNING, RunLifecycleStatus.PAUSED},
            red_backend_name=run.red_backend_name,
            blue_backend_name=run.blue_backend_name,
            terminal_reason=run.terminal_reason,
            last_error_summary=summary.last_error_summary,
        )

    def _last_updated_at(self, run_id: str, run_state) -> datetime:
        timestamps = [
            run_state.created_at,
            run_state.started_at,
            run_state.paused_at,
            run_state.resumed_at,
            run_state.stopped_at,
            run_state.failed_at,
        ]
        events = self.store.list_events(run_id)
        if events:
            timestamps.append(events[-1].occurred_at)
        return max(item for item in timestamps if item is not None)
