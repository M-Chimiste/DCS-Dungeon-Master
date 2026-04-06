from __future__ import annotations

from pathlib import Path

from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.integration.types import GrpcMissionMetadataSnapshot, IntegrationHealthStatus
from dcs_dungeon_master.setup_wizard import (
    AUTOEXEC_DOSTRING_LINE,
    AUTOEXEC_UNSAFE_API_LINE,
    SetupWizardService,
)


def _write_temp_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
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
db_path = "{tmp_path / "state.sqlite3"}"
enable_wal = true

[dry_run]
enabled = true
summary_output = "text"
""".strip(),
        encoding="utf-8",
    )
    return config_path


class _FakeOlympus:
    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    def check_health(self) -> IntegrationHealthStatus:
        return IntegrationHealthStatus(
            service="olympus",
            endpoint=self._endpoint,
            healthy=True,
            detail="ok",
            status="healthy",
            attempt_count=1,
        )

    def close(self) -> None:
        return None


class _FakeGrpc:
    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    def check_health(self) -> IntegrationHealthStatus:
        return IntegrationHealthStatus(
            service="dcs_grpc",
            endpoint=self._endpoint,
            healthy=True,
            detail="channel_ready",
            status="healthy",
            attempt_count=1,
        )

    def get_mission_metadata(self) -> GrpcMissionMetadataSnapshot:
        return GrpcMissionMetadataSnapshot(
            theater="Persian Gulf",
            mission_name="Wizard Test",
            mission_time="12:00:00Z",
        )

    def close(self) -> None:
        return None


def test_setup_wizard_supports_manual_saved_games_and_generated_config(tmp_path: Path) -> None:
    config_path = _write_temp_config(tmp_path)
    saved_games = tmp_path / "Saved Games" / "DCS.openbeta"
    autoexec = saved_games / "Config" / "autoexec.cfg"
    autoexec.parent.mkdir(parents=True)
    autoexec.write_text(
        f"{AUTOEXEC_UNSAFE_API_LINE}\n{AUTOEXEC_DOSTRING_LINE}\n",
        encoding="utf-8",
    )

    service = SetupWizardService(
        config_path=config_path,
        olympus_factory=lambda config: _FakeOlympus(config.base_url),
        grpc_factory=lambda config: _FakeGrpc(f"{config.host}:{config.port}"),
    )
    status = service.probe(saved_games_path=str(saved_games))
    output_path = tmp_path / "local.toml"
    write_result = service.write_local_config(output_path=output_path, saved_games_path=str(saved_games))
    loaded = load_config(output_path)

    assert status.overall_status == "ready"
    assert status.saved_games_path.detected_value == str(saved_games)
    assert status.autoexec_status.healthy is True
    assert write_result.written is True
    assert loaded.setup.saved_games_path == str(saved_games)


def test_setup_wizard_detects_saved_games_from_environment_and_reports_missing_autoexec(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_temp_config(tmp_path)
    saved_games = tmp_path / "profile" / "Saved Games" / "DCS.openbeta"
    saved_games.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))

    service = SetupWizardService(
        config_path=config_path,
        olympus_factory=lambda config: _FakeOlympus(config.base_url),
        grpc_factory=lambda config: _FakeGrpc(f"{config.host}:{config.port}"),
    )
    status = service.probe()

    assert status.saved_games_path.healthy is True
    assert status.saved_games_path.detected_value == str(saved_games)
    assert status.autoexec_status.healthy is False
    assert status.autoexec_status.metadata["required_lines"] == [AUTOEXEC_UNSAFE_API_LINE, AUTOEXEC_DOSTRING_LINE]


def test_setup_wizard_reports_missing_autoexec_lines(tmp_path: Path) -> None:
    config_path = _write_temp_config(tmp_path)
    saved_games = tmp_path / "Saved Games" / "DCS.openbeta"
    autoexec = saved_games / "Config" / "autoexec.cfg"
    autoexec.parent.mkdir(parents=True)
    autoexec.write_text("-- empty on purpose\n", encoding="utf-8")

    service = SetupWizardService(
        config_path=config_path,
        olympus_factory=lambda config: _FakeOlympus(config.base_url),
        grpc_factory=lambda config: _FakeGrpc(f"{config.host}:{config.port}"),
    )
    status = service.probe(saved_games_path=str(saved_games))

    assert status.autoexec_status.healthy is False
    assert AUTOEXEC_UNSAFE_API_LINE in status.autoexec_status.metadata["missing_lines"]
    assert AUTOEXEC_DOSTRING_LINE in status.autoexec_status.metadata["missing_lines"]
