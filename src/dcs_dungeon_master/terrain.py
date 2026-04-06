"""Terrain and route safety helpers backed by local elevation manifests."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from dcs_dungeon_master.core.config import AirOpsConfig
from dcs_dungeon_master.map_assets import TheaterAssetBundle


@dataclass(slots=True, frozen=True)
class TerrainSample:
    lat: float
    lng: float
    elevation_ft_msl: int


@dataclass(slots=True, frozen=True)
class TerrainSegmentAssessment:
    max_terrain_ft_msl: int
    safe_altitude_ft_msl: int
    sample_count: int


@dataclass(slots=True)
class TerrainService:
    config: AirOpsConfig

    def point_elevation_ft(self, bundle: TheaterAssetBundle | None, lat: float, lng: float) -> int | None:
        if bundle is None:
            return None
        grid = bundle.elevation.get("grid_ft")
        bounds = bundle.elevation.get("bounds")
        if not isinstance(grid, list) or not grid or not isinstance(bounds, dict):
            return None
        north = _float(bounds.get("north"))
        south = _float(bounds.get("south"))
        east = _float(bounds.get("east"))
        west = _float(bounds.get("west"))
        if None in {north, south, east, west}:
            return None
        if not (south <= lat <= north and west <= lng <= east):
            return None
        row_count = len(grid)
        col_count = len(grid[0]) if isinstance(grid[0], list) and grid[0] else 0
        if row_count < 1 or col_count < 1:
            return None
        lat_ratio = 0.0 if north == south else (north - lat) / (north - south)
        lng_ratio = 0.0 if east == west else (lng - west) / (east - west)
        row_index = min(row_count - 1, max(0, int(round(lat_ratio * (row_count - 1)))))
        col_index = min(col_count - 1, max(0, int(round(lng_ratio * (col_count - 1)))))
        value = grid[row_index][col_index]
        return int(value) if isinstance(value, int | float) else None

    def segment_assessment(
        self,
        bundle: TheaterAssetBundle | None,
        start_lat: float,
        start_lng: float,
        end_lat: float,
        end_lng: float,
        *,
        requested_altitude_ft_msl: int,
        aircraft_category: str,
    ) -> TerrainSegmentAssessment | None:
        samples = self._segment_samples(bundle, start_lat, start_lng, end_lat, end_lng)
        if not samples:
            return None
        max_terrain = max(sample.elevation_ft_msl for sample in samples)
        clearance = (
            self.config.helicopter_clearance_ft
            if aircraft_category.lower() in {"helicopter", "helo", "rotary"}
            else self.config.fixed_wing_clearance_ft
        )
        return TerrainSegmentAssessment(
            max_terrain_ft_msl=max_terrain,
            safe_altitude_ft_msl=max(requested_altitude_ft_msl, max_terrain + clearance),
            sample_count=len(samples),
        )

    def within_bounds(self, bundle: TheaterAssetBundle | None, lat: float, lng: float) -> bool:
        if bundle is None:
            return False
        bounds = bundle.elevation.get("bounds") or bundle.basemap.get("bounds")
        if not isinstance(bounds, dict):
            return False
        north = _float(bounds.get("north"))
        south = _float(bounds.get("south"))
        east = _float(bounds.get("east"))
        west = _float(bounds.get("west"))
        if None in {north, south, east, west}:
            return False
        return south <= lat <= north and west <= lng <= east

    def sector_terrain_summary(self, bundle: TheaterAssetBundle | None, sectors: tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
        if bundle is None:
            return ()
        summaries: list[dict[str, Any]] = []
        for sector in sectors:
            if sector.center_lat is None or sector.center_lng is None:
                continue
            elevation = self.point_elevation_ft(bundle, sector.center_lat, sector.center_lng)
            if elevation is None:
                continue
            summaries.append(
                {
                    "sector_id": sector.id,
                    "label": sector.name,
                    "center_elevation_ft_msl": elevation,
                    "hazard_level": "high" if elevation >= 4500 else "medium" if elevation >= 2000 else "low",
                }
            )
        return tuple(summaries)

    def _segment_samples(
        self,
        bundle: TheaterAssetBundle | None,
        start_lat: float,
        start_lng: float,
        end_lat: float,
        end_lng: float,
    ) -> tuple[TerrainSample, ...]:
        if bundle is None:
            return ()
        distance_nm = _nm_distance(start_lat, start_lng, end_lat, end_lng)
        steps = max(1, int(math.ceil(distance_nm / max(self.config.terrain_sample_nm, 1))))
        samples: list[TerrainSample] = []
        for index in range(steps + 1):
            t = index / steps
            lat = start_lat + ((end_lat - start_lat) * t)
            lng = start_lng + ((end_lng - start_lng) * t)
            elevation = self.point_elevation_ft(bundle, lat, lng)
            if elevation is None:
                return ()
            samples.append(TerrainSample(lat=lat, lng=lng, elevation_ft_msl=elevation))
        return tuple(samples)


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _nm_distance(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    mean_lat = math.radians((lat_a + lat_b) / 2.0)
    lat_delta = lat_a - lat_b
    lng_delta = (lng_a - lng_b) * math.cos(mean_lat)
    return math.sqrt((lat_delta * 60.0) ** 2 + (lng_delta * 60.0) ** 2)
