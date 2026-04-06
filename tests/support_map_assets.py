from __future__ import annotations

from pathlib import Path


def write_test_map_assets(asset_root: Path) -> Path:
    theater_dir = asset_root / "persian_gulf"
    theater_dir.mkdir(parents=True, exist_ok=True)
    (theater_dir / "basemap.svg").write_text(
        """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800">
  <rect width="1200" height="800" fill="#dbeafe" />
  <rect x="640" y="0" width="560" height="800" fill="#fde68a" />
  <circle cx="730" cy="260" r="120" fill="#a3b18a" />
  <circle cx="820" cy="210" r="80" fill="#7f5539" />
</svg>
""".strip(),
        encoding="utf-8",
    )
    (theater_dir / "basemap.json").write_text(
        """
{
  "image_path": "basemap.svg",
  "media_type": "image/svg+xml",
  "bounds": {
    "north": 27.5,
    "south": 24.0,
    "west": 52.0,
    "east": 56.8
  }
}
""".strip(),
        encoding="utf-8",
    )
    (theater_dir / "elevation.json").write_text(
        """
{
  "bounds": {
    "north": 27.5,
    "south": 24.0,
    "west": 52.0,
    "east": 56.8
  },
  "grid_ft": [
    [400, 900, 1200, 1500, 1800],
    [500, 1800, 3200, 4200, 2600],
    [700, 2300, 6400, 5200, 2800],
    [450, 1400, 2900, 3500, 2100],
    [200, 600, 900, 1200, 1500]
  ]
}
""".strip(),
        encoding="utf-8",
    )
    (theater_dir / "landmarks.json").write_text(
        """
[
  {
    "id": "mountain_peak",
    "name": "Mountain Peak",
    "lat": 26.35,
    "lng": 55.55,
    "tags": ["mountain", "terrain"]
  },
  {
    "id": "gulf_water",
    "name": "Persian Gulf",
    "lat": 25.2,
    "lng": 54.9,
    "tags": ["water", "coast"]
  }
]
""".strip(),
        encoding="utf-8",
    )
    return theater_dir
