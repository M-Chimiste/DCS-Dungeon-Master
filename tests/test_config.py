from pathlib import Path

import pytest

from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import LoggingFormat, ModelHostingMode
from dcs_dungeon_master.core.exceptions import ConfigError


def test_load_config_success() -> None:
    config = load_config(Path("config/milestone0.toml"))

    assert config.runtime.app_name == "dcs_dungeon_master"
    assert config.logging.format is LoggingFormat.TEXT
    assert config.models[0].hosting_mode is ModelHostingMode.LOCAL
    assert config.scenario.id == "phase1_baseline_caucasus_frontier"


def test_load_config_failure_for_missing_required_section(tmp_path: Path) -> None:
    broken_config = tmp_path / "broken.toml"
    broken_config.write_text("[runtime]\napp_name='test'\nenvironment='dev'\ndry_run=true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Config section 'logging'"):
        load_config(broken_config)
