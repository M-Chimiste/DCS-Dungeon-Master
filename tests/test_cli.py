import json

from dcs_dungeon_master.__main__ import main


def test_run_dry_cli_smoke(capsys) -> None:
    exit_code = main(["run-dry"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["dry_run"] is True
    assert payload["scenario_id"] == "phase1_baseline_caucasus_frontier"


def test_print_config_cli_smoke(capsys) -> None:
    exit_code = main(["print-config"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["scenario"]["id"] == "phase1_baseline_caucasus_frontier"


def test_check_scenario_cli_smoke(capsys) -> None:
    exit_code = main(["check-scenario"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["scenario_id"] == "phase1_baseline_caucasus_frontier"
    assert payload["sector_count"] == 7
