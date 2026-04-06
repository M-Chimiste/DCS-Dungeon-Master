"""CLI entrypoint for the DCS Dungeon Master backend."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Sequence

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.audit import MilestoneAuditService
from dcs_dungeon_master.app import DEFAULT_CONFIG_PATH, bootstrap_application
from dcs_dungeon_master.core.config import ModelRoutingConfig, load_config
from dcs_dungeon_master.core.enums import Coalition, FairnessReviewStatus
from dcs_dungeon_master.evaluation import EvaluationService, build_evaluation_metadata
from dcs_dungeon_master.execution import ExecutionEngine, LiveCommandLoopRunner
from dcs_dungeon_master.integration import IntegrationIngestCoordinator, build_integration_services
from dcs_dungeon_master.integration.grpc_codegen import generate_vendored_stubs
from dcs_dungeon_master.model_adapter import DryDecisionLoopRunner, build_model_registry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.operator_control import OperatorControlService
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.web_ui import WebUiService, serve_web_ui
from dcs_dungeon_master.world_state import KnowledgeDebugView, WorldStateRepository, WorldStateUpdater


def _run_mode(config) -> str:
    return "dry" if config.runtime.dry_run and config.dry_run.enabled else "live"


def _config_digest(config) -> str:
    return hashlib.sha256(json.dumps(config.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()


def _evaluation_metadata(config, **overrides):
    return build_evaluation_metadata(config, **overrides)


def _resolved_run_routing_from_config(config):
    from dcs_dungeon_master.core.models import ResolvedCoalitionRouting, ResolvedRunRouting

    return ResolvedRunRouting(
        red=ResolvedCoalitionRouting(
            coalition=Coalition.RED,
            primary_backend_name=config.model_routing.red_backend,
            fallback_backend_name=config.model_routing.red_fallback_backend,
            primary_catalog_id=config.model_routing.red_catalog_override or config.model_routing.default_catalog_id,
            fallback_catalog_id=config.model_routing.red_fallback_catalog_override or config.model_routing.default_fallback_catalog_id,
            primary_source="catalog" if (config.model_routing.red_catalog_override or config.model_routing.default_catalog_id) else "config",
            fallback_source="catalog"
            if (config.model_routing.red_fallback_catalog_override or config.model_routing.default_fallback_catalog_id)
            else None,
        ),
        blue=ResolvedCoalitionRouting(
            coalition=Coalition.BLUE,
            primary_backend_name=config.model_routing.blue_backend,
            fallback_backend_name=config.model_routing.blue_fallback_backend,
            primary_catalog_id=config.model_routing.blue_catalog_override or config.model_routing.default_catalog_id,
            fallback_catalog_id=config.model_routing.blue_fallback_catalog_override or config.model_routing.default_fallback_catalog_id,
            primary_source="catalog" if (config.model_routing.blue_catalog_override or config.model_routing.default_catalog_id) else "config",
            fallback_source="catalog"
            if (config.model_routing.blue_fallback_catalog_override or config.model_routing.default_fallback_catalog_id)
            else None,
        ),
        shared_primary_catalog_id=config.model_routing.default_catalog_id,
        shared_fallback_catalog_id=config.model_routing.default_fallback_catalog_id,
        same_primary_for_both=config.model_routing.red_backend == config.model_routing.blue_backend,
        same_fallback_for_both=config.model_routing.red_fallback_backend == config.model_routing.blue_fallback_backend,
    )


def _routing_config_for_run(config, run_state):
    if run_state.routing is None:
        return config.model_routing
    return ModelRoutingConfig(
        default_backend=run_state.routing.red.primary_backend_name if run_state.routing.same_primary_for_both else None,
        default_fallback_backend=run_state.routing.red.fallback_backend_name if run_state.routing.same_fallback_for_both else None,
        default_catalog_id=run_state.routing.shared_primary_catalog_id,
        default_fallback_catalog_id=run_state.routing.shared_fallback_catalog_id,
        red_backend=run_state.routing.red.primary_backend_name,
        blue_backend=run_state.routing.blue.primary_backend_name,
        red_fallback_backend=run_state.routing.red.fallback_backend_name,
        blue_fallback_backend=run_state.routing.blue.fallback_backend_name,
        red_catalog_override=None if run_state.routing.same_primary_for_both else run_state.routing.red.primary_catalog_id,
        blue_catalog_override=None if run_state.routing.same_primary_for_both else run_state.routing.blue.primary_catalog_id,
        red_fallback_catalog_override=None if run_state.routing.same_fallback_for_both else run_state.routing.red.fallback_catalog_id,
        blue_fallback_catalog_override=None if run_state.routing.same_fallback_for_both else run_state.routing.blue.fallback_catalog_id,
    )


def _build_model_registry_for_run(config, run_state):
    try:
        return build_model_registry(
            config,
            routing=_routing_config_for_run(config, run_state),
            run_scoped_backends=run_state.run_scoped_backends,
        )
    except TypeError:
        return build_model_registry(config)


def _print_payload(payload) -> None:
    print(json.dumps(_json_ready(payload), indent=2, sort_keys=True, default=str))


def _json_ready(value):
    if hasattr(value, "__dataclass_fields__"):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _handle_loop_failure(operator: OperatorControlService, run_id: str, exc: Exception) -> int:
    state = operator.get_status(run_id)
    if state.status.value in {"created", "running"}:
        try:
            operator.fail_run(run_id, reason=str(exc))
        except Exception:  # noqa: BLE001
            pass
    print(json.dumps({"run_id": run_id, "status": "failed", "error": str(exc)}, indent=2, sort_keys=True))
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dcs-dungeon-master")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_config_argument(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--config",
            type=Path,
            default=DEFAULT_CONFIG_PATH,
            help="Path to the TOML config file.",
        )

    run_dry = subparsers.add_parser("run-dry", help="Boot the Milestone 0 dry-run application.")
    add_config_argument(run_dry)

    print_config = subparsers.add_parser("print-config", help="Print the parsed config as JSON.")
    add_config_argument(print_config)

    check_scenario = subparsers.add_parser("check-scenario", help="Validate the configured scenario.")
    add_config_argument(check_scenario)

    init_state = subparsers.add_parser("init-state", help="Initialize SQLite state for the configured scenario.")
    add_config_argument(init_state)

    state_summary = subparsers.add_parser("state-summary", help="Print SQLite-backed current-state summary.")
    add_config_argument(state_summary)
    state_summary.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    check_integration = subparsers.add_parser("check-integration", help="Check Olympus and DCS-gRPC health.")
    add_config_argument(check_integration)

    ingest_step = subparsers.add_parser(
        "ingest-step",
        help="Run one normalized Olympus/gRPC ingest step into world state.",
    )
    add_config_argument(ingest_step)
    ingest_step.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    world_state_summary = subparsers.add_parser("world-state-summary", help="Print current world-state summary.")
    add_config_argument(world_state_summary)
    world_state_summary.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional run id. Defaults to the latest run.",
    )

    fusion_summary = subparsers.add_parser("fusion-summary", help="Preview coalition knowledge summary.")
    add_config_argument(fusion_summary)
    fusion_summary.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    fusion_summary.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)

    debug_contact_track = subparsers.add_parser(
        "debug-contact-track",
        help="Preview a coalition contact track and supporting truth/debug context.",
    )
    add_config_argument(debug_contact_track)
    debug_contact_track.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    debug_contact_track.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)
    debug_contact_track.add_argument("--track-id", type=str, required=True)

    compare_truth = subparsers.add_parser(
        "compare-truth-vs-knowledge",
        help="Compare internal truth against coalition-visible knowledge.",
    )
    add_config_argument(compare_truth)
    compare_truth.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    compare_truth.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)

    build_observation = subparsers.add_parser(
        "build-observation",
        help="Build and persist a canonical observation for one coalition.",
    )
    add_config_argument(build_observation)
    build_observation.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    build_observation.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)
    build_observation.add_argument("--decision-cycle", type=int, required=True)
    build_observation.add_argument("--seconds-since-last-cycle", type=int, default=30)

    build_observation_pair = subparsers.add_parser(
        "build-observation-pair",
        help="Build and persist RED and BLUE observations from one snapshot boundary.",
    )
    add_config_argument(build_observation_pair)
    build_observation_pair.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    build_observation_pair.add_argument("--decision-cycle", type=int, required=True)
    build_observation_pair.add_argument("--seconds-since-last-cycle", type=int, default=30)

    observation_summary = subparsers.add_parser(
        "observation-summary",
        help="Summarize the latest persisted observation for a coalition.",
    )
    add_config_argument(observation_summary)
    observation_summary.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    observation_summary.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)

    render_observation = subparsers.add_parser(
        "render-observation",
        help="Render the latest persisted observation as JSON, narrative, or a bundle.",
    )
    add_config_argument(render_observation)
    render_observation.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    render_observation.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)
    render_observation.add_argument("--format", choices=["json", "narrative", "bundle"], default="bundle")

    validate_actions = subparsers.add_parser(
        "validate-actions",
        help="Validate a batch of Phase 1 actions against the latest run state.",
    )
    add_config_argument(validate_actions)
    validate_actions.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    validate_actions.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)
    validate_actions.add_argument("--decision-cycle", type=int, required=True)
    action_input = validate_actions.add_mutually_exclusive_group(required=True)
    action_input.add_argument("--input", type=str, help="Inline JSON array or object with an 'actions' array.")
    action_input.add_argument("--input-file", type=Path, help="Path to a JSON file containing actions.")

    action_validation_summary = subparsers.add_parser(
        "action-validation-summary",
        help="Summarize the latest persisted action validation batch for a coalition.",
    )
    add_config_argument(action_validation_summary)
    action_validation_summary.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional run id. Defaults to the latest run.",
    )
    action_validation_summary.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)

    audit_milestones = subparsers.add_parser(
        "audit-milestones",
        help="Audit implemented milestone exit criteria against persisted run state and current capabilities.",
    )
    add_config_argument(audit_milestones)
    audit_milestones.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    check_model_backends = subparsers.add_parser(
        "check-model-backends",
        help="Check configured model backend connectivity without running a decision cycle.",
    )
    add_config_argument(check_model_backends)

    run_decision_cycle = subparsers.add_parser(
        "run-decision-cycle",
        help="Run one dry dual-commander decision cycle.",
    )
    add_config_argument(run_decision_cycle)
    run_decision_cycle.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    run_decision_cycle.add_argument("--decision-cycle", type=int, required=True)
    run_decision_cycle.add_argument("--seconds-since-last-cycle", type=int, default=30)

    run_decision_loop = subparsers.add_parser(
        "run-decision-loop",
        help="Run multiple dry dual-commander decision cycles.",
    )
    add_config_argument(run_decision_loop)
    run_decision_loop.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    run_decision_loop.add_argument("--start-decision-cycle", type=int, required=True)
    run_decision_loop.add_argument("--cycles", type=int, required=True)
    run_decision_loop.add_argument("--seconds-since-last-cycle", type=int, default=30)

    model_response_summary = subparsers.add_parser(
        "model-response-summary",
        help="Summarize the latest persisted model response for a coalition.",
    )
    add_config_argument(model_response_summary)
    model_response_summary.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    model_response_summary.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)

    run_live_cycle = subparsers.add_parser(
        "run-live-cycle",
        help="Run one live dual-commander cycle with execution after validation.",
    )
    add_config_argument(run_live_cycle)
    run_live_cycle.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    run_live_cycle.add_argument("--decision-cycle", type=int, required=True)
    run_live_cycle.add_argument("--seconds-since-last-cycle", type=int, default=30)

    run_live_loop = subparsers.add_parser(
        "run-live-loop",
        help="Run multiple live dual-commander cycles with execution enabled.",
    )
    add_config_argument(run_live_loop)
    run_live_loop.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    run_live_loop.add_argument("--start-decision-cycle", type=int, required=True)
    run_live_loop.add_argument("--cycles", type=int, required=True)
    run_live_loop.add_argument("--seconds-since-last-cycle", type=int, default=30)

    run_start = subparsers.add_parser("run-start", help="Transition a run from created to running.")
    add_config_argument(run_start)
    run_start.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    run_pause = subparsers.add_parser("run-pause", help="Pause a running run at a cycle boundary.")
    add_config_argument(run_pause)
    run_pause.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    run_resume = subparsers.add_parser("run-resume", help="Resume a paused run.")
    add_config_argument(run_resume)
    run_resume.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    run_stop = subparsers.add_parser("run-stop", help="Stop a run and record an operator reason.")
    add_config_argument(run_stop)
    run_stop.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    run_stop.add_argument("--reason", type=str, default="stopped_by_operator")

    run_status = subparsers.add_parser("run-status", help="Show run lifecycle status and metadata.")
    add_config_argument(run_status)
    run_status.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    run_summary = subparsers.add_parser("run-summary", help="Show an aggregated run summary.")
    add_config_argument(run_summary)
    run_summary.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    run_timeline = subparsers.add_parser("run-timeline", help="Show the persisted run timeline by decision cycle.")
    add_config_argument(run_timeline)
    run_timeline.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    run_inspect = subparsers.add_parser("run-inspect", help="Inspect the latest commander chain for one coalition.")
    add_config_argument(run_inspect)
    run_inspect.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    run_inspect.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)

    run_compare = subparsers.add_parser("run-compare", help="Compare two runs using persisted summary metadata.")
    add_config_argument(run_compare)
    run_compare.add_argument("--left-run-id", type=str, required=True)
    run_compare.add_argument("--right-run-id", type=str, required=True)

    replay_export = subparsers.add_parser("replay-export", help="Export a structured replay bundle for a run.")
    add_config_argument(replay_export)
    replay_export.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    replay_export.add_argument("--output", type=Path, required=True, help="Output directory for the replay bundle.")

    execution_summary = subparsers.add_parser(
        "execution-summary",
        help="Summarize the latest persisted execution batch for a coalition.",
    )
    add_config_argument(execution_summary)
    execution_summary.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    execution_summary.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)

    standing_orders_summary = subparsers.add_parser(
        "standing-orders-summary",
        help="Summarize active execution standing orders for a coalition.",
    )
    add_config_argument(standing_orders_summary)
    standing_orders_summary.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    standing_orders_summary.add_argument("--coalition", choices=[item.value for item in Coalition], required=True)

    execution_records = subparsers.add_parser(
        "execution-records",
        help="List persisted execution batches for a run.",
    )
    add_config_argument(execution_records)
    execution_records.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    eval_run = subparsers.add_parser("eval-run", help="Compute and persist evaluation artifacts for a run.")
    add_config_argument(eval_run)
    eval_run.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    eval_summary = subparsers.add_parser("eval-summary", help="Show the persisted evaluation summary for a run.")
    add_config_argument(eval_summary)
    eval_summary.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")

    eval_compare = subparsers.add_parser("eval-compare", help="Compare two evaluated runs.")
    add_config_argument(eval_compare)
    eval_compare.add_argument("--left-run-id", type=str, required=True)
    eval_compare.add_argument("--right-run-id", type=str, required=True)

    eval_review_queue = subparsers.add_parser("eval-review-queue", help="List suspicious findings and failed runs needing review.")
    add_config_argument(eval_review_queue)

    eval_review = subparsers.add_parser("eval-review", help="Review one fairness finding and recompute the run summary.")
    add_config_argument(eval_review)
    eval_review.add_argument("--finding-id", type=int, required=True)
    eval_review.add_argument("--status", choices=["cleared", "confirmed_leakage"], required=True)
    eval_review.add_argument("--note", type=str, default=None)

    eval_run_matrix = subparsers.add_parser("eval-run-matrix", help="Execute the configured evaluation matrix profile.")
    add_config_argument(eval_run_matrix)
    eval_run_matrix.add_argument("--profile", type=str, required=True)

    eval_matrix_report = subparsers.add_parser("eval-matrix-report", help="Show the persisted matrix report for a profile.")
    add_config_argument(eval_matrix_report)
    eval_matrix_report.add_argument("--profile", type=str, required=True)

    eval_closeout_status = subparsers.add_parser(
        "eval-closeout-status",
        help="Verify Milestone 9 completion evidence for a run/profile.",
    )
    add_config_argument(eval_closeout_status)
    eval_closeout_status.add_argument("--run-id", type=str, default=None, help="Optional run id. Defaults to the latest run.")
    eval_closeout_status.add_argument("--profile", type=str, default=None)

    validate_grpc_contracts = subparsers.add_parser(
        "validate-grpc-contracts",
        help="Generate vendored gRPC stubs into a temporary location to validate the proto contracts.",
    )

    serve_web = subparsers.add_parser(
        "serve-web-ui",
        help="Run the Milestone 10 JSON API server for Campaign Studio and Live Ops.",
    )
    add_config_argument(serve_web)
    serve_web.add_argument("--host", type=str, default="127.0.0.1")
    serve_web.add_argument("--port", type=int, default=8080)
    serve_web.add_argument("--static-dir", type=Path, default=Path("frontend/dist"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run-dry":
        application = bootstrap_application(args.config)
        print(json.dumps(application.summary(), indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "print-config":
        config = load_config(args.config)
        print(json.dumps(config.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "check-scenario":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        print(
            json.dumps(
                {
                    "scenario_id": scenario.id,
                    "scenario_version": scenario.version,
                    "name": scenario.name,
                    "theater": scenario.theater,
                    "sector_count": len(scenario.sector_ids),
                    "control_point_count": len(scenario.control_point_ids),
                    "zone_count": len(scenario.zone_ids),
                    "active_group_count": len(scenario.active_groups),
                    "reserve_group_count": len(scenario.reserve_groups),
                    "restriction_count": len(scenario.deployment_restrictions),
                    "coalitions": [coalition.coalition.value for coalition in scenario.coalitions],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "init-state":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        run_id = store.create_run_from_scenario(
            scenario,
            mode=_run_mode(config),
            config_digest=_config_digest(config),
            config_snapshot=config.to_dict(),
            red_backend_name=config.model_routing.red_backend,
            blue_backend_name=config.model_routing.blue_backend,
            routing=_resolved_run_routing_from_config(config),
            evaluation_metadata=_evaluation_metadata(config),
        )
        print(json.dumps(store.get_run_summary(run_id), indent=2, sort_keys=True))
        return 0

    if args.command == "state-summary":
        config = load_config(args.config)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        print(json.dumps(store.get_run_summary(args.run_id), indent=2, sort_keys=True))
        return 0

    if args.command == "check-integration":
        config = load_config(args.config)
        integrations = build_integration_services(config.dcs)
        payload = {
            "olympus": asdict(integrations.olympus.check_health()),
            "dcs_grpc": asdict(integrations.dcs_grpc.check_health()),
        }
        integrations.close()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "ingest-step":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        resolved_run_id = args.run_id or store.get_latest_run_id()
        integrations = build_integration_services(config.dcs)
        coordinator = IntegrationIngestCoordinator(integrations, WorldStateUpdater(WorldStateRepository(store), scenario))
        try:
            payload = coordinator.ingest_once(resolved_run_id)
        finally:
            integrations.close()
        _print_payload(payload)
        return 0

    if args.command == "world-state-summary":
        config = load_config(args.config)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        repository = WorldStateRepository(store)
        print(json.dumps(repository.summarize(args.run_id), indent=2, sort_keys=True))
        return 0

    if args.command == "fusion-summary":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        service = SensorFusionService(store, scenario, config.fog_of_war)
        payload = service.summarize(args.run_id or store.get_latest_run_id(), Coalition(args.coalition))
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "debug-contact-track":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        service = SensorFusionService(store, scenario, config.fog_of_war)
        payload = service.debug_contact_track(
            args.run_id or store.get_latest_run_id(),
            Coalition(args.coalition),
            args.track_id,
        )
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "compare-truth-vs-knowledge":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        debug_view = KnowledgeDebugView(store, SensorFusionService(store, scenario, config.fog_of_war))
        payload = debug_view.compare_truth_vs_knowledge(
            args.run_id or store.get_latest_run_id(),
            Coalition(args.coalition),
        )
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "build-observation":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        resolved_run_id = args.run_id or store.get_latest_run_id()
        builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        artifact = builder.build_observation(
            resolved_run_id,
            Coalition(args.coalition),
            args.decision_cycle,
            seconds_since_last_cycle=args.seconds_since_last_cycle,
        )
        print(json.dumps(builder.render_artifact(artifact, "bundle"), indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "build-observation-pair":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        resolved_run_id = args.run_id or store.get_latest_run_id()
        builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        red_artifact, blue_artifact = builder.build_observation_pair(
            resolved_run_id,
            args.decision_cycle,
            seconds_since_last_cycle=args.seconds_since_last_cycle,
        )
        payload = {
            "red": builder.render_artifact(red_artifact, "bundle"),
            "blue": builder.render_artifact(blue_artifact, "bundle"),
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "observation-summary":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        payload = builder.summarize_latest(args.run_id or store.get_latest_run_id(), Coalition(args.coalition))
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "render-observation":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        payload = builder.render_latest(args.run_id or store.get_latest_run_id(), Coalition(args.coalition), args.format)
        if isinstance(payload, str):
            print(payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "validate-actions":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        validator = ActionValidator(store, scenario)
        resolved_run_id = args.run_id or store.get_latest_run_id()
        raw_payload = args.input_file.read_text(encoding="utf-8") if args.input_file else args.input
        payload = json.loads(raw_payload)
        batch = validator.validate_payload(
            resolved_run_id,
            Coalition(args.coalition),
            args.decision_cycle,
            payload,
            persist=True,
        )
        print(json.dumps(asdict(batch), indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "action-validation-summary":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        validator = ActionValidator(store, scenario)
        payload = validator.summarize_latest(args.run_id or store.get_latest_run_id(), Coalition(args.coalition))
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "audit-milestones":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        sensor_fusion = SensorFusionService(store, scenario, config.fog_of_war)
        observation_builder = ObservationBuilder(store, scenario, sensor_fusion, config.multimodal)
        validator = ActionValidator(store, scenario)
        integrations = build_integration_services(config.dcs)
        registry = build_model_registry(config)
        try:
            audit_service = MilestoneAuditService(
                config=config,
                scenario=scenario,
                store=store,
                sensor_fusion=sensor_fusion,
                observation_builder=observation_builder,
                action_validator=validator,
                model_registry=registry,
                integrations=integrations,
            )
            payload = audit_service.audit(args.run_id)
        finally:
            registry.close()
            integrations.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "check-model-backends":
        config = load_config(args.config)
        registry = build_model_registry(config)
        try:
            payload = {
                "health": [asdict(item) for item in registry.check_health()],
                "capabilities": [asdict(item) for item in registry.describe_backends()],
                "routing": {
                    "red": {
                        "primary": config.model_routing.red_backend,
                        "fallback": config.model_routing.red_fallback_backend,
                        "catalog_id": config.model_routing.red_catalog_override or config.model_routing.default_catalog_id,
                    },
                    "blue": {
                        "primary": config.model_routing.blue_backend,
                        "fallback": config.model_routing.blue_fallback_backend,
                        "catalog_id": config.model_routing.blue_catalog_override or config.model_routing.default_catalog_id,
                    },
                },
                "catalog": [asdict(item) for item in registry.list_catalog()],
            }
        finally:
            registry.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command in {"eval-run", "eval-summary", "eval-compare", "eval-review-queue", "eval-review", "eval-run-matrix", "eval-matrix-report", "eval-closeout-status"}:
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        evaluation = EvaluationService(store, scenario)
        if args.command == "eval-run":
            payload = evaluation.evaluate_run(args.run_id or store.get_latest_run_id())
        elif args.command == "eval-summary":
            payload = evaluation.summarize_run(args.run_id or store.get_latest_run_id())
        elif args.command == "eval-compare":
            payload = evaluation.compare_runs(args.left_run_id, args.right_run_id)
        elif args.command == "eval-review-queue":
            payload = evaluation.review_queue()
        elif args.command == "eval-review":
            payload = evaluation.review_finding(
                args.finding_id,
                status=FairnessReviewStatus(args.status),
                note=args.note,
            )
        elif args.command == "eval-run-matrix":
            if args.profile != config.evaluation.profile_name:
                parser.error(
                    f"Configured evaluation profile is '{config.evaluation.profile_name}', got '{args.profile}'."
                )
            payload = evaluation.run_matrix(config)
        elif args.command == "eval-closeout-status":
            payload = evaluation.milestone9_completion_status(args.run_id or store.get_latest_run_id(), args.profile)
        else:
            payload = evaluation.get_matrix_report(args.profile)
        _print_payload(payload)
        return 0

    if args.command in {"run-start", "run-pause", "run-resume", "run-stop", "run-status", "run-summary", "run-timeline", "run-inspect", "run-compare", "replay-export"}:
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        world_repository = WorldStateRepository(store)
        sensor_fusion = SensorFusionService(store, scenario, config.fog_of_war)
        operator = OperatorControlService(store, sensor_fusion=sensor_fusion, world_repository=world_repository)
        resolved_run_id = getattr(args, "run_id", None) or store.get_latest_run_id()

        if args.command == "run-start":
            payload = operator.start_run(resolved_run_id)
        elif args.command == "run-pause":
            payload = operator.pause_run(resolved_run_id)
        elif args.command == "run-resume":
            payload = operator.resume_run(resolved_run_id)
        elif args.command == "run-stop":
            payload = operator.stop_run(resolved_run_id, reason=args.reason)
        elif args.command == "run-status":
            payload = operator.get_status(resolved_run_id)
        elif args.command == "run-summary":
            payload = operator.summarize_run(resolved_run_id)
        elif args.command == "run-timeline":
            payload = operator.list_timeline(resolved_run_id)
        elif args.command == "run-inspect":
            payload = operator.inspect_coalition(resolved_run_id, Coalition(args.coalition))
        elif args.command == "run-compare":
            payload = operator.compare_runs(args.left_run_id, args.right_run_id)
        else:
            payload = operator.export_replay_bundle(resolved_run_id, args.output)

        _print_payload(payload)
        return 0

    if args.command == "run-decision-cycle":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        resolved_run_id = args.run_id or store.get_latest_run_id()
        run_state = store.get_run_control_state(resolved_run_id)
        operator = OperatorControlService(store)
        world_repository = WorldStateRepository(store)
        world_updater = WorldStateUpdater(world_repository, scenario)
        observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        validator = ActionValidator(store, scenario)
        integrations = build_integration_services(config.dcs)
        registry = _build_model_registry_for_run(config, run_state)
        try:
            operator.ensure_cycle_allowed(resolved_run_id)
            runner = DryDecisionLoopRunner(
                store,
                observation_builder,
                validator,
                registry,
                IntegrationIngestCoordinator(integrations, world_updater),
            )
            cycle = runner.run_decision_cycle(
                resolved_run_id,
                args.decision_cycle,
                seconds_since_last_cycle=args.seconds_since_last_cycle,
            )
        except Exception as exc:  # noqa: BLE001
            return _handle_loop_failure(operator, resolved_run_id, exc)
        finally:
            registry.close()
            integrations.close()
        _print_payload(cycle)
        return 0

    if args.command == "run-decision-loop":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        resolved_run_id = args.run_id or store.get_latest_run_id()
        run_state = store.get_run_control_state(resolved_run_id)
        operator = OperatorControlService(store)
        world_repository = WorldStateRepository(store)
        world_updater = WorldStateUpdater(world_repository, scenario)
        observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        validator = ActionValidator(store, scenario)
        integrations = build_integration_services(config.dcs)
        registry = _build_model_registry_for_run(config, run_state)
        try:
            operator.ensure_cycle_allowed(resolved_run_id)
            runner = DryDecisionLoopRunner(
                store,
                observation_builder,
                validator,
                registry,
                IntegrationIngestCoordinator(integrations, world_updater),
            )
            payload = runner.run_decision_loop(
                resolved_run_id,
                start_decision_cycle=args.start_decision_cycle,
                cycles=args.cycles,
                seconds_since_last_cycle=args.seconds_since_last_cycle,
            )
        except Exception as exc:  # noqa: BLE001
            return _handle_loop_failure(operator, resolved_run_id, exc)
        finally:
            registry.close()
            integrations.close()
        _print_payload(payload)
        return 0

    if args.command == "model-response-summary":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        run_state = store.get_run_control_state(args.run_id or store.get_latest_run_id())
        observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        validator = ActionValidator(store, scenario)
        registry = _build_model_registry_for_run(config, run_state)
        try:
            runner = DryDecisionLoopRunner(store, observation_builder, validator, registry)
            payload = runner.summarize_latest_response(args.run_id or store.get_latest_run_id(), Coalition(args.coalition))
        finally:
            registry.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "run-live-cycle":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        resolved_run_id = args.run_id or store.get_latest_run_id()
        run_state = store.get_run_control_state(resolved_run_id)
        operator = OperatorControlService(store)
        world_repository = WorldStateRepository(store)
        world_updater = WorldStateUpdater(world_repository, scenario)
        observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        validator = ActionValidator(store, scenario)
        integrations = build_integration_services(config.dcs)
        registry = _build_model_registry_for_run(config, run_state)
        try:
            operator.ensure_cycle_allowed(resolved_run_id)
            execution_engine = ExecutionEngine(store, scenario, integrations.olympus)
            runner = LiveCommandLoopRunner(
                store,
                observation_builder,
                validator,
                registry,
                execution_engine,
                IntegrationIngestCoordinator(integrations, world_updater),
            )
            cycle = runner.run_live_cycle(
                resolved_run_id,
                args.decision_cycle,
                seconds_since_last_cycle=args.seconds_since_last_cycle,
            )
        except Exception as exc:  # noqa: BLE001
            return _handle_loop_failure(operator, resolved_run_id, exc)
        finally:
            registry.close()
            integrations.close()
        _print_payload(cycle)
        return 0

    if args.command == "run-live-loop":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        resolved_run_id = args.run_id or store.get_latest_run_id()
        run_state = store.get_run_control_state(resolved_run_id)
        operator = OperatorControlService(store)
        world_repository = WorldStateRepository(store)
        world_updater = WorldStateUpdater(world_repository, scenario)
        observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
        validator = ActionValidator(store, scenario)
        integrations = build_integration_services(config.dcs)
        registry = _build_model_registry_for_run(config, run_state)
        try:
            operator.ensure_cycle_allowed(resolved_run_id)
            execution_engine = ExecutionEngine(store, scenario, integrations.olympus)
            runner = LiveCommandLoopRunner(
                store,
                observation_builder,
                validator,
                registry,
                execution_engine,
                IntegrationIngestCoordinator(integrations, world_updater),
            )
            payload = runner.run_live_loop(
                resolved_run_id,
                start_decision_cycle=args.start_decision_cycle,
                cycles=args.cycles,
                seconds_since_last_cycle=args.seconds_since_last_cycle,
            )
        except Exception as exc:  # noqa: BLE001
            return _handle_loop_failure(operator, resolved_run_id, exc)
        finally:
            registry.close()
            integrations.close()
        _print_payload(payload)
        return 0

    if args.command == "execution-summary":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        integrations = build_integration_services(config.dcs)
        try:
            execution_engine = ExecutionEngine(store, scenario, integrations.olympus)
            payload = execution_engine.summarize_latest(args.run_id or store.get_latest_run_id(), Coalition(args.coalition))
        finally:
            integrations.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "standing-orders-summary":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        integrations = build_integration_services(config.dcs)
        try:
            execution_engine = ExecutionEngine(store, scenario, integrations.olympus)
            payload = execution_engine.summarize_standing_orders(args.run_id or store.get_latest_run_id(), Coalition(args.coalition))
        finally:
            integrations.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "execution-records":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        integrations = build_integration_services(config.dcs)
        try:
            execution_engine = ExecutionEngine(store, scenario, integrations.olympus)
            payload = [asdict(item) for item in execution_engine.list_execution_records(args.run_id or store.get_latest_run_id())]
        finally:
            integrations.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "validate-grpc-contracts":
        with tempfile.TemporaryDirectory(prefix="dcs-grpc-contracts-") as temp_dir:
            paths = [str(path) for path in generate_vendored_stubs(temp_dir)]
            print(json.dumps({"generated_files": paths}, indent=2, sort_keys=True))
        return 0

    if args.command == "serve-web-ui":
        service = WebUiService.from_config_path(args.config)
        serve_web_ui(service, host=args.host, port=args.port, static_dir=args.static_dir)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
