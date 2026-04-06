"""Model adapter services and dry decision loop for Phase 1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.config import AppConfig, ModelBackendConfig, ModelRoutingConfig
from dcs_dungeon_master.core.enums import ActionType, Coalition, ModelHostingMode, RunLifecycleStatus
from dcs_dungeon_master.core.exceptions import IntegrationError, PersistenceError
from dcs_dungeon_master.core.models import (
    CommanderObservation,
    DecisionCycleResult,
    LoopRunResult,
    ModelBackendCapability,
    ModelCatalogEntry,
    ModelInvocationAttempt,
    ModelInvocationChainResult,
    ModelInvocationRequest,
    ModelInvocationResult,
    ObservationArtifact,
    ParsedActionProposal,
    ResolvedCoalitionRouting,
    ResolvedRunRouting,
    RunScopedBackendDefinition,
)
from dcs_dungeon_master.integration.ingest import IntegrationIngestCoordinator
from dcs_dungeon_master.core.versions import ACTION_SCHEMA_VERSION
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore


_SYSTEM_PROMPTS = {
    "phase1_default": (
        "You are a DCS strategic coalition commander. "
        "You must respond with JSON only. "
        "Return exactly one JSON object with an 'actions' array. "
        "Use only the allowed Phase 1 action types and stay within the supplied observation. "
        "Do not invent hidden enemy facts or unsupported entities. "
        "If no change is needed, return {'actions': []} or include a valid hold_action in that array."
    ),
}


@dataclass(slots=True, frozen=True)
class ModelBackendHealthStatus:
    backend_name: str
    endpoint: str
    healthy: bool
    detail: str


class OpenAICompatibleAdapter:
    """LM Studio / OpenAI-compatible model transport."""

    def __init__(self, config: ModelBackendConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._client = httpx.Client(
            base_url=config.endpoint.rstrip("/"),
            timeout=config.timeout_sec,
            transport=transport,
            headers=self._build_headers(),
        )

    @property
    def endpoint(self) -> str:
        return self.config.endpoint.rstrip("/")

    def close(self) -> None:
        self._client.close()

    def check_health(self) -> ModelBackendHealthStatus:
        try:
            response = self._client.get("models")
            response.raise_for_status()
            payload = response.json()
            model_count = len(payload.get("data", [])) if isinstance(payload, dict) else 0
            return ModelBackendHealthStatus(
                backend_name=self.config.name,
                endpoint=f"{self.endpoint}/models",
                healthy=True,
                detail=f"http {response.status_code}; models={model_count}",
            )
        except Exception as exc:  # noqa: BLE001
            return ModelBackendHealthStatus(
                backend_name=self.config.name,
                endpoint=f"{self.endpoint}/models",
                healthy=False,
                detail=str(exc),
            )

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResult:
        last_error: str | None = None
        last_raw_response: str | None = None
        last_wrapped_object: dict[str, Any] | None = None
        last_usage: tuple[int | None, int | None, int | None] = (None, None, None)
        requested_at = datetime.now(UTC)

        for attempt in range(1, self.config.max_retries + 2):
            attempt_started = datetime.now(UTC)
            try:
                response = self._client.post("chat/completions", json=request.request_payload)
                response.raise_for_status()
                payload = response.json()
                last_wrapped_object = payload if isinstance(payload, dict) else None
                content = self._extract_message_content(payload)
                last_raw_response = content
                last_usage = self._extract_usage(payload)
                parsed = self._parse_json_content(content)
                completed_at = datetime.now(UTC)
                return ModelInvocationResult(
                    id=None,
                    run_id=request.run_id,
                    coalition=request.coalition,
                    decision_cycle=request.decision_cycle,
                    backend_name=request.backend_name,
                    observation_id=request.observation_id,
                    requested_at=requested_at,
                    completed_at=completed_at,
                    status="succeeded",
                    attempt_count=attempt,
                    request_payload=request.request_payload,
                    raw_response=content,
                    parsed_actions=parsed.payload,
                    parse_status="parsed",
                    error_detail=None,
                    latency_ms=max(0, int((completed_at - attempt_started).total_seconds() * 1000)),
                    prompt_tokens=last_usage[0],
                    completion_tokens=last_usage[1],
                    total_tokens=last_usage[2],
                    validation_batch_id=None,
                    wrapped_response_object=last_wrapped_object,
                )
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                last_error = str(exc)
                if attempt > self.config.max_retries:
                    completed_at = datetime.now(UTC)
                    status = "parse_failed" if last_raw_response is not None else "transport_failed"
                    return ModelInvocationResult(
                        id=None,
                        run_id=request.run_id,
                        coalition=request.coalition,
                        decision_cycle=request.decision_cycle,
                        backend_name=request.backend_name,
                        observation_id=request.observation_id,
                        requested_at=requested_at,
                        completed_at=completed_at,
                        status=status,
                        attempt_count=attempt,
                        request_payload=request.request_payload,
                        raw_response=last_raw_response,
                        parsed_actions=(),
                        parse_status="failed" if last_raw_response is not None else "not_attempted",
                        error_detail=last_error,
                        latency_ms=max(0, int((completed_at - attempt_started).total_seconds() * 1000)),
                        prompt_tokens=last_usage[0],
                        completion_tokens=last_usage[1],
                        total_tokens=last_usage[2],
                        validation_batch_id=None,
                        wrapped_response_object=last_wrapped_object,
                    )
        raise IntegrationError(f"Model invocation failed unexpectedly: {last_error}")

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key_env_var:
            api_key = os.getenv(self.config.api_key_env_var)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _extract_message_content(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Model response did not include any choices.")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("Model response choice was not an object.")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("Model response did not include a message object.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Model response message content was empty.")
        return content.strip()

    def _parse_json_content(self, content: str) -> ParsedActionProposal:
        candidate = content.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                candidate = "\n".join(lines[1:-1]).strip()
        decoder = json.JSONDecoder()
        first_brace = min((index for index in (candidate.find("["), candidate.find("{")) if index >= 0), default=-1)
        if first_brace < 0:
            raise ValueError("Model response did not contain a JSON object or array.")
        parsed, _ = decoder.raw_decode(candidate[first_brace:])
        if isinstance(parsed, list):
            if any(not isinstance(item, dict) for item in parsed):
                raise ValueError("Model response action list must contain only objects.")
            return ParsedActionProposal(payload=tuple(parsed), raw_content=content, wrapped_object=None)
        if isinstance(parsed, dict):
            if "actions" in parsed:
                actions = parsed.get("actions")
                if not isinstance(actions, list) or any(not isinstance(item, dict) for item in actions):
                    raise ValueError("Model response 'actions' field must be a list of objects.")
                return ParsedActionProposal(payload=tuple(actions), raw_content=content, wrapped_object=parsed)
            if "action_type" in parsed:
                return ParsedActionProposal(payload=(parsed,), raw_content=content, wrapped_object=None)
        raise ValueError("Model response JSON did not match the action batch contract.")

    def _extract_usage(self, payload: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return (None, None, None)
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        return (
            prompt_tokens if isinstance(prompt_tokens, int) else None,
            completion_tokens if isinstance(completion_tokens, int) else None,
            total_tokens if isinstance(total_tokens, int) else None,
        )


@dataclass(slots=True)
class ModelAdapterRegistry:
    backends: dict[str, OpenAICompatibleAdapter]
    routing: ModelRoutingConfig
    capabilities: tuple[ModelBackendCapability, ...]
    catalog: tuple[ModelCatalogEntry, ...] = ()

    @property
    def status(self) -> str:
        return "model-adapters-ready"

    def resolve_backend_name(self, coalition: Coalition) -> str:
        return self.routing.red_backend if coalition is Coalition.RED else self.routing.blue_backend

    def resolve_fallback_backend_name(self, coalition: Coalition) -> str | None:
        return self.routing.red_fallback_backend if coalition is Coalition.RED else self.routing.blue_fallback_backend

    def resolve_backend(self, coalition: Coalition) -> OpenAICompatibleAdapter:
        return self.backends[self.resolve_backend_name(coalition)]

    def resolve_backend_chain(self, coalition: Coalition) -> tuple[tuple[str, OpenAICompatibleAdapter], ...]:
        names = [self.resolve_backend_name(coalition)]
        fallback = self.resolve_fallback_backend_name(coalition)
        if fallback and fallback not in names:
            names.append(fallback)
        return tuple((name, self.backends[name]) for name in names)

    def capability_for(self, backend_name: str) -> ModelBackendCapability:
        for item in self.capabilities:
            if item.backend_name == backend_name:
                return item
        raise KeyError(backend_name)

    def check_health(self) -> tuple[ModelBackendHealthStatus, ...]:
        return tuple(self.backends[name].check_health() for name in sorted(self.backends))

    def describe_backends(self) -> tuple[ModelBackendCapability, ...]:
        return self.capabilities

    def list_catalog(self) -> tuple[ModelCatalogEntry, ...]:
        return tuple(item for item in self.catalog if item.enabled and not item.hidden)

    def close(self) -> None:
        for adapter in self.backends.values():
            adapter.close()


@dataclass(slots=True)
class DryDecisionLoopRunner:
    store: SQLiteStateStore
    observation_builder: ObservationBuilder
    action_validator: ActionValidator
    model_registry: ModelAdapterRegistry
    ingest_coordinator: IntegrationIngestCoordinator | None = None

    @property
    def status(self) -> str:
        return "dry-loop-ready"

    def run_decision_cycle(
        self,
        run_id: str,
        decision_cycle: int,
        *,
        now: datetime | None = None,
        seconds_since_last_cycle: int = 30,
    ) -> DecisionCycleResult:
        if self.ingest_coordinator is not None:
            self.ingest_coordinator.ingest_once(run_id, occurred_at=now)
        multimodal_submission = self._multimodal_submission_map()
        red_observation, blue_observation = self.observation_builder.build_observation_pair(
            run_id,
            decision_cycle,
            now=now,
            seconds_since_last_cycle=seconds_since_last_cycle,
            persist=True,
            multimodal_submission=multimodal_submission,
        )
        red_result = self._invoke_and_validate(run_id, Coalition.RED, red_observation)
        blue_result = self._invoke_and_validate(run_id, Coalition.BLUE, blue_observation)
        cycle = DecisionCycleResult(
            id=None,
            run_id=run_id,
            decision_cycle=decision_cycle,
            snapshot_time=red_observation.generated_at,
            red_observation_id=red_observation.id,
            blue_observation_id=blue_observation.id,
            red_invocation_id=red_result.id,
            blue_invocation_id=blue_result.id,
            classification=self._classify_cycle(red_result, blue_result),
            summary=(
                f"red:{red_result.status}",
                f"blue:{blue_result.status}",
            ),
        )
        cycle = replace(cycle, id=self.store.save_decision_cycle_result(cycle))
        return cycle

    def run_decision_loop(
        self,
        run_id: str,
        *,
        start_decision_cycle: int,
        cycles: int,
        seconds_since_last_cycle: int = 30,
    ) -> LoopRunResult:
        results: list[DecisionCycleResult] = []
        for offset in range(cycles):
            state = self.store.get_run_control_state(run_id)
            if state.status is RunLifecycleStatus.CREATED:
                self.store.update_run_status(run_id, RunLifecycleStatus.RUNNING, changed_at=datetime.now(UTC))
            elif state.status is RunLifecycleStatus.PAUSED:
                return LoopRunResult(
                    run_id=run_id,
                    start_decision_cycle=start_decision_cycle,
                    requested_cycles=cycles,
                    completed_cycles=len(results),
                    stopped_early=True,
                    terminal_run_status=state.status,
                    results=tuple(results),
                )
            elif state.status in {RunLifecycleStatus.STOPPED, RunLifecycleStatus.FAILED}:
                raise PersistenceError(f"Run '{run_id}' is terminal with status '{state.status.value}'.")
            results.append(
                self.run_decision_cycle(
                    run_id,
                    start_decision_cycle + offset,
                    seconds_since_last_cycle=seconds_since_last_cycle,
                )
            )
        final_state = self.store.get_run_control_state(run_id)
        return LoopRunResult(
            run_id=run_id,
            start_decision_cycle=start_decision_cycle,
            requested_cycles=cycles,
            completed_cycles=len(results),
            stopped_early=len(results) != cycles,
            terminal_run_status=final_state.status,
            results=tuple(results),
        )

    def summarize_latest_response(self, run_id: str, coalition: Coalition) -> dict[str, Any]:
        result = self.store.get_latest_model_invocation(run_id, coalition)
        return {
            "invocation_id": result.id,
            "run_id": result.run_id,
            "coalition": result.coalition.value,
            "decision_cycle": result.decision_cycle,
            "backend_name": result.backend_name,
            "status": result.status,
            "parse_status": result.parse_status,
            "attempt_count": result.attempt_count,
            "latency_ms": result.latency_ms,
            "validation_batch_id": result.validation_batch_id,
            "raw_response_present": result.raw_response is not None,
            "parsed_action_count": len(result.parsed_actions),
            "error_detail": result.error_detail,
        }

    def _invoke_and_validate(
        self,
        run_id: str,
        coalition: Coalition,
        artifact: ObservationArtifact,
    ) -> ModelInvocationResult:
        chain_result = self._invoke_with_failover(run_id, coalition, artifact)
        result = chain_result.final_result
        validation_batch_id: int | None = None
        if result.status == "succeeded":
            validation_batch = self.action_validator.validate_payload(
                run_id,
                coalition,
                artifact.decision_cycle,
                list(result.parsed_actions),
                persist=True,
                submitted_at=result.completed_at,
            )
            validation_batch_id = validation_batch.id
            result = replace(result, validation_batch_id=validation_batch_id)
        result = replace(result, id=self.store.save_model_invocation_result(result))
        return result

    def _invoke_with_failover(
        self,
        run_id: str,
        coalition: Coalition,
        artifact: ObservationArtifact,
    ) -> ModelInvocationChainResult:
        attempts: list[ModelInvocationAttempt] = []
        final_result: ModelInvocationResult | None = None
        chain = self.model_registry.resolve_backend_chain(coalition)
        for index, (backend_name, backend) in enumerate(chain):
            request = self._build_request(run_id, coalition, artifact, backend_name, backend.config)
            result = backend.invoke(request)
            attempts.append(
                ModelInvocationAttempt(
                    backend_name=backend_name,
                    status=result.status,
                    parse_status=result.parse_status,
                    error_detail=result.error_detail,
                )
            )
            final_result = replace(
                result,
                backend_name=backend_name,
                failover_used=index > 0,
                attempt_trace=tuple(attempts),
            )
            if result.status == "succeeded" or index == len(chain) - 1:
                break
        assert final_result is not None
        return ModelInvocationChainResult(
            final_result=final_result,
            attempts=tuple(attempts),
            fallback_used=final_result.failover_used,
        )

    def _build_request(
        self,
        run_id: str,
        coalition: Coalition,
        artifact: ObservationArtifact,
        backend_name: str,
        backend_config: ModelBackendConfig,
    ) -> ModelInvocationRequest:
        observation_payload = asdict(artifact.observation)
        system_prompt = _SYSTEM_PROMPTS.get(
            backend_config.system_prompt_variant or "phase1_default",
            _SYSTEM_PROMPTS["phase1_default"],
        )
        user_payload = {
            "action_schema_version": ACTION_SCHEMA_VERSION,
            "allowed_action_types": [action_type.value for action_type in ActionType],
            "observation": observation_payload,
            "response_contract": {
                "format": "json",
                "shape": "object_with_actions_array",
                "required_action_fields": ["action_id", "action_type", "reason"],
            },
        }
        request_payload: dict[str, Any] = {
            "model": backend_config.model,
            "temperature": backend_config.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": self._build_user_content(artifact, user_payload, backend_config.multimodal),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "phase1_action_batch",
                    "schema": self._action_batch_json_schema(),
                },
            },
        }
        if backend_config.max_output_tokens is not None:
            request_payload["max_tokens"] = backend_config.max_output_tokens
        return ModelInvocationRequest(
            run_id=run_id,
            coalition=coalition,
            decision_cycle=artifact.decision_cycle,
            backend_name=backend_name,
            observation_id=artifact.id,
            system_prompt=system_prompt,
            user_payload=user_payload,
            request_payload=request_payload,
        )

    def _build_user_content(
        self,
        artifact: ObservationArtifact,
        user_payload: dict[str, Any],
        allow_multimodal: bool,
    ) -> str | list[dict[str, Any]]:
        text_payload = json.dumps(user_payload, sort_keys=True, default=str)
        if not allow_multimodal:
            return text_payload
        image_parts = []
        for attachment in artifact.attachment_artifacts:
            if not attachment.submitted_to_backend:
                continue
            image_parts.append({"type": "image_url", "image_url": {"url": self._attachment_data_url(attachment.file_path, attachment.media_type)}})
        if not image_parts:
            return text_payload
        return [{"type": "text", "text": text_payload}, *image_parts]

    @staticmethod
    def _attachment_data_url(file_path: str, media_type: str) -> str:
        payload = Path(file_path).read_bytes()
        return f"data:{media_type};base64,{base64.b64encode(payload).decode('ascii')}"

    def _multimodal_submission_map(self) -> dict[Coalition, bool]:
        return {
            coalition: self.model_registry.capability_for(self.model_registry.resolve_backend_name(coalition)).supports_multimodal
            for coalition in Coalition
        }

    def _classify_cycle(self, red_result: ModelInvocationResult, blue_result: ModelInvocationResult) -> str:
        red_ok = red_result.status == "succeeded"
        blue_ok = blue_result.status == "succeeded"
        if red_ok and blue_ok:
            return "both_succeeded"
        if red_ok or blue_ok:
            return "one_succeeded_one_failed"
        return "both_failed"

    def _action_batch_json_schema(self) -> dict[str, Any]:
        action_types = [action_type.value for action_type in ActionType]
        action_properties: dict[str, Any] = {
            "action_id": {"type": "string"},
            "action_type": {"type": "string", "enum": action_types},
            "reason": {"type": "string"},
            "params": {"type": "object"},
            "priority": {"type": "string"},
            "sector_id": {"type": "string"},
            "target_sector_id": {"type": "string"},
            "role": {"type": "string"},
            "reserve_group_id": {"type": "string"},
            "group_id": {"type": "string"},
            "group_ids": {"type": "array", "items": {"type": "string"}},
            "control_point_id": {"type": "string"},
            "reinforcement_role": {"type": "string"},
            "destination_type": {"type": "string"},
            "destination_id": {"type": "string"},
            "fallback_destination_type": {"type": "string"},
            "fallback_destination_id": {"type": "string"},
            "posture": {"type": "string"},
            "scope": {"type": "string"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": action_properties,
                        "required": ["action_id", "action_type", "reason"],
                    },
                }
            },
            "required": ["actions"],
        }


def build_model_registry(
    config: AppConfig,
    *,
    routing: ModelRoutingConfig | None = None,
    run_scoped_backends: tuple[RunScopedBackendDefinition, ...] = (),
    transports: dict[str, httpx.BaseTransport] | None = None,
) -> ModelAdapterRegistry:
    adapters: dict[str, OpenAICompatibleAdapter] = {}
    capabilities: list[ModelBackendCapability] = []
    catalog_entries: list[ModelCatalogEntry] = []
    backend_configs = list(config.models)
    for scoped_backend in run_scoped_backends:
        backend_configs.append(
            ModelBackendConfig(
                name=scoped_backend.backend_name,
                hosting_mode=ModelHostingMode(scoped_backend.hosting_mode),
                endpoint=scoped_backend.endpoint,
                model=scoped_backend.model,
                enabled=True,
                multimodal=scoped_backend.multimodal,
                api_key_env_var=scoped_backend.api_key_env_var,
                timeout_sec=scoped_backend.timeout_sec,
                max_retries=scoped_backend.max_retries,
                temperature=scoped_backend.temperature,
                max_output_tokens=scoped_backend.max_output_tokens,
                system_prompt_variant=scoped_backend.system_prompt_variant,
            )
        )
        catalog_entries.append(
            ModelCatalogEntry(
                catalog_id=scoped_backend.backend_name,
                display_name=scoped_backend.display_name,
                backend_name=scoped_backend.backend_name,
                server_label=scoped_backend.source_label or "ad-hoc",
                endpoint=scoped_backend.endpoint,
                model=scoped_backend.model,
                hosting_mode=scoped_backend.hosting_mode,
                supports_structured_output=True,
                supports_multimodal=scoped_backend.multimodal,
                enabled=True,
                hidden=False,
                tags=("ad-hoc",),
            )
        )
    for backend in backend_configs:
        transport = transports.get(backend.name) if transports else None
        adapters[backend.name] = OpenAICompatibleAdapter(backend, transport=transport)
        capabilities.append(
            ModelBackendCapability(
                backend_name=backend.name,
                endpoint=backend.endpoint,
                hosting_mode=backend.hosting_mode.value,
                openai_compatible=True,
                supports_structured_output=True,
                supports_multimodal=backend.multimodal,
            )
        )
    configured_catalog = [
        ModelCatalogEntry(
            catalog_id=entry.id,
            display_name=entry.display_name,
            backend_name=entry.backend_name,
            server_label=entry.server_label,
            endpoint=adapters[entry.backend_name].config.endpoint,
            model=adapters[entry.backend_name].config.model,
            hosting_mode=adapters[entry.backend_name].config.hosting_mode.value,
            supports_structured_output=True,
            supports_multimodal=adapters[entry.backend_name].config.multimodal,
            enabled=entry.enabled,
            hidden=entry.hidden,
            tags=entry.tags,
        )
        for entry in config.model_catalog
        if entry.backend_name in adapters
    ]
    catalog_entries = configured_catalog + catalog_entries
    return ModelAdapterRegistry(
        backends=adapters,
        routing=routing or config.model_routing,
        capabilities=tuple(capabilities),
        catalog=tuple(catalog_entries),
    )
