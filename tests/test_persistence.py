from pathlib import Path

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
