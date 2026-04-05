"""Evaluation harness, fairness review, and matrix execution services."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.config import AppConfig, ModelBackendConfig, ModelRoutingConfig
from dcs_dungeon_master.core.enums import (
    ActionType,
    Coalition,
    ConfidenceBand,
    EvaluationRating,
    EvaluationReviewPolicy,
    FairnessMachineStatus,
    FairnessMode,
    FairnessReviewStatus,
    MatrixCaseStatus,
    RejectionCode,
    RunLifecycleStatus,
    ValidationStatus,
)
from dcs_dungeon_master.core.models import (
    ActionValidationBatch,
    CommanderObservation,
    EvaluationComparisonReport,
    EvaluationCycleMetrics,
    EvaluationRunSummary,
    FairnessFinding,
    MatrixCaseResult,
    MatrixRunReport,
    ObservationArtifact,
    TuningNote,
)
from dcs_dungeon_master.core.versions import ACTION_SCHEMA_VERSION, OBSERVATION_SCHEMA_VERSION
from dcs_dungeon_master.execution import ExecutionEngine, LiveCommandLoopRunner
from dcs_dungeon_master.integration import IntegrationIngestCoordinator, build_integration_services
from dcs_dungeon_master.model_adapter import DryDecisionLoopRunner, build_model_registry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.operator_control import OperatorControlService
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.world_state import WorldStateRepository, WorldStateUpdater


def build_evaluation_metadata(
    config: AppConfig,
    *,
    red_backend_name: str | None = None,
    blue_backend_name: str | None = None,
    decision_cadence_sec: int | None = None,
    prompt_version: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    backend_by_name = {backend.name: backend for backend in config.models}
    red_name = red_backend_name or config.model_routing.red_backend
    blue_name = blue_backend_name or config.model_routing.blue_backend
    red_backend = backend_by_name[red_name]
    blue_backend = backend_by_name[blue_name]
    return {
        "profile_name": config.evaluation.profile_name,
        "decision_cadence_sec": decision_cadence_sec or config.evaluation.decision_cadence_sec,
        "prompt_version": prompt_version or config.evaluation.prompt_version,
        "resource_settings_version": config.evaluation.resource_settings_version,
        "review_policy": config.evaluation.review_policy.value,
        "fairness_mode": config.evaluation.fairness_mode.value,
        "default_run_cycles": config.evaluation.default_run_cycles,
        "scenario_seed": config.evaluation.scenario_seed,
        "notes": notes or config.evaluation.notes,
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "action_schema_version": ACTION_SCHEMA_VERSION,
        "red_model_identifier": red_backend.model,
        "blue_model_identifier": blue_backend.model,
        "red_hosting_mode": red_backend.hosting_mode.value,
        "blue_hosting_mode": blue_backend.hosting_mode.value,
    }


class EvaluationService:
    def __init__(self, store: SQLiteStateStore, scenario) -> None:
        self.store = store
        self.scenario = scenario
        self._control_point_by_id = {point.id: point for point in scenario.control_points}
        self._sector_by_id = {sector.id: sector for sector in scenario.sectors}

    @property
    def status(self) -> str:
        return "evaluation-ready"

    def evaluate_run(self, run_id: str) -> EvaluationRunSummary:
        run = self.store.get_run_control_state(run_id)
        observations = self.store.list_observations(run_id)
        invocations = self.store.list_model_invocations(run_id)
        validations = self.store.list_action_validation_batches(run_id)
        executions = self.store.list_execution_batches(run_id)
        cycles = self.store.list_decision_cycles(run_id)
        events = self.store.list_events(run_id)
        reserve_groups = self.store.get_reserve_groups(run_id)
        coalition_states = self.store.get_coalition_states(run_id)
        world_state = self.store.get_world_state_snapshot(run_id)
        existing_findings = {
            self._finding_key(item): item for item in self.store.list_fairness_findings(run_id)
        }
        generated_at = datetime.now(UTC)

        new_findings = self._build_fairness_findings(
            run_id,
            observations=observations,
            validations=validations,
            world_state=world_state,
            reserve_groups=reserve_groups,
            existing_findings=existing_findings,
            run_status=run.status,
        )
        persisted_findings = self.store.replace_fairness_findings(run_id, tuple(new_findings))
        cycle_metrics = self._build_cycle_metrics(
            run_id,
            observations=observations,
            validations=validations,
            executions=executions,
            findings=persisted_findings,
        )
        self.store.save_evaluation_cycle_metrics(run_id, cycle_metrics, generated_at=generated_at)
        summary = self._build_run_summary(
            run_id,
            generated_at=generated_at,
            run_metadata=run.evaluation_metadata or {},
            observations=observations,
            invocations=invocations,
            validations=validations,
            executions=executions,
            cycles=cycles,
            events=events,
            reserve_groups=reserve_groups,
            coalition_states=coalition_states,
            findings=persisted_findings,
            cycle_metrics=cycle_metrics,
            run_status=run.status,
        )
        self.store.save_evaluation_run_summary(summary)
        return summary

    def summarize_run(self, run_id: str) -> EvaluationRunSummary:
        return self.store.get_evaluation_run_summary(run_id)

    def compare_runs(self, left_run_id: str, right_run_id: str) -> EvaluationComparisonReport:
        left = self.store.get_evaluation_run_summary(left_run_id)
        right = self.store.get_evaluation_run_summary(right_run_id)
        loop_keys = ("cycles_completed", "model_timeouts", "unhandled_run_failures", "usable_decision_rate")
        strategic_keys = (
            "sector_priority_change_count",
            "control_point_owner_change_count",
            "successful_reposition_withdraw_under_threat_count",
            "contested_center_activity_cycles",
        )
        left_note_types = {note.note_type for note in left.tuning_notes}
        right_note_types = {note.note_type for note in right.tuning_notes}
        all_note_types = sorted(left_note_types | right_note_types)
        all_rejection_codes = sorted(set(left.top_rejection_codes) | set(right.top_rejection_codes))
        return EvaluationComparisonReport(
            left_run_id=left_run_id,
            right_run_id=right_run_id,
            generated_at=datetime.now(UTC),
            fairness_score_delta=right.fairness_score - left.fairness_score,
            loop_health_delta={
                key: float(right.loop_health.get(key, 0)) - float(left.loop_health.get(key, 0))
                for key in loop_keys
            },
            strategic_behavior_delta={
                key: float(right.strategic_behavior.get(key, 0)) - float(left.strategic_behavior.get(key, 0))
                for key in strategic_keys
            },
            rejection_code_shift={
                code: (left.top_rejection_codes.get(code, 0), right.top_rejection_codes.get(code, 0))
                for code in all_rejection_codes
            },
            tuning_note_changes={
                note_type: (note_type in left_note_types, note_type in right_note_types) for note_type in all_note_types
            },
        )

    def review_queue(self) -> dict[str, Any]:
        pending_findings = self.store.list_fairness_review_queue()
        failed_runs = []
        seen_runs: set[str] = set()
        for finding in pending_findings:
            if finding.run_id not in seen_runs:
                seen_runs.add(finding.run_id)
        for run_id in sorted(seen_runs):
            run = self.store.get_run_control_state(run_id)
            summary = self.store.get_evaluation_run_summary(run_id)
            failed_runs.append(
                {
                    "run_id": run_id,
                    "run_status": run.status.value,
                    "fairness_rating": summary.fairness_rating.value,
                    "pending_findings": summary.findings_pending_review,
                }
            )
        for run in self._failed_runs_without_pending_findings():
            failed_runs.append(run)
        return {
            "pending_findings": tuple(pending_findings),
            "failed_runs": tuple(failed_runs),
        }

    def review_finding(self, finding_id: int, *, status: FairnessReviewStatus, note: str | None) -> dict[str, Any]:
        finding = self.store.update_fairness_finding_review(
            finding_id,
            review_status=status,
            reviewer_note=note,
            reviewed_at=datetime.now(UTC),
        )
        summary = self.evaluate_run(finding.run_id)
        return {"finding": finding, "summary": summary}

    def run_matrix(self, config: AppConfig) -> MatrixRunReport:
        if not config.evaluation.matrix_cases:
            raise ValueError("Evaluation matrix cases are not configured.")

        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        results: list[MatrixCaseResult] = []
        output_root = (
            Path("artifacts/eval")
            / config.evaluation.profile_name
            / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        )
        output_root.mkdir(parents=True, exist_ok=True)
        operator = OperatorControlService(self.store)
        for case in config.evaluation.matrix_cases:
            for repeat_index in range(1, case.repeat_count + 1):
                if case.visual_attachment:
                    case_result = MatrixCaseResult(
                        id=None,
                        profile_name=config.evaluation.profile_name,
                        case_id=case.id,
                        description=case.description,
                        repeat_index=repeat_index,
                        status=MatrixCaseStatus.SKIPPED_UNSUPPORTED,
                        mode=case.mode,
                        red_backend=case.red_backend,
                        blue_backend=case.blue_backend,
                        decision_cadence_sec=case.decision_cadence_sec,
                        run_cycles=case.run_cycles,
                        detail="visual attachments are not implemented in Phase 1 evaluation runs",
                    )
                    case_id = self.store.save_matrix_case_result(case_result)
                    results.append(replace(case_result, id=case_id))
                    continue

                case_config = _config_for_matrix_case(config, case.id)
                evaluation_metadata = build_evaluation_metadata(
                    case_config,
                    red_backend_name=case.red_backend,
                    blue_backend_name=case.blue_backend,
                    decision_cadence_sec=case.decision_cadence_sec,
                    prompt_version=case.system_prompt_variant_override or case_config.evaluation.prompt_version,
                    notes=f"matrix_case={case.id};repeat={repeat_index}",
                )
                run_id = self.store.create_run_from_scenario(
                    scenario,
                    mode=case.mode,
                    config_digest=_config_digest(case_config),
                    config_snapshot=case_config.to_dict(),
                    red_backend_name=case.red_backend,
                    blue_backend_name=case.blue_backend,
                    evaluation_metadata=evaluation_metadata,
                )
                try:
                    if case.mode == "dry":
                        self._run_dry_case(case_config, scenario, run_id, case.run_cycles, case.decision_cadence_sec)
                    else:
                        self._run_live_case(case_config, scenario, run_id, case.run_cycles, case.decision_cadence_sec)
                    summary = self.evaluate_run(run_id)
                    operator.export_replay_bundle(run_id, output_root / f"{case.id}_repeat_{repeat_index}")
                    case_result = MatrixCaseResult(
                        id=None,
                        profile_name=config.evaluation.profile_name,
                        case_id=case.id,
                        description=case.description,
                        repeat_index=repeat_index,
                        status=MatrixCaseStatus.COMPLETED,
                        mode=case.mode,
                        red_backend=case.red_backend,
                        blue_backend=case.blue_backend,
                        decision_cadence_sec=case.decision_cadence_sec,
                        run_cycles=case.run_cycles,
                        run_id=run_id,
                        evaluation_run_id=summary.run_id,
                        detail=f"fairness_score={summary.fairness_score}",
                    )
                except Exception as exc:  # noqa: BLE001
                    state = self.store.get_run_control_state(run_id)
                    if state.status in {RunLifecycleStatus.CREATED, RunLifecycleStatus.RUNNING}:
                        self.store.update_run_status(run_id, RunLifecycleStatus.FAILED, changed_at=datetime.now(UTC), terminal_reason=str(exc))
                    case_result = MatrixCaseResult(
                        id=None,
                        profile_name=config.evaluation.profile_name,
                        case_id=case.id,
                        description=case.description,
                        repeat_index=repeat_index,
                        status=MatrixCaseStatus.FAILED,
                        mode=case.mode,
                        red_backend=case.red_backend,
                        blue_backend=case.blue_backend,
                        decision_cadence_sec=case.decision_cadence_sec,
                        run_cycles=case.run_cycles,
                        run_id=run_id,
                        detail=str(exc),
                    )
                result_id = self.store.save_matrix_case_result(case_result)
                results.append(replace(case_result, id=result_id))

        report = MatrixRunReport(
            profile_name=config.evaluation.profile_name,
            generated_at=datetime.now(UTC),
            case_results=tuple(results),
            completed_case_count=sum(1 for item in results if item.status is MatrixCaseStatus.COMPLETED),
            failed_case_count=sum(1 for item in results if item.status is MatrixCaseStatus.FAILED),
            skipped_case_count=sum(1 for item in results if item.status is MatrixCaseStatus.SKIPPED_UNSUPPORTED),
        )
        self.store.save_matrix_run_report(report)
        (output_root / "matrix_report.json").write_text(json.dumps(asdict(report), indent=2, sort_keys=True, default=str), encoding="utf-8")
        (output_root / "SUMMARY.md").write_text(self._render_matrix_summary(report), encoding="utf-8")
        return report

    def get_matrix_report(self, profile_name: str) -> MatrixRunReport:
        return self.store.get_matrix_run_report(profile_name)

    def milestone9_completion_status(self, run_id: str | None = None, profile_name: str | None = None) -> dict[str, Any]:
        resolved_run_id = run_id or self.store.get_latest_run_id()
        summary = self.store.get_evaluation_run_summary(resolved_run_id)
        findings = self.store.list_fairness_findings(resolved_run_id)
        replay_exports = self.store.list_replay_export_results(resolved_run_id)
        try:
            matrix_report = self.store.get_matrix_run_report(profile_name or summary.run_metadata.get("profile_name", ""))
        except Exception:  # noqa: BLE001
            matrix_report = None
        live_case_completed = (
            matrix_report is not None
            and any(item.status is MatrixCaseStatus.COMPLETED and item.mode == "live" for item in matrix_report.case_results)
        )
        evaluation_artifacts_exported = any(
            export.includes_evaluation_summary
            and export.includes_cycle_evaluations
            and export.includes_fairness_findings
            for export in replay_exports
        )
        return {
            "run_id": resolved_run_id,
            "profile_name": profile_name or summary.run_metadata.get("profile_name"),
            "evaluation_summary_present": True,
            "fairness_finding_count": len(findings),
            "matrix_report_present": matrix_report is not None,
            "replay_export_present": bool(replay_exports),
            "evaluation_artifacts_exported": evaluation_artifacts_exported,
            "completed_live_case_count": (
                sum(1 for item in matrix_report.case_results if item.status is MatrixCaseStatus.COMPLETED and item.mode == "live")
                if matrix_report is not None
                else 0
            ),
            "operational_baseline_evidence_present": live_case_completed,
            "code_closeout_ready": bool(matrix_report is not None and evaluation_artifacts_exported),
            "findings_pending_review": summary.findings_pending_review,
            "fairness_rating": summary.fairness_rating.value,
        }

    def _build_run_summary(
        self,
        run_id: str,
        *,
        generated_at: datetime,
        run_metadata: dict[str, Any],
        observations: tuple[ObservationArtifact, ...],
        invocations: tuple[Any, ...],
        validations: tuple[ActionValidationBatch, ...],
        executions: tuple[Any, ...],
        cycles: tuple[Any, ...],
        events: tuple[Any, ...],
        reserve_groups: tuple[Any, ...],
        coalition_states: tuple[Any, ...],
        findings: tuple[FairnessFinding, ...],
        cycle_metrics: tuple[EvaluationCycleMetrics, ...],
        run_status: RunLifecycleStatus,
    ) -> EvaluationRunSummary:
        proposed_actions = sum(len(batch.raw_payload) for batch in validations)
        accepted_actions = sum(
            1 for batch in validations for result in batch.results if result.status in {ValidationStatus.ACCEPTED, ValidationStatus.PARTIALLY_ACCEPTED}
        )
        rejected_actions = sum(1 for batch in validations for result in batch.results if result.status is ValidationStatus.REJECTED)
        rejection_counts: dict[str, int] = {}
        schema_rejections = 0
        conflict_rejections = 0
        for batch in validations:
            for result in batch.results:
                if result.rejection_code is not None:
                    rejection_counts[result.rejection_code.value] = rejection_counts.get(result.rejection_code.value, 0) + 1
                    if result.rejection_code in {
                        RejectionCode.MISSING_REQUIRED_FIELD,
                        RejectionCode.INVALID_FIELD_VALUE,
                        RejectionCode.UNKNOWN_ACTION_TYPE,
                    }:
                        schema_rejections += 1
                    if result.rejection_code is RejectionCode.ACTION_CONFLICT:
                        conflict_rejections += 1
        usable_cycles = 0
        for batch in validations:
            if any(result.status in {ValidationStatus.ACCEPTED, ValidationStatus.PARTIALLY_ACCEPTED} for result in batch.results):
                usable_cycles += 1
                continue
            if any(action.get("action_type") == ActionType.HOLD_ACTION.value for action in batch.raw_payload):
                usable_cycles += 1
        initial_budgets = {state.coalition.value: state.budget_remaining for state in self.scenario.coalitions}
        current_budgets = {state.coalition.value: state.budget_remaining for state in coalition_states}
        budget_spent = {
            coalition: max(0, initial_budgets.get(coalition, 0) - current_budgets.get(coalition, 0))
            for coalition in initial_budgets
        }
        reserve_commitments = {Coalition.RED.value: 0, Coalition.BLUE.value: 0}
        first_reserve_commitment: dict[str, int | None] = {Coalition.RED.value: None, Coalition.BLUE.value: None}
        for batch in validations:
            for result in batch.results:
                if result.action_type is ActionType.DEPLOY_RESERVE_GROUP and result.status in {
                    ValidationStatus.ACCEPTED,
                    ValidationStatus.PARTIALLY_ACCEPTED,
                }:
                    coalition_key = batch.coalition.value
                    reserve_commitments[coalition_key] += 1
                    if first_reserve_commitment[coalition_key] is None:
                        first_reserve_commitment[coalition_key] = batch.decision_cycle
        total_reserves = {
            coalition.value: sum(1 for reserve in reserve_groups if reserve.coalition is coalition)
            for coalition in Coalition
        }
        immediate_reserve_dumping = {
            coalition.value: reserve_commitments[coalition.value] > (total_reserves[coalition.value] / 2 if total_reserves[coalition.value] else 10**9)
            and (first_reserve_commitment[coalition.value] or 999) <= 5
            for coalition in Coalition
        }
        sector_priority_change_count = sum(
            1
            for batch in validations
            for result in batch.results
            if result.action_type is ActionType.SET_SECTOR_PRIORITY
            and result.status in {ValidationStatus.ACCEPTED, ValidationStatus.PARTIALLY_ACCEPTED}
        )
        central_control_share = self._central_control_share(observations)
        control_point_owner_change_count = sum(item.control_point_owner_changes for item in cycle_metrics)
        successful_reposition_withdraw_count = sum(
            1
            for batch in executions
            for result in batch.results
            if result.action_type in {ActionType.REPOSITION_GROUP, ActionType.WITHDRAW_GROUP}
            and result.status.value in {"succeeded", "partially_succeeded"}
        )
        contested_center_activity_cycles = self._count_contested_center_activity(observations)
        avg_staleness = (
            sum(item.average_enemy_contact_staleness_sec for item in cycle_metrics) / len(cycle_metrics)
            if cycle_metrics
            else 0.0
        )
        low_confidence_decision_total = sum(item.low_confidence_enemy_decision_count for item in cycle_metrics)
        duplicate_action_total = sum(item.duplicate_action_count for item in cycle_metrics)
        model_timeouts = sum(
            1
            for invocation in invocations
            if invocation.error_detail and "timeout" in invocation.error_detail.lower()
        )
        integration_interruptions = sum(1 for event in events if "integration" in event.event_type)
        fairness_score = self._score_fairness(findings)
        fairness_rating = self._rate_fairness(fairness_score)
        pending_findings = sum(1 for item in findings if item.review_status is FairnessReviewStatus.PENDING_REVIEW)
        tuning_notes = self._build_tuning_notes(
            run_status=run_status,
            schema_rejection_rate=(schema_rejections / rejected_actions) if rejected_actions else 0.0,
            duplicate_conflict_rate=((duplicate_action_total + conflict_rejections) / proposed_actions) if proposed_actions else 0.0,
            timeout_rate=(model_timeouts / len(invocations)) if invocations else 0.0,
            immediate_reserve_dumping=immediate_reserve_dumping,
            sector_priority_change_count=sector_priority_change_count,
            reserve_commitments=reserve_commitments,
        )
        return EvaluationRunSummary(
            run_id=run_id,
            generated_at=generated_at,
            profile_name=run_metadata.get("profile_name"),
            decision_cadence_sec=run_metadata.get("decision_cadence_sec"),
            prompt_version=run_metadata.get("prompt_version"),
            resource_settings_version=run_metadata.get("resource_settings_version"),
            fairness_mode=FairnessMode(run_metadata.get("fairness_mode", FairnessMode.HYBRID.value)),
            review_policy=EvaluationReviewPolicy(self._review_policy_value(run_metadata)),
            loop_health={
                "cycles_completed": len(cycles),
                "unhandled_run_failures": int(run_status is RunLifecycleStatus.FAILED),
                "model_timeouts": model_timeouts,
                "integration_interruptions": integration_interruptions,
                "usable_decision_rate": (usable_cycles / len(validations)) if validations else 0.0,
            },
            action_quality={
                "total_actions_proposed": proposed_actions,
                "total_actions_accepted": accepted_actions,
                "total_actions_rejected": rejected_actions,
                "rejection_rate_by_code": rejection_counts,
                "average_accepted_actions_per_cycle": (accepted_actions / len(cycles)) if cycles else 0.0,
                "average_rejected_actions_per_cycle": (rejected_actions / len(cycles)) if cycles else 0.0,
                "conflict_rate": (conflict_rejections / proposed_actions) if proposed_actions else 0.0,
                "duplicate_adjacent_cycle_rate": (duplicate_action_total / proposed_actions) if proposed_actions else 0.0,
            },
            resource_discipline={
                "budget_spent_by_coalition": budget_spent,
                "reserve_commitments_by_coalition": reserve_commitments,
                "time_to_first_reserve_commitment_by_coalition": first_reserve_commitment,
                "immediate_reserve_dumping_by_coalition": immediate_reserve_dumping,
                "high_value_asset_loss_count": sum(len(item.observation.resource_state.recently_lost_assets) for item in observations),
            },
            strategic_behavior={
                "sector_priority_change_count": sector_priority_change_count,
                "central_objective_control_share_by_coalition": central_control_share,
                "control_point_owner_change_count": control_point_owner_change_count,
                "successful_reposition_withdraw_under_threat_count": successful_reposition_withdraw_count,
                "contested_center_activity_cycles": contested_center_activity_cycles,
            },
            observation_integrity={
                "low_confidence_enemy_decision_rate": (low_confidence_decision_total / accepted_actions) if accepted_actions else 0.0,
                "average_contact_staleness_at_decision_time": avg_staleness,
                "suspicious_finding_count": sum(1 for item in findings if item.machine_status is FairnessMachineStatus.SUSPICIOUS),
            },
            fairness_score=fairness_score,
            fairness_rating=fairness_rating,
            suspicious_finding_count=sum(1 for item in findings if item.machine_status is FairnessMachineStatus.SUSPICIOUS),
            findings_pending_review=pending_findings,
            top_rejection_codes=dict(sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0]))[:5]),
            tuning_notes=tuple(tuning_notes),
        )

    def _build_cycle_metrics(
        self,
        run_id: str,
        *,
        observations: tuple[ObservationArtifact, ...],
        validations: tuple[ActionValidationBatch, ...],
        executions: tuple[Any, ...],
        findings: tuple[FairnessFinding, ...],
    ) -> tuple[EvaluationCycleMetrics, ...]:
        observation_by_key = {(item.coalition, item.decision_cycle): item for item in observations}
        previous_signatures: dict[Coalition, set[str]] = {Coalition.RED: set(), Coalition.BLUE: set()}
        metrics: list[EvaluationCycleMetrics] = []
        findings_by_cycle: dict[int, int] = defaultdict(int)
        for finding in findings:
            if finding.decision_cycle is not None:
                findings_by_cycle[finding.decision_cycle] += 1
        for batch in validations:
            observation = observation_by_key.get((batch.coalition, batch.decision_cycle))
            signatures = {
                self._action_signature(result.action_type, result.normalized_params)
                for result in batch.results
                if result.status in {ValidationStatus.ACCEPTED, ValidationStatus.PARTIALLY_ACCEPTED}
            }
            duplicate_count = len(signatures & previous_signatures[batch.coalition])
            previous_signatures[batch.coalition] = signatures
            conflict_count = sum(1 for result in batch.results if result.rejection_code is RejectionCode.ACTION_CONFLICT)
            low_confidence_decisions = 0
            staleness_values: list[int] = []
            if observation is not None:
                low_confidence_sectors = {
                    contact.last_known_sector or contact.estimated_sector
                    for contact in observation.observation.enemy_contacts
                    if contact.confidence is ConfidenceBand.LOW or contact.stale
                }
                for contact in observation.observation.enemy_contacts:
                    staleness_values.append(contact.time_since_last_confirmation_sec)
                for result in batch.results:
                    target_sector = self._result_target_sector(result)
                    if target_sector and target_sector in low_confidence_sectors:
                        low_confidence_decisions += 1
            control_point_changes = sum(
                1 for change in (observation.observation.recent_changes if observation is not None else ()) if change.startswith("control_point_")
            )
            metrics.append(
                EvaluationCycleMetrics(
                    run_id=run_id,
                    decision_cycle=batch.decision_cycle,
                    coalition_action_counts={
                        batch.coalition.value: {
                            "proposed": len(batch.raw_payload),
                            "accepted": sum(
                                1
                                for result in batch.results
                                if result.status in {ValidationStatus.ACCEPTED, ValidationStatus.PARTIALLY_ACCEPTED}
                            ),
                            "rejected": sum(1 for result in batch.results if result.status is ValidationStatus.REJECTED),
                            "executed": sum(1 for execution in executions if execution.coalition is batch.coalition and execution.decision_cycle == batch.decision_cycle),
                        }
                    },
                    duplicate_action_count=duplicate_count,
                    conflict_action_count=conflict_count,
                    low_confidence_enemy_decision_count=low_confidence_decisions,
                    average_enemy_contact_staleness_sec=(sum(staleness_values) / len(staleness_values)) if staleness_values else 0.0,
                    suspicious_finding_count=findings_by_cycle.get(batch.decision_cycle, 0),
                    control_point_owner_changes=control_point_changes,
                )
            )
        return tuple(metrics)

    def _build_fairness_findings(
        self,
        run_id: str,
        *,
        observations: tuple[ObservationArtifact, ...],
        validations: tuple[ActionValidationBatch, ...],
        world_state,
        reserve_groups: tuple[Any, ...],
        existing_findings: dict[tuple[Any, ...], FairnessFinding],
        run_status: RunLifecycleStatus,
    ) -> tuple[FairnessFinding, ...]:
        del reserve_groups
        observation_by_key = {(item.coalition, item.decision_cycle): item for item in observations}
        enemy_world_sectors = {
            Coalition.RED: {group.sector_id for group in world_state.groups if group.coalition is Coalition.BLUE and group.sector_id},
            Coalition.BLUE: {group.sector_id for group in world_state.groups if group.coalition is Coalition.RED and group.sector_id},
        }
        low_confidence_reaction_counts: dict[Coalition, int] = defaultdict(int)
        findings: list[FairnessFinding] = []
        for batch in validations:
            observation = observation_by_key.get((batch.coalition, batch.decision_cycle))
            if observation is None:
                continue
            visible_enemy_sectors = {
                contact.last_known_sector or contact.estimated_sector
                for contact in observation.observation.enemy_contacts
                if (contact.last_known_sector or contact.estimated_sector)
            }
            visible_enemy_sectors.update(
                entry.sector_id for entry in observation.observation.sector_summary if entry.enemy_threat != "low"
            )
            stale_low_confidence_sectors = {
                contact.last_known_sector or contact.estimated_sector
                for contact in observation.observation.enemy_contacts
                if (contact.confidence is ConfidenceBand.LOW or contact.stale) and (contact.last_known_sector or contact.estimated_sector)
            }
            friendly_by_id = {force.force_id: force for force in observation.observation.friendly_forces}
            for result in batch.results:
                if result.status not in {ValidationStatus.ACCEPTED, ValidationStatus.PARTIALLY_ACCEPTED}:
                    continue
                target_sector = self._result_target_sector(result)
                if target_sector and target_sector in stale_low_confidence_sectors:
                    low_confidence_reaction_counts[batch.coalition] += 1
                    findings.append(
                        self._reuse_or_create_finding(
                            existing_findings,
                            FairnessFinding(
                                id=None,
                                run_id=run_id,
                                coalition=batch.coalition,
                                decision_cycle=batch.decision_cycle,
                                finding_type="stale_precision",
                                machine_status=FairnessMachineStatus.SUSPICIOUS,
                                review_status=FairnessReviewStatus.PENDING_REVIEW,
                                detail=f"Action '{result.action_id}' targeted stale low-confidence sector '{target_sector}'.",
                                score_penalty=15,
                                finding_data={"target_sector": target_sector, "action_type": result.action_type.value if result.action_type else None},
                            ),
                        )
                    )
                if target_sector and target_sector in enemy_world_sectors[batch.coalition] and target_sector not in visible_enemy_sectors:
                    findings.append(
                        self._reuse_or_create_finding(
                            existing_findings,
                            FairnessFinding(
                                id=None,
                                run_id=run_id,
                                coalition=batch.coalition,
                                decision_cycle=batch.decision_cycle,
                                finding_type="hidden_targeting",
                                machine_status=FairnessMachineStatus.SUSPICIOUS,
                                review_status=FairnessReviewStatus.PENDING_REVIEW,
                                detail=f"Action '{result.action_id}' reacted to hidden enemy presence in sector '{target_sector}'.",
                                score_penalty=25,
                                finding_data={"target_sector": target_sector, "action_type": result.action_type.value if result.action_type else None},
                            ),
                        )
                    )
                if result.action_type in {ActionType.WITHDRAW_GROUP, ActionType.REPOSITION_GROUP}:
                    group_id = result.normalized_params.get("group_id")
                    source_sector = friendly_by_id.get(group_id).assigned_sector if group_id in friendly_by_id else None
                    if source_sector and source_sector not in visible_enemy_sectors and source_sector in enemy_world_sectors[batch.coalition]:
                        findings.append(
                            self._reuse_or_create_finding(
                                existing_findings,
                                FairnessFinding(
                                    id=None,
                                    run_id=run_id,
                                    coalition=batch.coalition,
                                    decision_cycle=batch.decision_cycle,
                                    finding_type="preemptive_ambush_avoidance",
                                    machine_status=FairnessMachineStatus.SUSPICIOUS,
                                    review_status=FairnessReviewStatus.PENDING_REVIEW,
                                    detail=f"Action '{result.action_id}' withdrew from hidden-threat sector '{source_sector}'.",
                                    score_penalty=20,
                                    finding_data={"source_sector": source_sector, "action_type": result.action_type.value if result.action_type else None},
                                ),
                            )
                        )
        for coalition, count in low_confidence_reaction_counts.items():
            if count >= 3:
                findings.append(
                    self._reuse_or_create_finding(
                        existing_findings,
                        FairnessFinding(
                            id=None,
                            run_id=run_id,
                            coalition=coalition,
                            decision_cycle=None,
                            finding_type="repeated_low_confidence_reaction",
                            machine_status=FairnessMachineStatus.SUSPICIOUS,
                            review_status=FairnessReviewStatus.PENDING_REVIEW,
                            detail=f"{coalition.value} repeatedly reacted to low-confidence contact sectors ({count} times).",
                            score_penalty=min(30, 10 * count),
                            finding_data={"count": count},
                        ),
                    )
                )
        if run_status is RunLifecycleStatus.FAILED and not findings:
            findings.append(
                self._reuse_or_create_finding(
                    existing_findings,
                    FairnessFinding(
                        id=None,
                        run_id=run_id,
                        coalition=None,
                        decision_cycle=None,
                        finding_type="run_failed_requires_review",
                        machine_status=FairnessMachineStatus.SUSPICIOUS,
                        review_status=FairnessReviewStatus.PENDING_REVIEW,
                        detail="Run failed and requires operator review.",
                        score_penalty=0,
                        finding_data={},
                    ),
                )
            )
        return tuple(findings)

    def _run_dry_case(self, config: AppConfig, scenario, run_id: str, cycles: int, cadence_sec: int) -> None:
        store = self.store
        world_repository = WorldStateRepository(store)
        world_updater = WorldStateUpdater(world_repository, scenario)
        observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        validator = ActionValidator(store, scenario)
        registry = build_model_registry(config)
        integrations = build_integration_services(config.dcs)
        try:
            runner = DryDecisionLoopRunner(
                store,
                observation_builder,
                validator,
                registry,
                IntegrationIngestCoordinator(integrations, world_updater),
            )
            runner.run_decision_loop(
                run_id,
                start_decision_cycle=1,
                cycles=cycles,
                seconds_since_last_cycle=cadence_sec,
            )
        finally:
            registry.close()
            integrations.close()

    def _run_live_case(self, config: AppConfig, scenario, run_id: str, cycles: int, cadence_sec: int) -> None:
        store = self.store
        world_repository = WorldStateRepository(store)
        world_updater = WorldStateUpdater(world_repository, scenario)
        observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        validator = ActionValidator(store, scenario)
        registry = build_model_registry(config)
        integrations = build_integration_services(config.dcs)
        try:
            runner = LiveCommandLoopRunner(
                store,
                observation_builder,
                validator,
                registry,
                ExecutionEngine(store, scenario, integrations.olympus),
                IntegrationIngestCoordinator(integrations, world_updater),
            )
            runner.run_live_loop(
                run_id,
                start_decision_cycle=1,
                cycles=cycles,
                seconds_since_last_cycle=cadence_sec,
            )
        finally:
            registry.close()
            integrations.close()

    def _render_matrix_summary(self, report: MatrixRunReport) -> str:
        lines = [
            f"# {report.profile_name} Matrix Summary",
            "",
            f"Generated: {report.generated_at.isoformat()}",
            "",
            f"- Completed cases: {report.completed_case_count}",
            f"- Failed cases: {report.failed_case_count}",
            f"- Skipped cases: {report.skipped_case_count}",
            "",
        ]
        for item in report.case_results:
            lines.append(
                f"- {item.case_id} repeat {item.repeat_index}: {item.status.value}"
                + (f" ({item.detail})" if item.detail else "")
            )
        return "\n".join(lines) + "\n"

    def _central_control_share(self, observations: tuple[ObservationArtifact, ...]) -> dict[str, float]:
        high_points = {point.id for point in self.scenario.control_points if point.strategic_value == "high"}
        per_coalition = {Coalition.RED.value: [], Coalition.BLUE.value: []}
        for observation in observations:
            relevant = [
                item
                for item in observation.observation.scenario_state.known_control_points
                if item.id in high_points
            ]
            if not relevant:
                continue
            friendly_count = sum(1 for item in relevant if item.status == "friendly")
            per_coalition[observation.coalition.value].append(friendly_count / len(relevant))
        return {
            coalition: (sum(values) / len(values) if values else 0.0) for coalition, values in per_coalition.items()
        }

    def _count_contested_center_activity(self, observations: tuple[ObservationArtifact, ...]) -> int:
        contested_sectors = {sector.id for sector in self.scenario.sectors if sector.role == "contested"}
        active_cycles: set[tuple[Coalition, int]] = set()
        for observation in observations:
            for sector in observation.observation.sector_summary:
                if sector.sector_id in contested_sectors and (
                    sector.enemy_threat in {"medium", "high"} or sector.activity_level in {"active", "high"}
                ):
                    active_cycles.add((observation.coalition, observation.decision_cycle))
        return len(active_cycles)

    def _score_fairness(self, findings: tuple[FairnessFinding, ...]) -> int:
        score = 100
        for finding in findings:
            if finding.review_status is FairnessReviewStatus.CLEARED:
                continue
            if finding.machine_status is FairnessMachineStatus.SUSPICIOUS:
                score -= finding.score_penalty
        return max(0, min(100, score))

    def _rate_fairness(self, score: int) -> EvaluationRating:
        if score >= 90:
            return EvaluationRating.EXCELLENT
        if score >= 75:
            return EvaluationRating.ACCEPTABLE
        if score >= 60:
            return EvaluationRating.BORDERLINE
        return EvaluationRating.FAILED

    def _build_tuning_notes(
        self,
        *,
        run_status: RunLifecycleStatus,
        schema_rejection_rate: float,
        duplicate_conflict_rate: float,
        timeout_rate: float,
        immediate_reserve_dumping: dict[str, bool],
        sector_priority_change_count: int,
        reserve_commitments: dict[str, int],
    ) -> tuple[TuningNote, ...]:
        notes: list[TuningNote] = []
        if schema_rejection_rate > 0.2:
            notes.append(TuningNote("schema_prompt_issue", "medium", "More than 20% of rejected actions were schema misuse errors."))
        if duplicate_conflict_rate > 0.15:
            notes.append(TuningNote("cadence_too_fast", "medium", "Duplicate/conflicting adjacent-cycle actions exceed 15%."))
        if timeout_rate > 0.1:
            notes.append(TuningNote("backend_timeout_pressure", "high", "Model timeouts exceed 10% of invocations."))
        if any(immediate_reserve_dumping.values()):
            notes.append(TuningNote("reserve_dumping", "high", "A coalition committed more than half its reserves by cycle 5."))
        if run_status is not RunLifecycleStatus.FAILED and sector_priority_change_count <= 1 and sum(reserve_commitments.values()) == 0:
            notes.append(TuningNote("strategy_inert", "medium", "The run showed very low reprioritization and no meaningful reserve use."))
        return tuple(notes)

    def _result_target_sector(self, result) -> str | None:
        if "sector_id" in result.normalized_params:
            return result.normalized_params["sector_id"]
        if result.normalized_params.get("destination_type") == "sector":
            return result.normalized_params.get("destination_id")
        if result.normalized_params.get("destination_type") == "control_point":
            control_point_id = result.normalized_params.get("destination_id")
            return self._control_point_by_id[control_point_id].sector_id if control_point_id in self._control_point_by_id else None
        if result.normalized_params.get("destination_type") == "zone":
            zone_id = result.normalized_params.get("destination_id")
            for zone in self.scenario.zones:
                if zone.id == zone_id:
                    return zone.sector_id
        control_point_id = result.normalized_params.get("control_point_id")
        if control_point_id and control_point_id in self._control_point_by_id:
            return self._control_point_by_id[control_point_id].sector_id
        return None

    def _action_signature(self, action_type: ActionType | None, normalized_params: dict[str, Any]) -> str:
        return json.dumps(
            {"action_type": action_type.value if action_type else None, "params": normalized_params},
            sort_keys=True,
            default=str,
        )

    def _reuse_or_create_finding(
        self,
        existing_findings: dict[tuple[Any, ...], FairnessFinding],
        finding: FairnessFinding,
    ) -> FairnessFinding:
        existing = existing_findings.get(self._finding_key(finding))
        if existing is None:
            return finding
        return FairnessFinding(
            id=existing.id,
            run_id=finding.run_id,
            coalition=finding.coalition,
            decision_cycle=finding.decision_cycle,
            finding_type=finding.finding_type,
            machine_status=finding.machine_status,
            review_status=existing.review_status,
            detail=finding.detail,
            score_penalty=finding.score_penalty,
            finding_data=finding.finding_data,
            reviewer_note=existing.reviewer_note,
            reviewed_at=existing.reviewed_at,
        )

    def _finding_key(self, finding: FairnessFinding) -> tuple[Any, ...]:
        return (
            finding.coalition.value if finding.coalition else None,
            finding.decision_cycle,
            finding.finding_type,
            finding.detail,
        )

    def _failed_runs_without_pending_findings(self) -> tuple[dict[str, Any], ...]:
        rows = []
        for candidate in self.store.list_run_ids():
            state = self.store.get_run_control_state(candidate)
            if state.status is RunLifecycleStatus.FAILED:
                try:
                    summary = self.store.get_evaluation_run_summary(candidate)
                except Exception:  # noqa: BLE001
                    continue
                if summary.findings_pending_review == 0:
                    rows.append(
                        {
                            "run_id": candidate,
                            "run_status": state.status.value,
                            "fairness_rating": summary.fairness_rating.value,
                            "pending_findings": 0,
                        }
                    )
        return tuple(rows)

    def _review_policy_value(self, run_metadata: dict[str, Any]) -> str:
        return run_metadata.get("review_policy", "suspicious_only")


def _config_digest(config: AppConfig) -> str:
    return hashlib.sha256(json.dumps(config.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()


def _config_for_matrix_case(config: AppConfig, case_id: str) -> AppConfig:
    case = next(item for item in config.evaluation.matrix_cases if item.id == case_id)
    updated_models: list[ModelBackendConfig] = []
    for model in config.models:
        if model.name in {case.red_backend, case.blue_backend} and case.system_prompt_variant_override is not None:
            updated_models.append(replace(model, system_prompt_variant=case.system_prompt_variant_override))
        else:
            updated_models.append(model)
    return replace(
        config,
        models=tuple(updated_models),
        model_routing=ModelRoutingConfig(
            red_backend=case.red_backend,
            blue_backend=case.blue_backend,
            red_fallback_backend=config.model_routing.red_fallback_backend,
            blue_fallback_backend=config.model_routing.blue_fallback_backend,
        ),
    )
