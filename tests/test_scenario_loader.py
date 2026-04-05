from pathlib import Path

import pytest

from dcs_dungeon_master.core.exceptions import ConfigError
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition, list_scenarios


def test_scenario_loader_success_for_persian_gulf_baseline() -> None:
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")

    assert scenario.theater == "Persian Gulf"
    assert scenario.version == "1"
    assert len(scenario.sectors) == 7
    assert len(scenario.control_points) == 6
    assert len(scenario.active_groups) == 10
    assert len(scenario.reserve_groups) == 6


def test_scenario_registry_lists_persian_gulf() -> None:
    scenarios = list_scenarios("scenarios/index.toml")
    assert scenarios[0].id == "phase1_baseline_persian_gulf"


def test_scenario_loader_failure_for_missing_sections(tmp_path: Path) -> None:
    broken = tmp_path / "broken_scenario.toml"
    broken.write_text(
        """
id = "broken"
version = "1"
name = "Broken"
theater = "Persian Gulf"
summary = "Missing sectors"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="'sectors' must be a non-empty array"):
        get_scenario_definition("broken", _write_registry(tmp_path, broken))


def _write_registry(tmp_path: Path, scenario_path: Path) -> Path:
    index_path = tmp_path / "index.toml"
    index_path.write_text(
        f"""
[[scenarios]]
id = "broken"
name = "Broken"
theater = "Persian Gulf"
path = "{scenario_path.name}"
summary = "Broken scenario"
""".strip(),
        encoding="utf-8",
    )
    return index_path
