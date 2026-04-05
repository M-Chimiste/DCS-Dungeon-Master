"""Configuration models and loading helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import tomllib

from dcs_dungeon_master.core.enums import LoggingFormat, ModelHostingMode
from dcs_dungeon_master.core.exceptions import ConfigError


@dataclass(slots=True, frozen=True)
class RuntimeConfig:
    app_name: str
    environment: str
    dry_run: bool


@dataclass(slots=True, frozen=True)
class LoggingConfig:
    level: str
    format: LoggingFormat


@dataclass(slots=True, frozen=True)
class OlympusConfig:
    base_url: str
    timeout_sec: float


@dataclass(slots=True, frozen=True)
class GrpcConfig:
    host: str
    port: int
    timeout_sec: float


@dataclass(slots=True, frozen=True)
class DcsConfig:
    remote_hosted: bool
    olympus: OlympusConfig
    grpc: GrpcConfig


@dataclass(slots=True, frozen=True)
class ModelBackendConfig:
    name: str
    hosting_mode: ModelHostingMode
    endpoint: str
    model: str
    enabled: bool = False
    multimodal: bool = False
    api_key_env_var: str | None = None


@dataclass(slots=True, frozen=True)
class ScenarioConfig:
    id: str


@dataclass(slots=True, frozen=True)
class DryRunConfig:
    enabled: bool
    summary_output: str = "text"


@dataclass(slots=True, frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    logging: LoggingConfig
    dcs: DcsConfig
    models: tuple[ModelBackendConfig, ...] = field(default_factory=tuple)
    scenario: ScenarioConfig = field(default_factory=lambda: ScenarioConfig(id=""))
    dry_run: DryRunConfig = field(default_factory=lambda: DryRunConfig(enabled=True))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"Config section '{key}' is required and must be a table.")
    return value


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Config field '{key}' is required and must be a non-empty string.")
    return value


def _require_bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"Config field '{key}' is required and must be a boolean.")
    return value


def _require_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise ConfigError(f"Config field '{key}' is required and must be an integer.")
    return value


def _require_float(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, int | float):
        raise ConfigError(f"Config field '{key}' is required and must be numeric.")
    return float(value)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")

    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    runtime = _require_mapping(raw, "runtime")
    logging = _require_mapping(raw, "logging")
    dcs = _require_mapping(raw, "dcs")
    scenario = _require_mapping(raw, "scenario")
    dry_run = _require_mapping(raw, "dry_run")

    olympus = _require_mapping(dcs, "olympus")
    grpc = _require_mapping(dcs, "grpc")

    models_raw = raw.get("models", [])
    if not isinstance(models_raw, list):
        raise ConfigError("Config field 'models' must be an array of tables when provided.")

    backends = []
    for index, model_raw in enumerate(models_raw):
        if not isinstance(model_raw, dict):
            raise ConfigError(f"Config field 'models[{index}]' must be a table.")
        hosting_mode = _require_str(model_raw, "hosting_mode")
        try:
            parsed_hosting_mode = ModelHostingMode(hosting_mode)
        except ValueError as exc:
            raise ConfigError(
                f"Config field 'models[{index}].hosting_mode' must be one of: "
                f"{', '.join(mode.value for mode in ModelHostingMode)}."
            ) from exc
        backends.append(
            ModelBackendConfig(
                name=_require_str(model_raw, "name"),
                hosting_mode=parsed_hosting_mode,
                endpoint=_require_str(model_raw, "endpoint"),
                model=_require_str(model_raw, "model"),
                enabled=bool(model_raw.get("enabled", False)),
                multimodal=bool(model_raw.get("multimodal", False)),
                api_key_env_var=model_raw.get("api_key_env_var"),
            )
        )

    try:
        log_format = LoggingFormat(_require_str(logging, "format"))
    except ValueError as exc:
        raise ConfigError(
            f"Config field 'logging.format' must be one of: "
            f"{', '.join(item.value for item in LoggingFormat)}."
        ) from exc

    return AppConfig(
        runtime=RuntimeConfig(
            app_name=_require_str(runtime, "app_name"),
            environment=_require_str(runtime, "environment"),
            dry_run=_require_bool(runtime, "dry_run"),
        ),
        logging=LoggingConfig(
            level=_require_str(logging, "level"),
            format=log_format,
        ),
        dcs=DcsConfig(
            remote_hosted=bool(dcs.get("remote_hosted", False)),
            olympus=OlympusConfig(
                base_url=_require_str(olympus, "base_url"),
                timeout_sec=_require_float(olympus, "timeout_sec"),
            ),
            grpc=GrpcConfig(
                host=_require_str(grpc, "host"),
                port=_require_int(grpc, "port"),
                timeout_sec=_require_float(grpc, "timeout_sec"),
            ),
        ),
        models=tuple(backends),
        scenario=ScenarioConfig(id=_require_str(scenario, "id")),
        dry_run=DryRunConfig(
            enabled=_require_bool(dry_run, "enabled"),
            summary_output=_require_str(dry_run, "summary_output"),
        ),
    )
