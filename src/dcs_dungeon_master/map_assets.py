"""Local theater basemap and landmark asset loading."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from dcs_dungeon_master.core.config import MapAssetsConfig, TheaterAssetManifestConfig


def _slugify_theater(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


@dataclass(slots=True, frozen=True)
class TheaterAssetBundle:
    theater_name: str
    theater_slug: str
    basemap_manifest_path: Path
    elevation_manifest_path: Path
    landmarks_manifest_path: Path
    basemap: dict[str, Any]
    elevation: dict[str, Any]
    landmarks: tuple[dict[str, Any], ...]


@dataclass(slots=True)
class MapAssetService:
    config: MapAssetsConfig
    public_prefix: str = "/map-assets"

    @property
    def asset_root(self) -> Path:
        return Path(self.config.asset_root)

    def bundle_for_theater(self, theater_name: str) -> TheaterAssetBundle | None:
        manifest_config = self._manifest_config_for_theater(theater_name)
        if manifest_config is None:
            return None
        try:
            basemap_path = self._resolve_manifest_path(manifest_config.basemap_manifest)
            elevation_path = self._resolve_manifest_path(manifest_config.elevation_manifest)
            landmarks_path = self._resolve_manifest_path(manifest_config.landmarks_manifest)
            basemap = self._load_json(basemap_path)
            elevation = self._load_json(elevation_path)
            landmarks_raw = self._load_json(landmarks_path)
        except FileNotFoundError:
            return None
        if not isinstance(landmarks_raw, list):
            landmarks_raw = []
        return TheaterAssetBundle(
            theater_name=manifest_config.theater_name,
            theater_slug=_slugify_theater(manifest_config.theater_name),
            basemap_manifest_path=basemap_path,
            elevation_manifest_path=elevation_path,
            landmarks_manifest_path=landmarks_path,
            basemap=basemap,
            elevation=elevation,
            landmarks=tuple(item for item in landmarks_raw if isinstance(item, dict)),
        )

    def public_url_for_path(self, path: str | Path) -> str | None:
        candidate = Path(path)
        try:
            relative = candidate.resolve().relative_to(self.asset_root.resolve())
        except Exception:  # noqa: BLE001
            return None
        return f"{self.public_prefix.rstrip('/')}/{relative.as_posix()}"

    def _manifest_config_for_theater(self, theater_name: str) -> TheaterAssetManifestConfig | None:
        target_slug = _slugify_theater(theater_name)
        for key, item in self.config.theaters.items():
            if _slugify_theater(key) == target_slug or _slugify_theater(item.theater_name) == target_slug:
                return item
        return None

    def _resolve_manifest_path(self, relative_path: str) -> Path:
        return (self.asset_root / relative_path).resolve()

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | list[Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
