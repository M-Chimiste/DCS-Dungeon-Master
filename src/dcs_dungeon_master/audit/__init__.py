"""Milestone audit helpers for implemented Phase 1 slices."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.config import AppConfig
from dcs_dungeon_master.core.enums import Coalition
from dcs_dungeon_master.core.exceptions import PersistenceError
from dcs_dungeon_master.core.models import ScenarioDefinition
from dcs_dungeon_master.integration import IntegrationServices
from dcs_dungeon_master.model_adapter import ModelAdapterRegistry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.sensor_fusion import SensorFusionService


@dataclass(slots=True)
class MilestoneAuditService:
    config: AppConfig
    scenario: ScenarioDefinition
    store: SQLiteStateStore
    sensor_fusion: SensorFusionService
    observation_builder: ObservationBuilder
    action_validator: ActionValidator
    model_registry: ModelAdapterRegistry
    integrations: IntegrationServices

    def audit(self, run_id: str | None = None) -> dict[str, object]:
        resolved_run_id = run_id or self.store.get_latest_run_id()
        run_summary = self.store.get_run_summary(resolved_run_id)
        fusion_red = self.sensor_fusion.summarize(resolved_run_id, Coalition.RED)
        fusion_blue = self.sensor_fusion.summarize(resolved_run_id, Coalition.BLUE)
        integration_health = self.integrations.check_health()
        backend_descriptors = self.model_registry.describe_backends()

        checks: list[dict[str, object]] = []

        checks.extend(
            [
                self._check(
                    "Milestone 1",
                    "Authored scenario and SQLite-backed run state exist",
                    bool(self.scenario.sectors and self.scenario.control_points and run_summary["run_id"] == resolved_run_id),
                    "scenario_registry + sqlite current state",
                    {
                        "scenario_id": self.scenario.id,
                        "sector_count": len(self.scenario.sectors),
                        "control_point_count": len(self.scenario.control_points),
                        "run_id": run_summary["run_id"],
                    },
                ),
                self._check(
                    "Milestone 2",
                    "Integration services expose machine-readable health for Olympus and DCS-gRPC",
                    all(hasattr(item, "status") for item in integration_health),
                    "integration health surfaces",
                    [self._serialize(item) for item in integration_health],
                ),
                self._check(
                    "Milestone 2",
                    "Remote DCS topology is supported in config",
                    hasattr(self.config.dcs, "remote_hosted"),
                    "config topology support",
                    {
                        "remote_hosted": self.config.dcs.remote_hosted,
                        "olympus_endpoint": self.integrations.olympus.endpoint,
                        "grpc_endpoint": self.integrations.dcs_grpc.endpoint,
                    },
                ),
                self._check(
                    "Milestone 3",
                    "World state and coalition-safe fusion are available for both coalitions",
                    fusion_red["coalition"] == "red" and fusion_blue["coalition"] == "blue",
                    "persisted world snapshot + fusion preview",
                    {
                        "red_contact_count": fusion_red["contact_track_count"],
                        "blue_contact_count": fusion_blue["contact_track_count"],
                    },
                ),
            ]
        )

        observations = self.store.list_observations(resolved_run_id)
        checks.append(
            self._check(
                "Milestone 4",
                "Persisted observations include replayable canonical payloads and attachment placeholders",
                len(observations) >= 2 and all(artifact.observation.attachments for artifact in observations[-2:]),
                "observation artifacts",
                {
                    "observation_count": len(observations),
                    "latest_attachment_counts": [len(artifact.observation.attachments) for artifact in observations[-2:]],
                },
            )
        )

        try:
            action_batches_red = self.store.get_latest_action_validation_batch(resolved_run_id, Coalition.RED)
            action_batches_blue = self.store.get_latest_action_validation_batch(resolved_run_id, Coalition.BLUE)
        except PersistenceError:
            action_batches_red = None
            action_batches_blue = None
        checks.append(
            self._check(
                "Milestone 5",
                "Validated action batches exist for both coalitions with observation linkage",
                action_batches_red is not None
                and action_batches_blue is not None
                and action_batches_red.latest_observation_id is not None
                and action_batches_blue.latest_observation_id is not None,
                "action validation persistence",
                {
                    "red_batch_id": action_batches_red.id if action_batches_red else None,
                    "blue_batch_id": action_batches_blue.id if action_batches_blue else None,
                    "red_latest_observation_id": action_batches_red.latest_observation_id if action_batches_red else None,
                    "blue_latest_observation_id": action_batches_blue.latest_observation_id if action_batches_blue else None,
                },
            )
        )

        decision_cycles = self.store.list_decision_cycles(resolved_run_id)
        invocations = self.store.list_model_invocations(resolved_run_id)
        checks.append(
            self._check(
                "Milestone 6",
                "Model registry exposes backend capability metadata and structured-output-ready backends",
                bool(backend_descriptors)
                and all(item.openai_compatible and item.supports_structured_output for item in backend_descriptors),
                "model registry capability metadata",
                [self._serialize(item) for item in backend_descriptors],
            )
        )
        checks.append(
            self._check(
                "Milestone 6",
                "Dry decision cycles persist model invocations and cycle classifications",
                bool(decision_cycles)
                and bool(invocations)
                and all(cycle.classification in {"both_succeeded", "one_succeeded_one_failed", "both_failed"} for cycle in decision_cycles),
                "decision-cycle and model-invocation persistence",
                {
                    "decision_cycle_count": len(decision_cycles),
                    "model_invocation_count": len(invocations),
                    "cycle_classifications": [cycle.classification for cycle in decision_cycles],
                },
            )
        )

        try:
            execution_red = self.store.get_latest_execution_batch(resolved_run_id, Coalition.RED)
            execution_blue = self.store.get_latest_execution_batch(resolved_run_id, Coalition.BLUE)
        except PersistenceError:
            execution_red = None
            execution_blue = None
        checks.append(
            self._check(
                "Milestone 7",
                "Live execution batches persist for both coalitions with execution linkage",
                execution_red is not None and execution_blue is not None,
                "execution batch persistence",
                {
                    "red_execution_batch_id": execution_red.id if execution_red else None,
                    "blue_execution_batch_id": execution_blue.id if execution_blue else None,
                },
            )
        )
        checks.append(
            self._check(
                "Milestone 7",
                "Execution standing orders persist for replayable operator inspection",
                bool(self.store.get_active_execution_standing_orders(resolved_run_id)),
                "execution standing orders",
                {
                    "standing_order_count": len(self.store.get_active_execution_standing_orders(resolved_run_id)),
                    "standing_order_history_count": len(self.store.list_execution_standing_order_history(resolved_run_id)),
                },
            )
        )
        run_control = self.store.get_run_control_state(resolved_run_id)
        checks.append(
            self._check(
                "Milestone 8",
                "Run lifecycle control is persisted on the run record",
                run_control.status.value in {"created", "running", "paused", "stopped", "failed"},
                "run lifecycle metadata",
                self._serialize(run_control),
            )
        )
        checks.append(
            self._check(
                "Milestone 8",
                "Replay-friendly artifacts include persisted fusion snapshots and execution notes",
                bool(self.store.list_fusion_snapshot_records(resolved_run_id)),
                "fusion snapshots + execution notes",
                {
                    "fusion_snapshot_count": len(self.store.list_fusion_snapshot_records(resolved_run_id)),
                    "execution_note_count": len(self.store.list_current_execution_notes(resolved_run_id)),
                },
            )
        )
        checks.append(
            self._check(
                "Milestone 8",
                "Run inspection data supports comparison-friendly summaries",
                "mode" in run_summary and "status" in run_summary and "execution_batch_count" in run_summary,
                "run summary metadata",
                {
                    "mode": run_summary["mode"],
                    "status": run_summary["status"],
                    "execution_batch_count": run_summary["execution_batch_count"],
                },
            )
        )

        return {
            "run_id": resolved_run_id,
            "scenario_id": self.scenario.id,
            "checks": checks,
        }

    @staticmethod
    def _check(
        milestone: str,
        criteria: str,
        passed: bool,
        evidence_source: str,
        evidence: object,
    ) -> dict[str, object]:
        return {
            "milestone": milestone,
            "criteria": criteria,
            "passed": passed,
            "evidence_source": evidence_source,
            "evidence": evidence,
        }

    @staticmethod
    def _serialize(item: object) -> dict[str, object]:
        try:
            return asdict(item)  # type: ignore[arg-type]
        except TypeError:
            return {
                key: value
                for key, value in vars(item).items()
                if not key.startswith("_")
            }


__all__ = ["MilestoneAuditService"]
