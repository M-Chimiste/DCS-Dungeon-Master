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
    assert config.scenario.id == "phase1_baseline_persian_gulf"
    assert config.scenario.registry_path == "scenarios/index.toml"
    assert config.persistence.db_path.endswith("state.sqlite3")
    assert config.dcs.olympus.retry_attempts == 1
    assert config.dcs.grpc.retry_attempts == 1
    assert config.fog_of_war.inference_mode.value == "conservative"


def test_load_config_failure_for_missing_required_section(tmp_path: Path) -> None:
    broken_config = tmp_path / "broken.toml"
    broken_config.write_text("[runtime]\napp_name='test'\nenvironment='dev'\ndry_run=true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Config section 'logging'"):
        load_config(broken_config)


def test_load_config_supports_remote_hosted_dcs(tmp_path: Path) -> None:
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        """
[runtime]
app_name = "dcs_dungeon_master"
environment = "test"
dry_run = true

[logging]
level = "INFO"
format = "text"

[dcs]
remote_hosted = true

[dcs.olympus]
base_url = "https://dcs-remote.internal:4512"
timeout_sec = 6.0
retry_attempts = 2
username = "operator"
token_env_var = "REMOTE_OLYMPUS_TOKEN"

[dcs.grpc]
host = "dcs-remote.internal"
port = 50051
timeout_sec = 6.0
secure = true
retry_attempts = 2
server_host_override = "dcs.internal"

[scenario]
id = "phase1_baseline_persian_gulf"
registry_path = "scenarios/index.toml"

[persistence]
db_path = "/tmp/dcs.sqlite3"
enable_wal = true

[dry_run]
enabled = true
summary_output = "text"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.dcs.remote_hosted is True
    assert config.dcs.olympus.base_url == "https://dcs-remote.internal:4512"
    assert config.dcs.grpc.host == "dcs-remote.internal"
    assert config.dcs.grpc.secure is True
    assert config.dcs.grpc.server_host_override == "dcs.internal"
    assert config.fog_of_war.freshness_window_sec == 300
