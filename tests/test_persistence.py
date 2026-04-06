import sqlite3
from pathlib import Path

from dcs_dungeon_master.core.enums import Coalition
from dcs_dungeon_master.core.models import ResolvedCoalitionRouting, ResolvedRunRouting, RunScopedBackendDefinition
from dcs_dungeon_master.persistence import SQLiteStateStore
from dcs_dungeon_master.scenario_state.registry import get_scenario_definition


def test_sqlite_initialization_and_event_log(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite3"
    store = SQLiteStateStore(db_path)
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")

    run_id = store.create_run_from_scenario(scenario)
    events = store.list_events(run_id)
    summary = store.get_run_summary(run_id)

    assert db_path.exists()
    assert events
    assert events[-1].event_type == "scenario_initialized"
    assert summary["active_group_count"] == 10
    assert summary["reserve_group_count"] == 6
    assert summary["status"] == "created"
    assert summary["mode"] == "dry"
    assert summary["coalitions"]["red"]["budget_remaining"] == 24
    assert summary["coalitions"]["blue"]["budget_remaining"] == 24


def test_coalition_state_query_returns_materialized_state(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite3"
    store = SQLiteStateStore(db_path)
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")

    run_id = store.create_run_from_scenario(scenario)
    coalition_states = store.get_coalition_states(run_id)

    assert len(coalition_states) == 2
    assert {state.coalition.value for state in coalition_states} == {"red", "blue"}


def test_run_routing_round_trips_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite3"
    store = SQLiteStateStore(db_path)
    scenario = get_scenario_definition("phase1_baseline_persian_gulf", "scenarios/index.toml")
    routing = ResolvedRunRouting(
        red=ResolvedCoalitionRouting(
            coalition=Coalition.RED,
            primary_backend_name="local_default",
            fallback_backend_name="hosted_default",
            primary_catalog_id="local_gemma",
            fallback_catalog_id="hosted_gpt5",
            primary_source="catalog",
            fallback_source="catalog",
        ),
        blue=ResolvedCoalitionRouting(
            coalition=Coalition.BLUE,
            primary_backend_name="blue_override",
            fallback_backend_name=None,
            primary_catalog_id="blue_special",
            fallback_catalog_id=None,
            primary_source="catalog",
            fallback_source=None,
        ),
        shared_primary_catalog_id="local_gemma",
        shared_fallback_catalog_id="hosted_gpt5",
        same_primary_for_both=False,
        same_fallback_for_both=False,
    )
    scoped = (
        RunScopedBackendDefinition(
            backend_name="adhoc_blue_primary_1",
            display_name="Ad-hoc Blue",
            endpoint="https://api.example.test/v1",
            model="gpt-5",
            hosting_mode="hosted",
        ),
    )

    run_id = store.create_run_from_scenario(
        scenario,
        red_backend_name=routing.red.primary_backend_name,
        blue_backend_name=routing.blue.primary_backend_name,
        routing=routing,
        run_scoped_backends=scoped,
    )
    loaded = store.get_run_control_state(run_id)

    assert loaded.routing is not None
    assert loaded.routing.red.primary_backend_name == "local_default"
    assert loaded.routing.blue.primary_catalog_id == "blue_special"
    assert loaded.routing.is_symmetric is False
    assert loaded.run_scoped_backends[0].backend_name == "adhoc_blue_primary_1"
    assert loaded.run_scoped_backends[0].model == "gpt-5"


def test_initialize_schema_migrates_legacy_tables_forward(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                scenario_version TEXT NOT NULL,
                scenario_name TEXT NOT NULL,
                theater TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE model_invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                coalition TEXT NOT NULL,
                decision_cycle INTEGER NOT NULL,
                backend_name TEXT NOT NULL,
                observation_id INTEGER,
                requested_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                request_payload_json TEXT NOT NULL,
                raw_response TEXT,
                parsed_actions_json TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                error_detail TEXT,
                latency_ms INTEGER,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                validation_batch_id INTEGER
            )
            """
        )
        connection.commit()

    store = SQLiteStateStore(db_path)
    store.initialize_schema()

    with sqlite3.connect(db_path) as connection:
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
        invocation_columns = {row[1] for row in connection.execute("PRAGMA table_info(model_invocations)").fetchall()}

    assert "status" in run_columns
    assert "routing_json" in run_columns
    assert "run_scoped_backends_json" in run_columns
    assert "evaluation_metadata_json" in run_columns
    assert "failover_used" in invocation_columns
    assert "attempt_trace_json" in invocation_columns
