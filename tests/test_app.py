from pathlib import Path

from dcs_dungeon_master.app import bootstrap_application


def _write_temp_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state.sqlite3"
    config_path.write_text(
        f"""
[runtime]
app_name = "dcs_dungeon_master"
environment = "test"
dry_run = true

[logging]
level = "INFO"
format = "text"

[dcs]
remote_hosted = false

[dcs.olympus]
base_url = "http://127.0.0.1:4512"
timeout_sec = 5.0

[dcs.grpc]
host = "127.0.0.1"
port = 50051
timeout_sec = 5.0

[[models]]
name = "local_default"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true

[model_routing]
red_backend = "local_default"
blue_backend = "local_default"

[scenario]
id = "phase1_baseline_persian_gulf"
registry_path = "scenarios/index.toml"

[persistence]
db_path = "{db_path}"
enable_wal = true

[dry_run]
enabled = true
summary_output = "text"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def test_bootstrap_application_without_external_dependencies(tmp_path: Path) -> None:
    application = bootstrap_application(_write_temp_config(tmp_path))

    summary = application.summary()
    assert summary["dry_run"] is True
    assert summary["service_statuses"]["olympus_gateway"] == "client-ready"
    assert summary["service_statuses"]["dcs_grpc_gateway"] == "client-ready"
    assert summary["service_statuses"]["world_state"] == "world-state-ready"
    assert summary["service_statuses"]["sensor_fusion"] == "sensor-fusion-ready"
    assert summary["service_statuses"]["observation_builder"] == "observation-ready"
    assert summary["service_statuses"]["action_validator"] == "action-validator-ready"
    assert summary["service_statuses"]["model_adapters"] == "model-adapters-ready"
    assert summary["service_statuses"]["dry_decision_loop"] == "dry-loop-ready"
    assert summary["service_statuses"]["execution_engine"] == "execution-ready"
    assert summary["service_statuses"]["live_command_loop"] == "live-loop-ready"
    assert summary["service_statuses"]["operator_control"] == "operator-control-ready"
    assert summary["service_statuses"]["evaluation"] == "evaluation-ready"
    assert summary["integration_endpoints"]["olympus"] == "http://127.0.0.1:4512"
    assert summary["integration_endpoints"]["dcs_grpc"] == "127.0.0.1:50051"
    assert summary["integration_health"] is None
    assert summary["state_summary"]["event_count"] > 0


def test_bootstrap_application_supports_remote_dcs_topology(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state.sqlite3"
    config_path.write_text(
        f"""
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
base_url = "http://192.168.10.50:4512"
timeout_sec = 5.0
retry_attempts = 2

[dcs.grpc]
host = "192.168.10.50"
port = 50051
timeout_sec = 5.0
secure = false
retry_attempts = 2

[[models]]
name = "local_default"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true

[model_routing]
red_backend = "local_default"
blue_backend = "local_default"

[scenario]
id = "phase1_baseline_persian_gulf"
registry_path = "scenarios/index.toml"

[persistence]
db_path = "{db_path}"
enable_wal = true

[dry_run]
enabled = true
summary_output = "text"
""".strip(),
        encoding="utf-8",
    )

    application = bootstrap_application(config_path)

    assert application.config.dcs.remote_hosted is True
    assert application.summary()["integration_endpoints"]["olympus"] == "http://192.168.10.50:4512"
    assert application.summary()["integration_endpoints"]["dcs_grpc"] == "192.168.10.50:50051"
