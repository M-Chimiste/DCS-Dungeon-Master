from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx

from dcs_dungeon_master.action_validation import ActionValidator
from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import Coalition, RunLifecycleStatus
from dcs_dungeon_master.core.models import ModelInvocationRequest, RunScopedBackendDefinition
from dcs_dungeon_master.model_adapter import DryDecisionLoopRunner, OpenAICompatibleAdapter, build_model_registry
from dcs_dungeon_master.observation import ObservationBuilder
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition
from dcs_dungeon_master.sensor_fusion import SensorFusionService


def _write_temp_config(tmp_path: Path, *, include_fallback: bool = False) -> Path:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state.sqlite3"
    fallback_models = (
        """

[[models]]
name = "fallback_backend"
hosting_mode = "hosted"
endpoint = "https://api.example.test/v1"
model = "gpt-5"
enabled = true
max_retries = 1
""".strip()
        if include_fallback
        else ""
    )
    fallback_routing = (
        """
red_fallback_backend = "fallback_backend"
blue_fallback_backend = "fallback_backend"
""".strip()
        if include_fallback
        else ""
    )
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
name = "red_backend"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true
max_retries = 1

[[models]]
name = "blue_backend"
hosting_mode = "lan"
endpoint = "http://192.168.1.22:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true
max_retries = 1

{fallback_models}

[model_routing]
red_backend = "red_backend"
blue_backend = "blue_backend"
{fallback_routing}

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


def _build_runner(tmp_path: Path, transports: dict[str, httpx.BaseTransport], *, include_fallback: bool = False):
    config = load_config(_write_temp_config(tmp_path, include_fallback=include_fallback))
    scenario = get_scenario_definition(config.scenario.id, config.scenario.registry_path)
    store = SQLiteStateStore(config.persistence.db_path)
    run_id = store.create_run_from_scenario(scenario)
    observation_builder = ObservationBuilder(store, scenario, SensorFusionService(store, scenario, config.fog_of_war), config.multimodal)
    validator = ActionValidator(store, scenario)
    registry = build_model_registry(config, transports=transports)
    runner = DryDecisionLoopRunner(store, observation_builder, validator, registry)
    return store, run_id, runner


def test_openai_compatible_adapter_round_trip_and_retry_on_parse_failure() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if request.url.path.endswith("/chat/completions") and calls["count"] == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"actions":[{"action_id":"act_1","action_type":"hold_action","reason":"Hold","scope":"global"}]}'}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
                },
            )
        return httpx.Response(200, json={"data": [{"id": "gemma-4-26b-a4b-it"}]})

    config = load_config(Path("config/milestone0.toml"))
    backend = config.models[0]
    adapter = OpenAICompatibleAdapter(backend, transport=httpx.MockTransport(handler))
    request = ModelInvocationRequest(
        run_id="run-1",
        coalition=Coalition.RED,
        decision_cycle=1,
        backend_name=backend.name,
        observation_id=1,
        system_prompt="Return JSON only.",
        user_payload={"observation": {"meta": {"coalition": "red"}}},
        request_payload={"model": backend.model, "messages": [{"role": "user", "content": "{}"}]},
    )

    result = adapter.invoke(request)

    assert result.status == "succeeded"
    assert result.attempt_count == 2
    assert result.parsed_actions[0]["action_type"] == "hold_action"
    assert result.total_tokens == 19


def test_model_registry_exposes_hosted_capabilities_and_auth(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
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
remote_hosted = false

[dcs.olympus]
base_url = "http://127.0.0.1:4512"
timeout_sec = 5.0

[dcs.grpc]
host = "127.0.0.1"
port = 50051
timeout_sec = 5.0

[[models]]
name = "red_backend"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true

[[models]]
name = "blue_backend"
hosting_mode = "hosted"
endpoint = "https://api.example.test/v1"
model = "gemma-4-26b-a4b-it"
enabled = true
api_key_env_var = "HOSTED_API_KEY"

[model_routing]
red_backend = "red_backend"
blue_backend = "blue_backend"

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
    monkeypatch.setenv("HOSTED_API_KEY", "secret-token")
    config = load_config(config_path)
    registry = build_model_registry(config)

    capabilities = {item.backend_name: item for item in registry.describe_backends()}
    hosted_adapter = registry.backends["blue_backend"]

    assert capabilities["blue_backend"].hosting_mode == "hosted"
    assert capabilities["blue_backend"].supports_structured_output is True
    assert hosted_adapter._client.headers["Authorization"] == "Bearer secret-token"
    registry.close()


def test_dry_decision_cycle_persists_invocations_and_validation_results(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            payload = json.loads(request.content.decode("utf-8"))
            is_red = '"coalition": "red"' in payload["messages"][1]["content"]
            response_content = (
                '{"actions":[{"action_id":"red_hold","action_type":"hold_action","reason":"Maintain plan","scope":"global"}]}'
                if is_red
                else '{"actions":[{"action_id":"blue_hold","action_type":"hold_action","reason":"Maintain plan","scope":"global"}]}'
            )
            return httpx.Response(200, json={"choices": [{"message": {"content": response_content}}]})
        return httpx.Response(200, json={"data": [{"id": "gemma-4-26b-a4b-it"}]})

    transports = {
        "red_backend": httpx.MockTransport(handler),
        "blue_backend": httpx.MockTransport(handler),
    }
    store, run_id, runner = _build_runner(tmp_path, transports)

    cycle = runner.run_decision_cycle(
        run_id,
        1,
        now=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
    )

    invocations = store.list_model_invocations(run_id)
    decisions = store.list_decision_cycles(run_id)
    red_validation = store.get_latest_action_validation_batch(run_id, Coalition.RED)
    blue_validation = store.get_latest_action_validation_batch(run_id, Coalition.BLUE)

    assert cycle.id is not None
    assert len(invocations) == 2
    assert len(decisions) == 1
    assert invocations[0].decision_cycle == 1
    assert invocations[1].decision_cycle == 1
    assert red_validation.results[0].action_id == "red_hold"
    assert blue_validation.results[0].action_id == "blue_hold"
    assert store.get_run_summary(run_id)["model_invocation_count"] == 2
    assert store.get_run_summary(run_id)["decision_cycle_count"] == 1
    assert decisions[0].classification == "both_succeeded"
    assert invocations[0].request_payload["response_format"]["json_schema"]["schema"]["properties"]["actions"]["items"]["required"] == [
        "action_id",
        "action_type",
        "reason",
    ]


def test_build_model_registry_merges_run_scoped_backends(tmp_path: Path) -> None:
    config = load_config(_write_temp_config(tmp_path))

    registry = build_model_registry(
        config,
        run_scoped_backends=(
            RunScopedBackendDefinition(
                backend_name="adhoc_blue",
                display_name="Ad-hoc Blue",
                endpoint="https://api.example.test/v1",
                model="gpt-5",
                hosting_mode="hosted",
            ),
        ),
    )

    try:
        assert "adhoc_blue" in registry.backends
        assert any(item.catalog_id == "adhoc_blue" for item in registry.catalog)
    finally:
        registry.close()


def test_dry_decision_cycle_continues_other_coalition_when_one_parse_fails(tmp_path: Path) -> None:
    def red_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"actions":[{"action_id":"red_hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'}}]},
            )
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    def blue_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "no-json-here"}}]})
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    store, run_id, runner = _build_runner(
        tmp_path,
        {
            "red_backend": httpx.MockTransport(red_handler),
            "blue_backend": httpx.MockTransport(blue_handler),
        },
    )

    cycle = runner.run_decision_cycle(run_id, 2)
    invocations = {item.coalition: item for item in store.list_model_invocations(run_id)}

    assert cycle.id is not None
    assert invocations[Coalition.RED].status == "succeeded"
    assert invocations[Coalition.BLUE].status == "parse_failed"
    assert invocations[Coalition.BLUE].validation_batch_id is None
    assert store.get_latest_action_validation_batch(run_id, Coalition.RED).results[0].action_id == "red_hold"
    assert cycle.classification == "one_succeeded_one_failed"


def test_model_failover_uses_secondary_backend_after_primary_parse_failure(tmp_path: Path) -> None:
    def red_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    def blue_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"actions":[{"action_id":"blue_hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'}}]},
            )
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    store, run_id, runner = _build_runner(
        tmp_path,
        {
            "red_backend": httpx.MockTransport(red_handler),
            "blue_backend": httpx.MockTransport(blue_handler),
            "fallback_backend": httpx.MockTransport(blue_handler),
        },
        include_fallback=True,
    )

    result = runner.run_decision_cycle(run_id, 3)
    invocations = {item.coalition: item for item in store.list_model_invocations(run_id)}

    assert result.classification == "both_succeeded"
    assert invocations[Coalition.RED].backend_name == "fallback_backend"
    assert invocations[Coalition.RED].failover_used is True
    assert len(invocations[Coalition.RED].attempt_trace) == 2
    assert invocations[Coalition.RED].attempt_trace[0].backend_name == "red_backend"
    assert invocations[Coalition.RED].attempt_trace[1].backend_name == "fallback_backend"


def test_dry_decision_loop_stops_cleanly_when_run_is_paused(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"actions":[{"action_id":"hold","action_type":"hold_action","reason":"Maintain","scope":"global"}]}'}}]},
            )
        return httpx.Response(200, json={"data": [{"id": "gemma"}]})

    store, run_id, runner = _build_runner(
        tmp_path,
        {
            "red_backend": httpx.MockTransport(handler),
            "blue_backend": httpx.MockTransport(handler),
        },
    )
    store.update_run_status(run_id, RunLifecycleStatus.RUNNING, changed_at=datetime.now(UTC))
    first = runner.run_decision_cycle(run_id, 1)
    assert first.id is not None
    store.update_run_status(run_id, RunLifecycleStatus.PAUSED, changed_at=datetime.now(UTC))

    loop = runner.run_decision_loop(run_id, start_decision_cycle=2, cycles=2)

    assert loop.completed_cycles == 0
    assert loop.stopped_early is True
    assert loop.terminal_run_status is RunLifecycleStatus.PAUSED
