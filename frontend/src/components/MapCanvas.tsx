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
  tracks?: TrackLike[];
  worldGroups?: GroupLike[];
  title?: string;
  selectedSectorId?: string | null;
  selectedControlPointId?: string | null;
  onSectorSelect?: (sectorId: string) => void;
  onControlPointSelect?: (controlPointId: string) => void;
};

export function MapCanvas({
  sectors,
  controlPoints = [],
  tracks = [],
  worldGroups = [],
  title,
  selectedSectorId = null,
  selectedControlPointId = null,
  onSectorSelect,
  onControlPointSelect,
}: Props) {
  const width = 720;
  const height = 420;
  const latitudes = sectors.map((sector) => sector.center_lat ?? 0);
  const longitudes = sectors.map((sector) => sector.center_lng ?? 0);
  const minLat = Math.min(...latitudes, 0);
  const maxLat = Math.max(...latitudes, 1);
  const minLng = Math.min(...longitudes, 0);
  const maxLng = Math.max(...longitudes, 1);
  const spanLat = Math.max(1, maxLat - minLat);
  const spanLng = Math.max(1, maxLng - minLng);
  const coordinatesBySector = new Map(
    sectors.map((sector) => [
      sector.id,
      {
        x: 60 + (((sector.center_lng ?? minLng) - minLng) / spanLng) * (width - 120),
        y: 50 + (1 - (((sector.center_lat ?? minLat) - minLat) / spanLat)) * (height - 110),
      },
    ]),
  );

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
        <rect x="0" y="0" width={width} height={height} rx="20" fill="url(#paper)" />
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
