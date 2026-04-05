"""CLI entrypoint for the DCS Dungeon Master backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from dcs_dungeon_master.app import DEFAULT_CONFIG_PATH, bootstrap_application
from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition


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
        scenario = get_scenario_definition(config.scenario.id)
        print(
            json.dumps(
                {
                    "scenario_id": scenario.id,
                    "name": scenario.name,
                    "theater": scenario.theater,
                    "sector_count": len(scenario.sector_ids),
                    "control_point_count": len(scenario.control_point_ids),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
