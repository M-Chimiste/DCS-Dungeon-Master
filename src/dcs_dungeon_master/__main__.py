"""CLI entrypoint for the DCS Dungeon Master backend."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from typing import Sequence

from dcs_dungeon_master.app import DEFAULT_CONFIG_PATH, bootstrap_application
from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import Coalition
from dcs_dungeon_master.integration import build_integration_services
from dcs_dungeon_master.integration.grpc_codegen import generate_vendored_stubs
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService
from dcs_dungeon_master.world_state import KnowledgeDebugView, WorldStateRepository


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

    validate_grpc_contracts = subparsers.add_parser(
        "validate-grpc-contracts",
        help="Generate vendored gRPC stubs into a temporary location to validate the proto contracts.",
    )

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
        run_id = store.create_run_from_scenario(scenario)
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
        print(json.dumps(payload, indent=2, sort_keys=True))
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
        builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war))
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
        builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war))
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
        builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war))
        payload = builder.summarize_latest(args.run_id or store.get_latest_run_id(), Coalition(args.coalition))
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "render-observation":
        config = load_config(args.config)
        scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
        store = SQLiteStateStore(config.persistence.db_path, enable_wal=config.persistence.enable_wal)
        builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war))
        payload = builder.render_latest(args.run_id or store.get_latest_run_id(), Coalition(args.coalition), args.format)
        if isinstance(payload, str):
            print(payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "validate-grpc-contracts":
        with tempfile.TemporaryDirectory(prefix="dcs-grpc-contracts-") as temp_dir:
            paths = [str(path) for path in generate_vendored_stubs(temp_dir)]
            print(json.dumps({"generated_files": paths}, indent=2, sort_keys=True))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
