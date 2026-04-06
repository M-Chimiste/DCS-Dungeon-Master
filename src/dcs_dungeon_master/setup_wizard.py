"""Setup/configuration wizard services for local operator onboarding."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
from typing import Any, Callable

from dcs_dungeon_master.core.config import (
    AppConfig,
    GrpcConfig,
    ModelBackendConfig,
    ModelCatalogEntryConfig,
    ModelRoutingConfig,
    OlympusConfig,
    load_config,
)
from dcs_dungeon_master.core.enums import ModelHostingMode
from dcs_dungeon_master.core.exceptions import ConfigError
from dcs_dungeon_master.core.models import (
    SetupCheckResult,
    SetupConfigWriteResult,
    SetupRecommendation,
    SetupWizardStatus,
)
from dcs_dungeon_master.integration.grpc_client import DcsGrpcClient
from dcs_dungeon_master.integration.olympus import OlympusClient


AUTOEXEC_UNSAFE_API_LINE = 'net.allow_unsafe_api = { "userhooks" }'
AUTOEXEC_DOSTRING_LINE = 'net.allow_dostring_in = { "server" }'


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if value is None:
        raise ValueError("None cannot be serialized directly into TOML.")
    return json.dumps(str(value))


def _recommendation(code: str, message: str, action: str | None = None) -> SetupRecommendation:
    return SetupRecommendation(code=code, message=message, action=action)


class SetupWizardService:
    """Environment probe and local-config generation helper."""

    def __init__(
        self,
        *,
        config_path: str | Path,
        template_config_path: str | Path | None = None,
        olympus_factory: Callable[[OlympusConfig], Any] | None = None,
        grpc_factory: Callable[[GrpcConfig], Any] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.template_config_path = Path(template_config_path or "config/milestone0.toml")
        self._olympus_factory = olympus_factory or OlympusClient
        self._grpc_factory = grpc_factory or DcsGrpcClient
        self._loaded_config, self._config_error = self._load_optional_config(self.config_path)
        self._template_config, self._template_error = self._load_optional_config(self.template_config_path)

    @property
    def status(self) -> str:
        return "setup-wizard-ready"

    def probe(
        self,
        *,
        saved_games_path: str | None = None,
        olympus_url: str | None = None,
        grpc_host: str | None = None,
        grpc_port: int | None = None,
    ) -> SetupWizardStatus:
        resolved_saved_games = self._resolve_saved_games_path(saved_games_path)
        saved_games_result = self._check_saved_games_path(saved_games_path, resolved_saved_games)
        autoexec_result = self._check_autoexec(resolved_saved_games)
        base_config = self._effective_base_config()
        olympus_result = self._probe_olympus(base_config, olympus_url=olympus_url)
        grpc_result = self._probe_grpc(base_config, grpc_host=grpc_host, grpc_port=grpc_port)
        config_result = self._check_config_status()

        results = (saved_games_result, autoexec_result, olympus_result, grpc_result, config_result)
        healthy_count = sum(1 for item in results if item.healthy)
        overall_status = "ready" if healthy_count == len(results) else "needs_attention"
        recommendations = self._build_recommendations(results)
        return SetupWizardStatus(
            config_path=str(self.config_path),
            saved_games_path=saved_games_result,
            autoexec_status=autoexec_result,
            olympus_status=olympus_result,
            grpc_status=grpc_result,
            config_status=config_result,
            overall_status=overall_status,
            recommended_actions=tuple(recommendations),
        )

    def write_local_config(
        self,
        *,
        output_path: str | Path,
        saved_games_path: str | None = None,
        olympus_url: str | None = None,
        grpc_host: str | None = None,
        grpc_port: int | None = None,
    ) -> SetupConfigWriteResult:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        generated = self.generate_config_text(
            saved_games_path=saved_games_path,
            olympus_url=olympus_url,
            grpc_host=grpc_host,
            grpc_port=grpc_port,
        )
        previous = output.read_text(encoding="utf-8") if output.exists() else None
        output.write_text(generated, encoding="utf-8")
        changed = previous != generated
        return SetupConfigWriteResult(
            output_path=str(output),
            written=True,
            changed=changed,
            overwritten=previous is not None,
            detail="Wrote generated local config." if changed or previous is None else "Config already matched generated output.",
            saved_games_path=saved_games_path or self._saved_games_value_from_config() or self._detect_saved_games_path_str(),
        )

    def generate_config_text(
        self,
        *,
        saved_games_path: str | None = None,
        olympus_url: str | None = None,
        grpc_host: str | None = None,
        grpc_port: int | None = None,
    ) -> str:
        base = self._effective_base_config()
        dcs = replace(
            base.dcs,
            olympus=replace(base.dcs.olympus, base_url=(olympus_url or base.dcs.olympus.base_url)),
            grpc=replace(
                base.dcs.grpc,
                host=(grpc_host or base.dcs.grpc.host),
                port=(grpc_port if grpc_port is not None else base.dcs.grpc.port),
            ),
        )
        setup = replace(base.setup, saved_games_path=saved_games_path or base.setup.saved_games_path)
        effective = replace(base, dcs=dcs, setup=setup)
        return self._serialize_config(effective)

    def _load_optional_config(self, path: Path) -> tuple[AppConfig | None, str | None]:
        try:
            return (load_config(path), None)
        except ConfigError as exc:
            return (None, str(exc))

    def _effective_base_config(self) -> AppConfig:
        if self._loaded_config is not None:
            return self._loaded_config
        if self._template_config is not None:
            return self._template_config
        raise ConfigError(self._config_error or self._template_error or "No usable config available for setup wizard.")

    def _resolve_saved_games_path(self, override: str | None) -> Path | None:
        if override:
            return Path(override).expanduser()
        configured = self._saved_games_value_from_config()
        if configured:
            return Path(configured).expanduser()
        detected = self._detect_saved_games_path_str()
        return Path(detected).expanduser() if detected else None

    def _saved_games_value_from_config(self) -> str | None:
        if self._loaded_config is not None and self._loaded_config.setup.saved_games_path:
            return self._loaded_config.setup.saved_games_path
        if self._template_config is not None and self._template_config.setup.saved_games_path:
            return self._template_config.setup.saved_games_path
        return None

    def _detect_saved_games_path_str(self) -> str | None:
        candidate_roots: list[Path] = []
        for env_var in ("USERPROFILE", "HOME", "OneDrive"):
            raw = os.getenv(env_var)
            if raw:
                candidate_roots.append(Path(raw).expanduser())
        candidate_roots.extend(
            [
                Path.home(),
                Path.home() / "Documents",
            ]
        )

        subdirs = (
            Path("Saved Games") / "DCS.openbeta",
            Path("Saved Games") / "DCS",
            Path("Saved Games") / "DCS.openbeta_server",
            Path("Documents") / "Saved Games" / "DCS.openbeta",
            Path("Documents") / "Saved Games" / "DCS",
        )
        seen: set[Path] = set()
        for root in candidate_roots:
            for subdir in subdirs:
                candidate = (root / subdir).expanduser()
                if candidate in seen:
                    continue
                seen.add(candidate)
                if candidate.exists():
                    return str(candidate)
        return None

    def _check_saved_games_path(self, override: str | None, resolved_path: Path | None) -> SetupCheckResult:
        if resolved_path is None:
            return SetupCheckResult(
                step="saved_games_path",
                status="needs_attention",
                healthy=False,
                detail="No DCS Saved Games directory was detected. Provide a manual path override.",
            )
        if not resolved_path.exists():
            return SetupCheckResult(
                step="saved_games_path",
                status="needs_attention",
                healthy=False,
                detail="Configured Saved Games path does not exist.",
                detected_value=str(resolved_path),
                metadata={"source": "manual_override" if override else "config"},
            )
        return SetupCheckResult(
            step="saved_games_path",
            status="ready",
            healthy=True,
            detail="Saved Games path detected.",
            detected_value=str(resolved_path),
            metadata={"source": "manual_override" if override else ("config" if self._saved_games_value_from_config() else "auto_detected")},
        )

    def _check_autoexec(self, saved_games_path: Path | None) -> SetupCheckResult:
        if saved_games_path is None:
            return SetupCheckResult(
                step="autoexec_status",
                status="needs_attention",
                healthy=False,
                detail="autoexec.cfg could not be checked because no Saved Games path is available.",
            )
        autoexec_path = saved_games_path / "Config" / "autoexec.cfg"
        if not autoexec_path.exists():
            return SetupCheckResult(
                step="autoexec_status",
                status="needs_attention",
                healthy=False,
                detail="autoexec.cfg is missing.",
                detected_value=str(autoexec_path),
                metadata={
                    "required_lines": [AUTOEXEC_UNSAFE_API_LINE, AUTOEXEC_DOSTRING_LINE],
                },
            )
        text = autoexec_path.read_text(encoding="utf-8", errors="ignore")
        has_unsafe = bool(re.search(r'net\.allow_unsafe_api\s*=\s*\{[^}]*"userhooks"[^}]*\}', text))
        has_dostring = bool(re.search(r'net\.allow_dostring_in\s*=\s*\{[^}]*"server"[^}]*\}', text))
        missing_lines: list[str] = []
        if not has_unsafe:
            missing_lines.append(AUTOEXEC_UNSAFE_API_LINE)
        if not has_dostring:
            missing_lines.append(AUTOEXEC_DOSTRING_LINE)
        return SetupCheckResult(
            step="autoexec_status",
            status="ready" if not missing_lines else "needs_attention",
            healthy=not missing_lines,
            detail="autoexec.cfg contains required Olympus permissions." if not missing_lines else "autoexec.cfg is missing required Olympus permissions.",
            detected_value=str(autoexec_path),
            metadata={
                "required_lines": [AUTOEXEC_UNSAFE_API_LINE, AUTOEXEC_DOSTRING_LINE],
                "missing_lines": missing_lines,
            },
        )

    def _probe_olympus(self, base_config: AppConfig, *, olympus_url: str | None) -> SetupCheckResult:
        config = replace(base_config.dcs.olympus, base_url=olympus_url or base_config.dcs.olympus.base_url)
        client = self._olympus_factory(config)
        try:
            health = client.check_health()
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        return SetupCheckResult(
            step="olympus_status",
            status="ready" if health.healthy else health.status,
            healthy=health.healthy,
            detail=health.detail,
            detected_value=health.endpoint,
            metadata={"service": health.service, "attempt_count": health.attempt_count},
        )

    def _probe_grpc(
        self,
        base_config: AppConfig,
        *,
        grpc_host: str | None,
        grpc_port: int | None,
    ) -> SetupCheckResult:
        config = replace(
            base_config.dcs.grpc,
            host=grpc_host or base_config.dcs.grpc.host,
            port=grpc_port if grpc_port is not None else base_config.dcs.grpc.port,
        )
        client = self._grpc_factory(config)
        metadata: dict[str, Any] = {}
        try:
            health = client.check_health()
            detail = health.detail
            if health.healthy:
                try:
                    snapshot = client.get_mission_metadata()
                    metadata.update(
                        {
                            "mission_name": snapshot.mission_name,
                            "theater": snapshot.theater,
                            "mission_time": snapshot.mission_time,
                        }
                    )
                    detail = f"{detail}; mission={snapshot.mission_name or 'unknown'}"
                except Exception as exc:  # noqa: BLE001
                    detail = f"{detail}; metadata_probe_failed={exc}"
                    health = replace(health, healthy=False, status="needs_attention", detail=detail)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        return SetupCheckResult(
            step="grpc_status",
            status="ready" if health.healthy else health.status,
            healthy=health.healthy,
            detail=health.detail,
            detected_value=health.endpoint,
            metadata={"service": health.service, "attempt_count": health.attempt_count, **metadata},
        )

    def _check_config_status(self) -> SetupCheckResult:
        if self._loaded_config is not None:
            return SetupCheckResult(
                step="config_status",
                status="ready",
                healthy=True,
                detail="Config file loaded successfully.",
                detected_value=str(self.config_path),
            )
        if self.config_path.exists():
            return SetupCheckResult(
                step="config_status",
                status="needs_attention",
                healthy=False,
                detail=self._config_error or "Config file could not be parsed.",
                detected_value=str(self.config_path),
            )
        return SetupCheckResult(
            step="config_status",
            status="needs_attention",
            healthy=False,
            detail="Config file does not exist yet. The wizard can generate one.",
            detected_value=str(self.config_path),
        )

    def _build_recommendations(self, results: tuple[SetupCheckResult, ...]) -> list[SetupRecommendation]:
        recommendations: list[SetupRecommendation] = []
        for item in results:
            if item.step == "saved_games_path" and not item.healthy:
                recommendations.append(
                    _recommendation(
                        "set_saved_games_path",
                        "Provide or confirm the DCS Saved Games path before relying on local mission hooks.",
                        "Run setup-wizard with --saved-games or use the Setup page in the web UI.",
                    )
                )
            elif item.step == "autoexec_status" and not item.healthy:
                recommendations.append(
                    _recommendation(
                        "update_autoexec_cfg",
                        "Add the required unsafe API permissions to autoexec.cfg for Olympus integration.",
                        f"Add {AUTOEXEC_UNSAFE_API_LINE} and {AUTOEXEC_DOSTRING_LINE}.",
                    )
                )
            elif item.step == "olympus_status" and not item.healthy:
                recommendations.append(
                    _recommendation(
                        "check_olympus_endpoint",
                        "Verify Olympus is installed, running, and reachable at the configured endpoint.",
                        "Confirm Olympus service status and base URL.",
                    )
                )
            elif item.step == "grpc_status" and not item.healthy:
                recommendations.append(
                    _recommendation(
                        "check_grpc_endpoint",
                        "Verify DCS-gRPC is installed and reachable at the configured host/port.",
                        "Confirm DCS-gRPC server startup and network settings.",
                    )
                )
            elif item.step == "config_status" and not item.healthy:
                recommendations.append(
                    _recommendation(
                        "write_local_config",
                        "Generate a local config file before running loops or the web UI against your environment.",
                        "Use setup-write-config or the Setup page to write config/local.toml.",
                    )
                )
        if not recommendations:
            recommendations.append(_recommendation("environment_ready", "Environment checks passed.", None))
        return recommendations

    def _serialize_config(self, config: AppConfig) -> str:
        lines = [
            "[runtime]",
            f'app_name = {_toml_value(config.runtime.app_name)}',
            f'environment = {_toml_value(config.runtime.environment)}',
            f"dry_run = {_toml_value(config.runtime.dry_run)}",
            "",
            "[logging]",
            f'level = {_toml_value(config.logging.level)}',
            f'format = {_toml_value(config.logging.format.value)}',
            "",
            "[dcs]",
            f"remote_hosted = {_toml_value(config.dcs.remote_hosted)}",
            "",
            "[dcs.olympus]",
            f'base_url = {_toml_value(config.dcs.olympus.base_url)}',
            f"timeout_sec = {_toml_value(config.dcs.olympus.timeout_sec)}",
            f"verify_tls = {_toml_value(config.dcs.olympus.verify_tls)}",
            f'health_path = {_toml_value(config.dcs.olympus.health_path)}',
            f"retry_attempts = {_toml_value(config.dcs.olympus.retry_attempts)}",
        ]
        if config.dcs.olympus.username:
            lines.append(f'username = {_toml_value(config.dcs.olympus.username)}')
        if config.dcs.olympus.password_env_var:
            lines.append(f'password_env_var = {_toml_value(config.dcs.olympus.password_env_var)}')
        if config.dcs.olympus.token_env_var:
            lines.append(f'token_env_var = {_toml_value(config.dcs.olympus.token_env_var)}')
        lines.extend(
            [
                "",
                "[dcs.grpc]",
                f'host = {_toml_value(config.dcs.grpc.host)}',
                f"port = {_toml_value(config.dcs.grpc.port)}",
                f"timeout_sec = {_toml_value(config.dcs.grpc.timeout_sec)}",
                f"secure = {_toml_value(config.dcs.grpc.secure)}",
                f"retry_attempts = {_toml_value(config.dcs.grpc.retry_attempts)}",
            ]
        )
        if config.dcs.grpc.server_host_override:
            lines.append(f'server_host_override = {_toml_value(config.dcs.grpc.server_host_override)}')

        for backend in config.models:
            lines.extend(self._serialize_model_backend(backend))
        for entry in config.model_catalog:
            lines.extend(self._serialize_model_catalog_entry(entry))
        lines.extend(self._serialize_model_routing(config.model_routing))
        lines.extend(
            [
                "",
                "[scenario]",
                f'id = {_toml_value(config.scenario.id)}',
                f'registry_path = {_toml_value(config.scenario.registry_path)}',
                "",
                "[persistence]",
                f'db_path = {_toml_value(config.persistence.db_path)}',
                f"enable_wal = {_toml_value(config.persistence.enable_wal)}",
                "",
                "[dry_run]",
                f"enabled = {_toml_value(config.dry_run.enabled)}",
                f'summary_output = {_toml_value(config.dry_run.summary_output)}',
                "",
                "[fog_of_war]",
                f"freshness_window_sec = {_toml_value(config.fog_of_war.freshness_window_sec)}",
                f"decay_window_sec = {_toml_value(config.fog_of_war.decay_window_sec)}",
                f"archive_window_sec = {_toml_value(config.fog_of_war.archive_window_sec)}",
                f"adjacency_drift_enabled = {_toml_value(config.fog_of_war.adjacency_drift_enabled)}",
                f'inference_mode = {_toml_value(config.fog_of_war.inference_mode.value)}',
                f"debug_truth_comparison = {_toml_value(config.fog_of_war.debug_truth_comparison)}",
                "",
                "[multimodal]",
                f"enabled = {_toml_value(config.multimodal.enabled)}",
                f'output_dir = {_toml_value(config.multimodal.output_dir)}',
            ]
        )
        if config.setup.saved_games_path:
            lines.extend(
                [
                    "",
                    "[setup]",
                    f'saved_games_path = {_toml_value(config.setup.saved_games_path)}',
                ]
            )
        lines.extend(
            [
                "",
                "[evaluation]",
                f'profile_name = {_toml_value(config.evaluation.profile_name)}',
                f"decision_cadence_sec = {_toml_value(config.evaluation.decision_cadence_sec)}",
                f'prompt_version = {_toml_value(config.evaluation.prompt_version)}',
                f'resource_settings_version = {_toml_value(config.evaluation.resource_settings_version)}',
                f'review_policy = {_toml_value(config.evaluation.review_policy.value)}',
                f'fairness_mode = {_toml_value(config.evaluation.fairness_mode.value)}',
                f"default_run_cycles = {_toml_value(config.evaluation.default_run_cycles)}",
            ]
        )
        if config.evaluation.scenario_seed:
            lines.append(f'scenario_seed = {_toml_value(config.evaluation.scenario_seed)}')
        if config.evaluation.notes:
            lines.append(f'notes = {_toml_value(config.evaluation.notes)}')
        for case in config.evaluation.matrix_cases:
            lines.extend(
                [
                    "",
                    "[[evaluation.matrix_cases]]",
                    f'id = {_toml_value(case.id)}',
                    f'description = {_toml_value(case.description)}',
                    f'mode = {_toml_value(case.mode)}',
                    f'red_backend = {_toml_value(case.red_backend)}',
                    f'blue_backend = {_toml_value(case.blue_backend)}',
                    f"decision_cadence_sec = {_toml_value(case.decision_cadence_sec)}",
                    f"run_cycles = {_toml_value(case.run_cycles)}",
                    f"repeat_count = {_toml_value(case.repeat_count)}",
                ]
            )
            if case.system_prompt_variant_override:
                lines.append(
                    f'system_prompt_variant_override = {_toml_value(case.system_prompt_variant_override)}'
                )
            if case.visual_attachment:
                lines.append(f"visual_attachment = {_toml_value(case.visual_attachment)}")
        return "\n".join(lines).strip() + "\n"

    def _serialize_model_backend(self, backend: ModelBackendConfig) -> list[str]:
        lines = [
            "",
            "[[models]]",
            f'name = {_toml_value(backend.name)}',
            f'hosting_mode = {_toml_value(backend.hosting_mode.value if isinstance(backend.hosting_mode, ModelHostingMode) else backend.hosting_mode)}',
            f'endpoint = {_toml_value(backend.endpoint)}',
            f'model = {_toml_value(backend.model)}',
            f"enabled = {_toml_value(backend.enabled)}",
            f"multimodal = {_toml_value(backend.multimodal)}",
            f"timeout_sec = {_toml_value(backend.timeout_sec)}",
            f"max_retries = {_toml_value(backend.max_retries)}",
            f"temperature = {_toml_value(backend.temperature)}",
        ]
        if backend.api_key_env_var:
            lines.append(f'api_key_env_var = {_toml_value(backend.api_key_env_var)}')
        if backend.max_output_tokens is not None:
            lines.append(f"max_output_tokens = {_toml_value(backend.max_output_tokens)}")
        if backend.system_prompt_variant:
            lines.append(f'system_prompt_variant = {_toml_value(backend.system_prompt_variant)}')
        return lines

    def _serialize_model_catalog_entry(self, entry: ModelCatalogEntryConfig) -> list[str]:
        lines = [
            "",
            "[[model_catalog]]",
            f'id = {_toml_value(entry.id)}',
            f'display_name = {_toml_value(entry.display_name)}',
            f'backend_name = {_toml_value(entry.backend_name)}',
            f'server_label = {_toml_value(entry.server_label)}',
        ]
        if not entry.enabled:
            lines.append(f"enabled = {_toml_value(entry.enabled)}")
        if entry.hidden:
            lines.append(f"hidden = {_toml_value(entry.hidden)}")
        if entry.tags:
            lines.append(f"tags = {_toml_value(entry.tags)}")
        return lines

    def _serialize_model_routing(self, routing: ModelRoutingConfig) -> list[str]:
        lines = ["", "[model_routing]"]

        def configured_value(primary: str | None, catalog_id: str | None) -> str | None:
            return catalog_id or primary

        shared_default = configured_value(routing.default_backend, routing.default_catalog_id)
        if shared_default is not None:
            lines.append(f"default_backend = {_toml_value(shared_default)}")
            shared_fallback = configured_value(routing.default_fallback_backend, routing.default_fallback_catalog_id)
            if shared_fallback is not None:
                lines.append(f"default_fallback_backend = {_toml_value(shared_fallback)}")
            red_override = configured_value(routing.red_backend_override, routing.red_catalog_override)
            blue_override = configured_value(routing.blue_backend_override, routing.blue_catalog_override)
            red_fallback = configured_value(routing.red_fallback_backend_override, routing.red_fallback_catalog_override)
            blue_fallback = configured_value(routing.blue_fallback_backend_override, routing.blue_fallback_catalog_override)
            if red_override is not None:
                lines.append(f"red_backend_override = {_toml_value(red_override)}")
            if blue_override is not None:
                lines.append(f"blue_backend_override = {_toml_value(blue_override)}")
            if red_fallback is not None:
                lines.append(f"red_fallback_backend_override = {_toml_value(red_fallback)}")
            if blue_fallback is not None:
                lines.append(f"blue_fallback_backend_override = {_toml_value(blue_fallback)}")
            return lines

        red_backend = configured_value(routing.red_backend, routing.red_catalog_override) or routing.red_backend
        blue_backend = configured_value(routing.blue_backend, routing.blue_catalog_override) or routing.blue_backend
        lines.append(f"red_backend = {_toml_value(red_backend)}")
        lines.append(f"blue_backend = {_toml_value(blue_backend)}")
        red_fallback_backend = configured_value(routing.red_fallback_backend, routing.red_fallback_catalog_override)
        blue_fallback_backend = configured_value(routing.blue_fallback_backend, routing.blue_fallback_catalog_override)
        if red_fallback_backend is not None:
            lines.append(f"red_fallback_backend = {_toml_value(red_fallback_backend)}")
        if blue_fallback_backend is not None:
            lines.append(f"blue_fallback_backend = {_toml_value(blue_fallback_backend)}")
        return lines
