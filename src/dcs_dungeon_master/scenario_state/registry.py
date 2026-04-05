"""Scenario registry and authored scenario loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from dcs_dungeon_master.core.enums import Coalition, GroupPosture
from dcs_dungeon_master.core.exceptions import ConfigError, ScenarioNotFoundError
from dcs_dungeon_master.core.models import (
    ActiveGroupState,
    CoalitionState,
    ControlPointState,
    DeploymentRestrictionState,
    ReserveGroupState,
    ScenarioDefinition,
    ScenarioZone,
    SectorState,
    StandingOrderState,
)


DEFAULT_SCENARIO_INDEX_PATH = Path("scenarios/index.toml")


@dataclass(slots=True, frozen=True)
class ScenarioRegistryEntry:
    id: str
    name: str
    theater: str
    path: Path
    summary: str


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Required TOML file does not exist: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"TOML root must be a table: {path}")
    return data


def _require_non_empty_string(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}: field '{key}' is required and must be a non-empty string.")
    return value


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Optional field '{key}' must be a non-empty string when provided.")
    return value


def _optional_string_list(data: dict[str, Any], key: str, context: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigError(f"{context}: field '{key}' must be an array of non-empty strings.")
    return tuple(value)


def _optional_float(data: dict[str, Any], key: str, context: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise ConfigError(f"{context}: field '{key}' must be numeric when provided.")
    return float(value)


def load_registry(index_path: str | Path = DEFAULT_SCENARIO_INDEX_PATH) -> tuple[ScenarioRegistryEntry, ...]:
    path = Path(index_path)
    raw = _load_toml(path)
    entries_raw = raw.get("scenarios", [])
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ConfigError(f"Scenario registry '{path}' must define a non-empty 'scenarios' array.")

    registry_entries: list[ScenarioRegistryEntry] = []
    for index, entry_raw in enumerate(entries_raw):
        if not isinstance(entry_raw, dict):
            raise ConfigError(f"Scenario registry entry {index} must be a table.")
        relative_path = _require_non_empty_string(entry_raw, "path", f"Scenario registry entry {index}")
        registry_entries.append(
            ScenarioRegistryEntry(
                id=_require_non_empty_string(entry_raw, "id", f"Scenario registry entry {index}"),
                name=_require_non_empty_string(entry_raw, "name", f"Scenario registry entry {index}"),
                theater=_require_non_empty_string(entry_raw, "theater", f"Scenario registry entry {index}"),
                path=(path.parent / relative_path).resolve(),
                summary=_require_non_empty_string(entry_raw, "summary", f"Scenario registry entry {index}"),
            )
        )
    return tuple(registry_entries)


def list_scenarios(index_path: str | Path = DEFAULT_SCENARIO_INDEX_PATH) -> tuple[ScenarioRegistryEntry, ...]:
    return load_registry(index_path)


def get_scenario_definition(
    scenario_id: str,
    index_path: str | Path = DEFAULT_SCENARIO_INDEX_PATH,
) -> ScenarioDefinition:
    entries = load_registry(index_path)
    try:
        entry = next(item for item in entries if item.id == scenario_id)
    except StopIteration as exc:
        raise ScenarioNotFoundError(f"Unknown scenario id: {scenario_id}") from exc
    return load_scenario_definition(entry.path)


def load_scenario_definition(path: str | Path) -> ScenarioDefinition:
    raw = _load_toml(Path(path))
    context = f"Scenario definition '{path}'"

    sectors_raw = raw.get("sectors", [])
    control_points_raw = raw.get("control_points", [])
    coalitions_raw = raw.get("coalitions", [])
    active_groups_raw = raw.get("active_groups", [])
    reserve_groups_raw = raw.get("reserve_groups", [])
    zones_raw = raw.get("zones", [])
    restrictions_raw = raw.get("deployment_restrictions", [])

    if not sectors_raw or not isinstance(sectors_raw, list):
        raise ConfigError(f"{context}: 'sectors' must be a non-empty array of tables.")
    if not control_points_raw or not isinstance(control_points_raw, list):
        raise ConfigError(f"{context}: 'control_points' must be a non-empty array of tables.")
    if not coalitions_raw or not isinstance(coalitions_raw, list):
        raise ConfigError(f"{context}: 'coalitions' must be a non-empty array of tables.")

    sectors = tuple(_parse_sector(item, context) for item in sectors_raw)
    control_points = tuple(_parse_control_point(item, context) for item in control_points_raw)
    coalitions = tuple(_parse_coalition_state(item, context) for item in coalitions_raw)
    active_groups = tuple(_parse_active_group(item, context) for item in active_groups_raw)
    reserve_groups = tuple(_parse_reserve_group(item, context) for item in reserve_groups_raw)
    zones = tuple(_parse_zone(item, context) for item in zones_raw)
    restrictions = tuple(_parse_restriction(item, context) for item in restrictions_raw)

    sector_ids = {sector.id for sector in sectors}
    coalition_ids = {coalition.coalition for coalition in coalitions}

    for sector in sectors:
        unknown_neighbors = [neighbor_id for neighbor_id in sector.neighbor_ids if neighbor_id not in sector_ids]
        if unknown_neighbors:
            raise ConfigError(
                f"{context}: sector '{sector.id}' references unknown neighbors: {', '.join(unknown_neighbors)}."
            )

    for control_point in control_points:
        if control_point.sector_id not in sector_ids:
            raise ConfigError(
                f"{context}: control point '{control_point.id}' references unknown sector '{control_point.sector_id}'."
            )

    for group in active_groups:
        if group.sector_id is not None and group.sector_id not in sector_ids:
            raise ConfigError(
                f"{context}: active group '{group.id}' references unknown sector '{group.sector_id}'."
            )
        if group.coalition not in coalition_ids:
            raise ConfigError(f"{context}: active group '{group.id}' references unknown coalition '{group.coalition}'.")

    for reserve_group in reserve_groups:
        if reserve_group.coalition not in coalition_ids:
            raise ConfigError(
                f"{context}: reserve group '{reserve_group.id}' references unknown coalition '{reserve_group.coalition}'."
            )
        unknown_allowed = [sector_id for sector_id in reserve_group.allowed_sector_ids if sector_id not in sector_ids]
        if unknown_allowed:
            raise ConfigError(
                f"{context}: reserve group '{reserve_group.id}' references unknown allowed sectors: "
                f"{', '.join(unknown_allowed)}."
            )

    for zone in zones:
        if zone.sector_id not in sector_ids:
            raise ConfigError(f"{context}: zone '{zone.id}' references unknown sector '{zone.sector_id}'.")

    return ScenarioDefinition(
        id=_require_non_empty_string(raw, "id", context),
        version=_require_non_empty_string(raw, "version", context),
        name=_require_non_empty_string(raw, "name", context),
        theater=_require_non_empty_string(raw, "theater", context),
        summary=_require_non_empty_string(raw, "summary", context),
        sectors=sectors,
        control_points=control_points,
        coalitions=coalitions,
        active_groups=active_groups,
        reserve_groups=reserve_groups,
        zones=zones,
        deployment_restrictions=restrictions,
    )


def _parse_sector(data: dict[str, Any], context: str) -> SectorState:
    if not isinstance(data, dict):
        raise ConfigError(f"{context}: sector entries must be tables.")
    return SectorState(
        id=_require_non_empty_string(data, "id", context),
        name=_require_non_empty_string(data, "name", context),
        role=_require_non_empty_string(data, "role", context),
        neighbor_ids=_optional_string_list(data, "neighbor_ids", context),
        tags=_optional_string_list(data, "tags", context),
        center_lat=_optional_float(data, "center_lat", context),
        center_lng=_optional_float(data, "center_lng", context),
        radius_nm=float(data.get("radius_nm", 35.0)),
    )


def _parse_control_point(data: dict[str, Any], context: str) -> ControlPointState:
    if not isinstance(data, dict):
        raise ConfigError(f"{context}: control point entries must be tables.")
    owner_raw = _optional_string(data, "owner")
    owner = Coalition(owner_raw) if owner_raw is not None else None
    return ControlPointState(
        id=_require_non_empty_string(data, "id", context),
        name=_require_non_empty_string(data, "name", context),
        sector_id=_require_non_empty_string(data, "sector_id", context),
        kind=_require_non_empty_string(data, "kind", context),
        owner=owner,
        strategic_value=_require_non_empty_string(data, "strategic_value", context),
        lat=_optional_float(data, "lat", context),
        lng=_optional_float(data, "lng", context),
    )


def _parse_coalition_state(data: dict[str, Any], context: str) -> CoalitionState:
    if not isinstance(data, dict):
        raise ConfigError(f"{context}: coalition entries must be tables.")
    coalition = Coalition(_require_non_empty_string(data, "coalition", context))
    standing_orders_raw = data.get("standing_orders", [])
    if not isinstance(standing_orders_raw, list):
        raise ConfigError(f"{context}: coalition standing orders must be an array.")
    standing_orders = tuple(
        StandingOrderState(
            id=_require_non_empty_string(item, "id", context),
            coalition=coalition,
            text=_require_non_empty_string(item, "text", context),
            active=bool(item.get("active", True)),
        )
        for item in standing_orders_raw
        if isinstance(item, dict)
    )
    if len(standing_orders) != len(standing_orders_raw):
        raise ConfigError(f"{context}: coalition standing orders must be tables.")
    objectives = _optional_string_list(data, "objectives", context)
    budget_remaining = data.get("budget_remaining")
    if not isinstance(budget_remaining, int):
        raise ConfigError(f"{context}: coalition 'budget_remaining' must be an integer.")
    attrition_pool = data.get("attrition_pool", 0)
    replacement_pool = data.get("replacement_pool", 0)
    if not isinstance(attrition_pool, int) or not isinstance(replacement_pool, int):
        raise ConfigError(f"{context}: coalition attrition/replacement pools must be integers.")
    return CoalitionState(
        coalition=coalition,
        objectives=objectives,
        budget_remaining=budget_remaining,
        reserve_ids=_optional_string_list(data, "reserve_ids", context),
        standing_orders=standing_orders,
        attrition_pool=attrition_pool,
        replacement_pool=replacement_pool,
    )


def _parse_active_group(data: dict[str, Any], context: str) -> ActiveGroupState:
    if not isinstance(data, dict):
        raise ConfigError(f"{context}: active group entries must be tables.")
    return ActiveGroupState(
        id=_require_non_empty_string(data, "id", context),
        coalition=Coalition(_require_non_empty_string(data, "coalition", context)),
        group_type=_require_non_empty_string(data, "group_type", context),
        posture=GroupPosture(_require_non_empty_string(data, "posture", context)),
        sector_id=_optional_string(data, "sector_id"),
        mobile=bool(data.get("mobile", True)),
        status=_require_non_empty_string(data, "status", context),
        control_point_id=_optional_string(data, "control_point_id"),
        attrition_count=int(data.get("attrition_count", 0)),
        replacement_pool=int(data.get("replacement_pool", 0)),
    )


def _parse_reserve_group(data: dict[str, Any], context: str) -> ReserveGroupState:
    if not isinstance(data, dict):
        raise ConfigError(f"{context}: reserve group entries must be tables.")
    cost = data.get("cost")
    if not isinstance(cost, int):
        raise ConfigError(f"{context}: reserve group 'cost' must be an integer.")
    return ReserveGroupState(
        id=_require_non_empty_string(data, "id", context),
        coalition=Coalition(_require_non_empty_string(data, "coalition", context)),
        group_type=_require_non_empty_string(data, "group_type", context),
        available=bool(data.get("available", True)),
        cost=cost,
        allowed_sector_ids=_optional_string_list(data, "allowed_sector_ids", context),
        status=_require_non_empty_string(data, "status", context),
        emergency=bool(data.get("emergency", False)),
        attrition_count=int(data.get("attrition_count", 0)),
        replacement_pool=int(data.get("replacement_pool", 0)),
    )


def _parse_restriction(data: dict[str, Any], context: str) -> DeploymentRestrictionState:
    if not isinstance(data, dict):
        raise ConfigError(f"{context}: restriction entries must be tables.")
    coalition_raw = _optional_string(data, "coalition")
    coalition = Coalition(coalition_raw) if coalition_raw is not None else None
    return DeploymentRestrictionState(
        id=_require_non_empty_string(data, "id", context),
        coalition=coalition,
        restriction_type=_require_non_empty_string(data, "restriction_type", context),
        description=_require_non_empty_string(data, "description", context),
        sector_ids=_optional_string_list(data, "sector_ids", context),
        control_point_ids=_optional_string_list(data, "control_point_ids", context),
        adjacency_limited=bool(data.get("adjacency_limited", False)),
    )


def _parse_zone(data: dict[str, Any], context: str) -> ScenarioZone:
    if not isinstance(data, dict):
        raise ConfigError(f"{context}: zone entries must be tables.")
    center_lat = data.get("center_lat")
    center_lng = data.get("center_lng")
    radius_nm = data.get("radius_nm")
    if not isinstance(center_lat, int | float) or not isinstance(center_lng, int | float):
        raise ConfigError(f"{context}: zone center coordinates must be numeric.")
    if not isinstance(radius_nm, int | float) or float(radius_nm) <= 0:
        raise ConfigError(f"{context}: zone 'radius_nm' must be a positive number.")
    return ScenarioZone(
        id=_require_non_empty_string(data, "id", context),
        name=_require_non_empty_string(data, "name", context),
        sector_id=_require_non_empty_string(data, "sector_id", context),
        center_lat=float(center_lat),
        center_lng=float(center_lng),
        radius_nm=float(radius_nm),
        tags=_optional_string_list(data, "tags", context),
    )
