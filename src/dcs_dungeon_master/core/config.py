"""Configuration models and loading helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import tomllib

from dcs_dungeon_master.core.enums import (
    EvaluationReviewPolicy,
    FairnessMode,
    InferenceMode,
    LoggingFormat,
    ModelHostingMode,
)
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
    verify_tls: bool = True
    health_path: str = "/olympus/mission"
    retry_attempts: int = 1
    username: str | None = None
    password_env_var: str | None = None
    token_env_var: str | None = None


@dataclass(slots=True, frozen=True)
class GrpcConfig:
    host: str
    port: int
    timeout_sec: float
    secure: bool = False
    retry_attempts: int = 1
    server_host_override: str | None = None


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
    timeout_sec: float = 30.0
    max_retries: int = 1
    temperature: float = 0.2
    max_output_tokens: int | None = None
    system_prompt_variant: str | None = None


@dataclass(slots=True, frozen=True)
class ModelRoutingConfig:
    red_backend: str
    blue_backend: str


@dataclass(slots=True, frozen=True)
class ScenarioConfig:
    id: str
    registry_path: str


@dataclass(slots=True, frozen=True)
class PersistenceConfig:
    db_path: str
    enable_wal: bool = True


@dataclass(slots=True, frozen=True)
class DryRunConfig:
    enabled: bool
    summary_output: str = "text"


@dataclass(slots=True, frozen=True)
class FogOfWarConfig:
    freshness_window_sec: int = 300
    decay_window_sec: int = 900
    archive_window_sec: int = 1800
    adjacency_drift_enabled: bool = True
    inference_mode: InferenceMode = InferenceMode.CONSERVATIVE
    debug_truth_comparison: bool = False


@dataclass(slots=True, frozen=True)
class EvaluationMatrixCaseConfig:
    id: str
    description: str
    mode: str
    red_backend: str
    blue_backend: str
    decision_cadence_sec: int
    run_cycles: int
    repeat_count: int = 1
    system_prompt_variant_override: str | None = None
    visual_attachment: bool = False


@dataclass(slots=True, frozen=True)
class EvaluationProfileConfig:
    profile_name: str = "phase1_baseline"
    decision_cadence_sec: int = 30
    prompt_version: str = "phase1_default"
    resource_settings_version: str = "phase1_default"
    review_policy: EvaluationReviewPolicy = EvaluationReviewPolicy.SUSPICIOUS_ONLY
    fairness_mode: FairnessMode = FairnessMode.HYBRID
    default_run_cycles: int = 90
    scenario_seed: str | None = None
    notes: str | None = None
    matrix_cases: tuple[EvaluationMatrixCaseConfig, ...] = ()


@dataclass(slots=True, frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    logging: LoggingConfig
    dcs: DcsConfig
    models: tuple[ModelBackendConfig, ...] = field(default_factory=tuple)
    model_routing: ModelRoutingConfig = field(default_factory=lambda: ModelRoutingConfig(red_backend="", blue_backend=""))
    scenario: ScenarioConfig = field(default_factory=lambda: ScenarioConfig(id="", registry_path=""))
    persistence: PersistenceConfig = field(default_factory=lambda: PersistenceConfig(db_path=""))
    dry_run: DryRunConfig = field(default_factory=lambda: DryRunConfig(enabled=True))
    fog_of_war: FogOfWarConfig = field(default_factory=FogOfWarConfig)
    evaluation: EvaluationProfileConfig = field(default_factory=EvaluationProfileConfig)

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


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Config field '{key}' must be a non-empty string when provided.")
    return value


def _non_negative_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or value < 0:
        raise ConfigError(f"Config field '{key}' must be a non-negative integer.")
    return value


def _positive_float(data: dict[str, Any], key: str) -> float:
    value = _require_float(data, key)
    if value <= 0:
        raise ConfigError(f"Config field '{key}' must be greater than zero.")
    return value


def _non_negative_optional_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise ConfigError(f"Config field '{key}' must be a non-negative integer when provided.")
    return value


def _bounded_float(data: dict[str, Any], key: str, *, minimum: float, maximum: float, default: float) -> float:
    value = data.get(key, default)
    if not isinstance(value, int | float):
        raise ConfigError(f"Config field '{key}' must be numeric.")
    parsed = float(value)
    if parsed < minimum or parsed > maximum:
        raise ConfigError(f"Config field '{key}' must be between {minimum} and {maximum}.")
    return parsed


def _positive_int(data: dict[str, Any], key: str) -> int:
    value = _require_int(data, key)
    if value <= 0:
        raise ConfigError(f"Config field '{key}' must be greater than zero.")
    return value


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
    persistence = _require_mapping(raw, "persistence")
    dry_run = _require_mapping(raw, "dry_run")
    model_routing = _require_mapping(raw, "model_routing")
    evaluation = raw.get("evaluation", {})
    if evaluation is None:
        evaluation = {}
    if not isinstance(evaluation, dict):
        raise ConfigError("Config section 'evaluation' must be a table when provided.")
    fog_of_war = raw.get("fog_of_war", {})
    if fog_of_war is None:
        fog_of_war = {}
    if not isinstance(fog_of_war, dict):
        raise ConfigError("Config section 'fog_of_war' must be a table when provided.")
    evaluation_matrix_raw = evaluation.get("matrix_cases", [])
    if not isinstance(evaluation_matrix_raw, list):
        raise ConfigError("Config field 'evaluation.matrix_cases' must be an array of tables when provided.")

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
                timeout_sec=_positive_float(model_raw, "timeout_sec") if "timeout_sec" in model_raw else 30.0,
                max_retries=_non_negative_int(model_raw, "max_retries", 1),
                temperature=_bounded_float(model_raw, "temperature", minimum=0.0, maximum=2.0, default=0.2),
                max_output_tokens=_non_negative_optional_int(model_raw, "max_output_tokens"),
                system_prompt_variant=_optional_str(model_raw, "system_prompt_variant"),
            )
        )

    if not backends:
        raise ConfigError("Config must define at least one model backend in the 'models' array.")

    backend_names = {backend.name for backend in backends}
    red_backend = _require_str(model_routing, "red_backend")
    blue_backend = _require_str(model_routing, "blue_backend")
    if red_backend not in backend_names:
        raise ConfigError(f"Config field 'model_routing.red_backend' references unknown backend '{red_backend}'.")
    if blue_backend not in backend_names:
        raise ConfigError(f"Config field 'model_routing.blue_backend' references unknown backend '{blue_backend}'.")
    backend_by_name = {backend.name: backend for backend in backends}
    if not backend_by_name[red_backend].enabled:
        raise ConfigError(f"Config field 'model_routing.red_backend' must reference an enabled backend, got '{red_backend}'.")
    if not backend_by_name[blue_backend].enabled:
        raise ConfigError(f"Config field 'model_routing.blue_backend' must reference an enabled backend, got '{blue_backend}'.")

    try:
        log_format = LoggingFormat(_require_str(logging, "format"))
    except ValueError as exc:
        raise ConfigError(
            f"Config field 'logging.format' must be one of: "
            f"{', '.join(item.value for item in LoggingFormat)}."
        ) from exc

    try:
        inference_mode = InferenceMode(_optional_str(fog_of_war, "inference_mode") or "conservative")
    except ValueError as exc:
        raise ConfigError(
            "Config field 'fog_of_war.inference_mode' must be one of: "
            f"{', '.join(item.value for item in InferenceMode)}."
        ) from exc

    try:
        review_policy = EvaluationReviewPolicy(_optional_str(evaluation, "review_policy") or "suspicious_only")
    except ValueError as exc:
        raise ConfigError(
            "Config field 'evaluation.review_policy' must be one of: "
            f"{', '.join(item.value for item in EvaluationReviewPolicy)}."
        ) from exc

    try:
        fairness_mode = FairnessMode(_optional_str(evaluation, "fairness_mode") or "hybrid")
    except ValueError as exc:
        raise ConfigError(
            "Config field 'evaluation.fairness_mode' must be one of: "
            f"{', '.join(item.value for item in FairnessMode)}."
        ) from exc

    matrix_cases = []
    for index, item in enumerate(evaluation_matrix_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"Config field 'evaluation.matrix_cases[{index}]' must be a table.")
        mode = _require_str(item, "mode")
        if mode not in {"dry", "live"}:
            raise ConfigError(f"Config field 'evaluation.matrix_cases[{index}].mode' must be 'dry' or 'live'.")
        red_case_backend = _require_str(item, "red_backend")
        blue_case_backend = _require_str(item, "blue_backend")
        if red_case_backend not in backend_names:
            raise ConfigError(
                f"Config field 'evaluation.matrix_cases[{index}].red_backend' references unknown backend '{red_case_backend}'."
            )
        if blue_case_backend not in backend_names:
            raise ConfigError(
                f"Config field 'evaluation.matrix_cases[{index}].blue_backend' references unknown backend '{blue_case_backend}'."
            )
        matrix_cases.append(
            EvaluationMatrixCaseConfig(
                id=_require_str(item, "id"),
                description=_require_str(item, "description"),
                mode=mode,
                red_backend=red_case_backend,
                blue_backend=blue_case_backend,
                decision_cadence_sec=_positive_int(item, "decision_cadence_sec"),
                run_cycles=_positive_int(item, "run_cycles"),
                repeat_count=_positive_int(item, "repeat_count") if "repeat_count" in item else 1,
                system_prompt_variant_override=_optional_str(item, "system_prompt_variant_override"),
                visual_attachment=bool(item.get("visual_attachment", False)),
            )
        )

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
                timeout_sec=_positive_float(olympus, "timeout_sec"),
                verify_tls=bool(olympus.get("verify_tls", True)),
                health_path=_optional_str(olympus, "health_path") or "/olympus/mission",
                retry_attempts=_non_negative_int(olympus, "retry_attempts", 1),
                username=_optional_str(olympus, "username"),
                password_env_var=_optional_str(olympus, "password_env_var"),
                token_env_var=_optional_str(olympus, "token_env_var"),
            ),
            grpc=GrpcConfig(
                host=_require_str(grpc, "host"),
                port=_require_int(grpc, "port"),
                timeout_sec=_positive_float(grpc, "timeout_sec"),
                secure=bool(grpc.get("secure", False)),
                retry_attempts=_non_negative_int(grpc, "retry_attempts", 1),
                server_host_override=_optional_str(grpc, "server_host_override"),
            ),
        ),
        models=tuple(backends),
        model_routing=ModelRoutingConfig(red_backend=red_backend, blue_backend=blue_backend),
        scenario=ScenarioConfig(
            id=_require_str(scenario, "id"),
            registry_path=_require_str(scenario, "registry_path"),
        ),
        persistence=PersistenceConfig(
            db_path=_require_str(persistence, "db_path"),
            enable_wal=bool(persistence.get("enable_wal", True)),
        ),
        dry_run=DryRunConfig(
            enabled=_require_bool(dry_run, "enabled"),
            summary_output=_require_str(dry_run, "summary_output"),
        ),
        fog_of_war=FogOfWarConfig(
            freshness_window_sec=_non_negative_int(fog_of_war, "freshness_window_sec", 300),
            decay_window_sec=_non_negative_int(fog_of_war, "decay_window_sec", 900),
            archive_window_sec=_non_negative_int(fog_of_war, "archive_window_sec", 1800),
            adjacency_drift_enabled=bool(fog_of_war.get("adjacency_drift_enabled", True)),
            inference_mode=inference_mode,
            debug_truth_comparison=bool(fog_of_war.get("debug_truth_comparison", False)),
        ),
        evaluation=EvaluationProfileConfig(
            profile_name=_optional_str(evaluation, "profile_name") or "phase1_baseline",
            decision_cadence_sec=_positive_int(evaluation, "decision_cadence_sec")
            if "decision_cadence_sec" in evaluation
            else 30,
            prompt_version=_optional_str(evaluation, "prompt_version") or "phase1_default",
            resource_settings_version=_optional_str(evaluation, "resource_settings_version") or "phase1_default",
            review_policy=review_policy,
            fairness_mode=fairness_mode,
            default_run_cycles=_positive_int(evaluation, "default_run_cycles")
            if "default_run_cycles" in evaluation
            else 90,
            scenario_seed=_optional_str(evaluation, "scenario_seed"),
            notes=_optional_str(evaluation, "notes"),
            matrix_cases=tuple(matrix_cases),
        ),
    )
