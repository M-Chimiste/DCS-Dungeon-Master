"""Read-only pydcs theater reference helpers with authored fallback."""

from __future__ import annotations

from dataclasses import dataclass, replace
import importlib
from typing import Any

from dcs_dungeon_master.core.models import MapReferenceLayer, ScenarioDefinition


@dataclass(slots=True)
class PydcsReferenceService:
    """Provide read-only theater reference data when available."""

    def reference_for_scenario(self, scenario: ScenarioDefinition) -> MapReferenceLayer:
        default_center_lat, default_center_lng, default_radius_nm = self._default_view(scenario)
        airports: tuple[dict[str, Any], ...] = ()
        status = "unsupported"
        message = "Using authored fallback reference data only."

        try:
            terrain_module = importlib.import_module("dcs.terrain")
            module_path = getattr(terrain_module, "__file__", "") or ""
            if "/src/dcs/" in module_path.replace("\\", "/"):
                status = "partial"
                message = "pydcs terrain metadata is shadowed by the vendored grpc dcs package; using authored fallback."
            else:
                status = "partial"
                message = "pydcs terrain package detected, but this theater is using authored fallback layers in v1."
                airports = self._safe_airport_reference(terrain_module, scenario.theater)
        except Exception:  # noqa: BLE001
            status = "unsupported"
            message = "pydcs terrain metadata is unavailable; using authored fallback reference data."

        return MapReferenceLayer(
            theater_id=scenario.theater,
            reference_status=status,
            message=message,
            default_center_lat=default_center_lat,
            default_center_lng=default_center_lng,
            default_radius_nm=default_radius_nm,
            airports=airports,
            sectors=tuple(
                {
                    "id": sector.id,
                    "name": sector.name,
                    "role": sector.role,
                    "center_lat": sector.center_lat,
                    "center_lng": sector.center_lng,
                    "radius_nm": sector.radius_nm,
                    "neighbor_ids": sector.neighbor_ids,
                    "tags": sector.tags,
                }
                for sector in scenario.sectors
            ),
            control_points=tuple(
                {
                    "id": control_point.id,
                    "name": control_point.name,
                    "sector_id": control_point.sector_id,
                    "kind": control_point.kind,
                    "owner": control_point.owner.value if control_point.owner is not None else None,
                    "strategic_value": control_point.strategic_value,
                    "lat": control_point.lat,
                    "lng": control_point.lng,
                }
                for control_point in scenario.control_points
            ),
            zones=tuple(
                {
                    "id": zone.id,
                    "name": zone.name,
                    "sector_id": zone.sector_id,
                    "center_lat": zone.center_lat,
                    "center_lng": zone.center_lng,
                    "radius_nm": zone.radius_nm,
                    "tags": zone.tags,
                }
                for zone in scenario.zones
            ),
        )

    def reference_for_blank_scenario(self, scenario: ScenarioDefinition, fallback: ScenarioDefinition | None = None) -> MapReferenceLayer:
        if fallback is None:
            return self.reference_for_scenario(scenario)
        reference = self.reference_for_scenario(fallback)
        return replace(
            reference,
            theater_id=scenario.theater,
            sectors=(),
            control_points=(),
            zones=(),
        )

    def _default_view(self, scenario: ScenarioDefinition) -> tuple[float | None, float | None, float | None]:
        latitudes: list[float] = []
        longitudes: list[float] = []
        radii: list[float] = []
        for sector in scenario.sectors:
            if sector.center_lat is not None and sector.center_lng is not None:
                latitudes.append(sector.center_lat)
                longitudes.append(sector.center_lng)
                radii.append(sector.radius_nm)
        for control_point in scenario.control_points:
            if control_point.lat is not None and control_point.lng is not None:
                latitudes.append(control_point.lat)
                longitudes.append(control_point.lng)
        for zone in scenario.zones:
            latitudes.append(zone.center_lat)
            longitudes.append(zone.center_lng)
            radii.append(zone.radius_nm)
        if not latitudes or not longitudes:
            return (None, None, None)
        return (
            sum(latitudes) / len(latitudes),
            sum(longitudes) / len(longitudes),
            max(radii) if radii else 50.0,
        )

    def _safe_airport_reference(self, terrain_module: Any, theater_name: str) -> tuple[dict[str, Any], ...]:
        airport_list = getattr(terrain_module, "airport_list", None)
        if not callable(airport_list):
            return ()
        try:
            airports = airport_list()
        except Exception:  # noqa: BLE001
            return ()
        normalized = []
        for airport in airports:
            name = getattr(airport, "name", None)
            if not isinstance(name, str) or not name.strip():
                continue
            lat = getattr(airport, "latitude", None)
            lng = getattr(airport, "longitude", None)
            normalized.append(
                {
                    "name": name,
                    "theater": theater_name,
                    "lat": lat if isinstance(lat, int | float) else None,
                    "lng": lng if isinstance(lng, int | float) else None,
                }
            )
        return tuple(normalized)
