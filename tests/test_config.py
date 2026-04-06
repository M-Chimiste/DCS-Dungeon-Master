from pathlib import Path

import pytest

from dcs_dungeon_master.core.config import load_config
from dcs_dungeon_master.core.enums import EvaluationReviewPolicy, FairnessMode, LoggingFormat, ModelHostingMode
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
    assert config.model_routing.red_backend == "local_default"
    assert config.model_routing.blue_backend == "local_default"
    assert config.model_catalog[0].id == "local_gemma"
    assert config.model_routing.default_catalog_id == "local_gemma"
    assert config.fog_of_war.inference_mode.value == "conservative"
    assert config.evaluation.profile_name == "phase1_baseline"
    assert config.evaluation.review_policy is EvaluationReviewPolicy.SUSPICIOUS_ONLY
    assert config.evaluation.fairness_mode is FairnessMode.HYBRID
    assert config.evaluation.matrix_cases


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

[[models]]
name = "remote_lmstudio"
hosting_mode = "hosted"
endpoint = "https://api.example.test/v1"
model = "gemma-4-26b-a4b-it"
enabled = true
api_key_env_var = "REMOTE_MODEL_API_KEY"
timeout_sec = 45.0
max_retries = 1
temperature = 0.1
max_output_tokens = 900

[model_routing]
red_backend = "remote_lmstudio"
blue_backend = "remote_lmstudio"

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
    assert config.models[0].hosting_mode is ModelHostingMode.HOSTED
    assert config.models[0].api_key_env_var == "REMOTE_MODEL_API_KEY"
    assert config.models[0].timeout_sec == 45.0
    assert config.models[0].max_output_tokens == 900
    assert config.model_routing.red_backend == "remote_lmstudio"
    assert config.fog_of_war.freshness_window_sec == 300


def test_load_config_requires_model_routing_and_known_backends(tmp_path: Path) -> None:
    config_path = tmp_path / "broken-routing.toml"
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
name = "local_default"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma-4-26b-a4b-it"
enabled = true

[model_routing]
red_backend = "missing_backend"
blue_backend = "local_default"

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

    with pytest.raises(ConfigError, match="model_routing.red_backend"):
        load_config(config_path)


def test_load_config_parses_evaluation_matrix_cases(tmp_path: Path) -> None:
    config_path = tmp_path / "evaluation.toml"
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
db_path = "/tmp/dcs.sqlite3"
enable_wal = true

[dry_run]
enabled = true
summary_output = "text"

[evaluation]
profile_name = "phase1_eval"
decision_cadence_sec = 45
prompt_version = "prompt_v2"
resource_settings_version = "resources_v1"
review_policy = "suspicious_only"
fairness_mode = "hybrid"
default_run_cycles = 12

[[evaluation.matrix_cases]]
id = "visual_case"
description = "Visual test case"
mode = "dry"
red_backend = "local_default"
blue_backend = "local_default"
decision_cadence_sec = 60
run_cycles = 2
repeat_count = 1
visual_attachment = true
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.evaluation.profile_name == "phase1_eval"
    assert config.evaluation.decision_cadence_sec == 45
    assert config.evaluation.matrix_cases[0].visual_attachment is True


def test_load_config_supports_shared_default_catalog_routing(tmp_path: Path) -> None:
    config_path = tmp_path / "shared.toml"
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
name = "local_default"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma"
enabled = true

[[models]]
name = "hosted_default"
hosting_mode = "hosted"
endpoint = "https://api.example.test/v1"
model = "gpt-5"
enabled = true

[[model_catalog]]
id = "local_gemma"
display_name = "Local Gemma"
backend_name = "local_default"
server_label = "LM Studio"

[[model_catalog]]
id = "hosted_gpt5"
display_name = "Hosted GPT-5"
backend_name = "hosted_default"
server_label = "OpenAI"

[model_routing]
default_backend = "local_gemma"
default_fallback_backend = "hosted_gpt5"
blue_backend_override = "hosted_gpt5"

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

    assert config.model_routing.red_backend == "local_default"
    assert config.model_routing.blue_backend == "hosted_default"
    assert config.model_routing.default_catalog_id == "local_gemma"
    assert config.model_routing.blue_catalog_override == "hosted_gpt5"


def test_load_config_rejects_unknown_model_catalog_backend(tmp_path: Path) -> None:
    config_path = tmp_path / "bad-catalog.toml"
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
name = "local_default"
hosting_mode = "local"
endpoint = "http://127.0.0.1:1234/v1"
model = "gemma"
enabled = true

[[model_catalog]]
id = "bad_entry"
display_name = "Broken"
backend_name = "missing_backend"
server_label = "Broken"

[model_routing]
red_backend = "local_default"
blue_backend = "local_default"

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

    with pytest.raises(ConfigError, match="model_catalog\\[0\\]\\.backend_name"):
        load_config(config_path)
