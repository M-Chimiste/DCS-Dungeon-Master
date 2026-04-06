from __future__ import annotations

from pathlib import Path

from dcs_dungeon_master.core.config import AirOpsConfig, MapAssetsConfig, TheaterAssetManifestConfig
from dcs_dungeon_master.map_assets import MapAssetService
from dcs_dungeon_master.terrain import TerrainService
from tests.support_map_assets import write_test_map_assets


def _build_services(tmp_path: Path) -> tuple[MapAssetService, TerrainService]:
    asset_root = tmp_path / "map-assets"
    write_test_map_assets(asset_root)
    map_asset_service = MapAssetService(
        MapAssetsConfig(
            asset_root=str(asset_root),
            theaters={
                "persian_gulf": TheaterAssetManifestConfig(
                    theater_name="Persian Gulf",
                    basemap_manifest="persian_gulf/basemap.json",
                    elevation_manifest="persian_gulf/elevation.json",
                    landmarks_manifest="persian_gulf/landmarks.json",
                )
            },
        )
    )
    terrain_service = TerrainService(AirOpsConfig(enabled=True, fixed_wing_clearance_ft=2000, terrain_sample_nm=2))
    return map_asset_service, terrain_service


def test_point_lookup_and_segment_clearance(tmp_path: Path) -> None:
    map_asset_service, terrain_service = _build_services(tmp_path)
    bundle = map_asset_service.bundle_for_theater("Persian Gulf")

    peak_elevation = terrain_service.point_elevation_ft(bundle, 26.35, 55.55)
    assessment = terrain_service.segment_assessment(
        bundle,
        27.2184,
        56.3778,
        26.35,
        55.55,
        requested_altitude_ft_msl=3000,
        aircraft_category="fixed_wing",
    )

    assert peak_elevation == 4200
    assert assessment is not None
    assert assessment.max_terrain_ft_msl == 4200
    assert assessment.safe_altitude_ft_msl == 6200
    assert assessment.sample_count >= 2
