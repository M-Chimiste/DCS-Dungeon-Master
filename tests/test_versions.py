from dcs_dungeon_master.core.versions import ACTION_SCHEMA_VERSION, OBSERVATION_SCHEMA_VERSION


def test_schema_versions_are_defined() -> None:
    assert OBSERVATION_SCHEMA_VERSION == "0.1"
    assert ACTION_SCHEMA_VERSION == "0.1"
