type SectorLike = {
  id: string;
  name?: string;
  role?: string;
  center_lat?: number | null;
  center_lng?: number | null;
  radius_nm?: number;
};

type MarkerLike = {
  id?: string;
  name?: string;
  sector_id?: string;
  owner?: string | null;
  kind?: string;
};

type ZoneLike = {
  id: string;
  name?: string;
  sector_id?: string;
  center_lat?: number | null;
  center_lng?: number | null;
  radius_nm?: number;
};

type TrackLike = {
  track_id?: string;
  last_known_sector_id?: string | null;
  estimated_sector_id?: string | null;
  stale?: boolean;
  inferred?: boolean;
};

type GroupLike = {
  id?: string;
  sector_id?: string | null;
  coalition?: string | null;
};

type Props = {
  sectors: SectorLike[];
  controlPoints?: MarkerLike[];
  zones?: ZoneLike[];
  tracks?: TrackLike[];
  worldGroups?: GroupLike[];
  title?: string;
  selectedSectorId?: string | null;
  selectedControlPointId?: string | null;
  selectedZoneId?: string | null;
  referenceCenterLat?: number | null;
  referenceCenterLng?: number | null;
  referenceRadiusNm?: number | null;
  onSectorSelect?: (sectorId: string) => void;
  onControlPointSelect?: (controlPointId: string) => void;
  onZoneSelect?: (zoneId: string) => void;
  onMapClick?: (coords: { lat: number; lng: number }) => void;
};

export function MapCanvas({
  sectors,
  controlPoints = [],
  zones = [],
  tracks = [],
  worldGroups = [],
  title,
  selectedSectorId = null,
  selectedControlPointId = null,
  selectedZoneId = null,
  referenceCenterLat = null,
  referenceCenterLng = null,
  referenceRadiusNm = null,
  onSectorSelect,
  onControlPointSelect,
  onZoneSelect,
  onMapClick,
}: Props) {
  const width = 720;
  const height = 420;
  const latitudes = [
    ...sectors.map((sector) => sector.center_lat).filter((value): value is number => typeof value === "number"),
    ...zones.map((zone) => zone.center_lat).filter((value): value is number => typeof value === "number"),
  ];
  const longitudes = [
    ...sectors.map((sector) => sector.center_lng).filter((value): value is number => typeof value === "number"),
    ...zones.map((zone) => zone.center_lng).filter((value): value is number => typeof value === "number"),
  ];
  const defaultCenterLat = referenceCenterLat ?? 25;
  const defaultCenterLng = referenceCenterLng ?? 55;
  const referenceDelta = Math.max(((referenceRadiusNm ?? 35) / 60) * 2.5, 1);
  const minLat = latitudes.length ? Math.min(...latitudes) : defaultCenterLat - referenceDelta;
  const maxLat = latitudes.length ? Math.max(...latitudes) : defaultCenterLat + referenceDelta;
  const minLng = longitudes.length ? Math.min(...longitudes) : defaultCenterLng - referenceDelta;
  const maxLng = longitudes.length ? Math.max(...longitudes) : defaultCenterLng + referenceDelta;
  const spanLat = Math.max(referenceDelta * 2, maxLat - minLat || 0.5);
  const spanLng = Math.max(referenceDelta * 2, maxLng - minLng || 0.5);

  const toPoint = (lat?: number | null, lng?: number | null) => ({
    x: 60 + (((lng ?? minLng) - minLng) / spanLng) * (width - 120),
    y: 50 + (1 - (((lat ?? minLat) - minLat) / spanLat)) * (height - 110),
  });

  const coordinatesBySector = new Map(sectors.map((sector) => [sector.id, toPoint(sector.center_lat, sector.center_lng)]));
  const coordinatesByZone = new Map(zones.map((zone) => [zone.id, toPoint(zone.center_lat, zone.center_lng)]));

  return (
    <div className="map-card">
      {title ? <div className="map-title">{title}</div> : null}
      <svg viewBox={`0 0 ${width} ${height}`} className="map-svg">
        <defs>
          <linearGradient id="paper" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#fbf5e5" />
            <stop offset="100%" stopColor="#efe1bf" />
          </linearGradient>
        </defs>
        <rect
          x="0"
          y="0"
          width={width}
          height={height}
          rx="20"
          fill="url(#paper)"
          className={onMapClick ? "map-surface interactive" : "map-surface"}
          onClick={(event) => {
            if (!onMapClick) return;
            const rect = event.currentTarget.getBoundingClientRect();
            const x = ((event.clientX - rect.left) / rect.width) * width;
            const y = ((event.clientY - rect.top) / rect.height) * height;
            const lng = minLng + ((x - 60) / (width - 120)) * spanLng;
            const lat = minLat + (1 - (y - 50) / (height - 110)) * spanLat;
            onMapClick({ lat, lng });
          }}
        />
        {zones.map((zone) => {
          const position = coordinatesByZone.get(zone.id);
          if (!position) return null;
          const radius = Math.max(10, Math.min(42, (zone.radius_nm ?? 12) * 0.55));
          return (
            <g key={zone.id}>
              <circle
                cx={position.x}
                cy={position.y}
                r={radius}
                className={`zone-ring ${selectedZoneId === zone.id ? "selected" : ""} ${onZoneSelect ? "interactive" : ""}`}
                onClick={() => onZoneSelect?.(zone.id)}
              />
              <text x={position.x} y={position.y - radius - 6} textAnchor="middle" className="marker-label">
                {zone.name ?? zone.id}
              </text>
            </g>
          );
        })}
        {sectors.map((sector) => {
          const position = coordinatesBySector.get(sector.id);
          if (!position) return null;
          const radius = Math.max(18, Math.min(48, (sector.radius_nm ?? 35) * 0.55));
          return (
            <g key={sector.id}>
              <circle
                cx={position.x}
                cy={position.y}
                r={radius}
                className={`sector-ring sector-${sector.role ?? "unknown"} ${selectedSectorId === sector.id ? "selected" : ""} ${onSectorSelect ? "interactive" : ""}`}
                onClick={() => onSectorSelect?.(sector.id)}
              />
              <text x={position.x} y={position.y + 4} textAnchor="middle" className="sector-label">
                {sector.name ?? sector.id}
              </text>
            </g>
          );
        })}
        {controlPoints.map((point, index) => {
          const position = coordinatesBySector.get(point.sector_id ?? "");
          if (!position) return null;
          return (
            <g key={`${point.id ?? point.name}-${index}`}>
              <rect
                x={position.x - 7}
                y={position.y - 32}
                width="14"
                height="14"
                className={`control-point owner-${point.owner ?? "unknown"} ${selectedControlPointId === point.id ? "selected" : ""} ${onControlPointSelect ? "interactive" : ""}`}
                onClick={() => point.id && onControlPointSelect?.(point.id)}
              />
              <text x={position.x + 12} y={position.y - 18} className="marker-label">
                {point.name ?? point.id ?? "CP"}
              </text>
            </g>
          );
        })}
        {worldGroups.map((group, index) => {
          const sectorId = group.sector_id ?? "";
          const position = coordinatesBySector.get(sectorId);
          if (!position) return null;
          return (
            <circle
              key={`${group.id ?? sectorId}-${index}`}
              cx={position.x - 18}
              cy={position.y + 18}
              r="5"
              className={`world-group ${group.coalition ?? "unknown"}`}
            />
          );
        })}
        {tracks.map((track, index) => {
          const sectorId = track.estimated_sector_id ?? track.last_known_sector_id ?? "";
          const position = coordinatesBySector.get(sectorId);
          if (!position) return null;
          return (
            <g key={`${track.track_id ?? sectorId}-${index}`}>
              <circle
                cx={position.x}
                cy={position.y + 28}
                r="7"
                className={`track-dot ${track.stale ? "stale" : "fresh"} ${track.inferred ? "inferred" : ""}`}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}
