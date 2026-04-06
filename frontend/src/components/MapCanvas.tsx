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
  control_point_id?: string;
  name?: string;
  sector_id?: string;
  owner?: string | null;
  kind?: string;
  lat?: number | null;
  lng?: number | null;
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
  lat?: number | null;
  lng?: number | null;
};

type LandmarkLike = {
  id?: string;
  name?: string;
  lat?: number | null;
  lng?: number | null;
};

type TerrainSummaryLike = {
  sector_id?: string;
  center_elevation_ft_msl?: number;
  hazard_level?: string;
};

type AirRouteLegLike = {
  leg_id?: string;
  lat?: number | null;
  lng?: number | null;
  altitude_ft_msl?: number | null;
};

type AirPackageLike = {
  package_id?: string;
  coalition?: string | null;
  current_sector_id?: string | null;
  route_legs?: AirRouteLegLike[];
};

type BasemapLike = {
  image_url?: string | null;
  bounds?: {
    north?: number;
    south?: number;
    east?: number;
    west?: number;
  } | null;
};

type Props = {
  sectors: SectorLike[];
  controlPoints?: MarkerLike[];
  zones?: ZoneLike[];
  tracks?: TrackLike[];
  worldGroups?: GroupLike[];
  airPackages?: AirPackageLike[];
  landmarks?: LandmarkLike[];
  terrainSummary?: TerrainSummaryLike[];
  basemap?: BasemapLike | null;
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

const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

export function MapCanvas({
  sectors,
  controlPoints = [],
  zones = [],
  tracks = [],
  worldGroups = [],
  airPackages = [],
  landmarks = [],
  terrainSummary = [],
  basemap = null,
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
  const width = 920;
  const height = 560;
  const bounds = basemap?.bounds ?? null;
  const boundLatitudes = [bounds?.south, bounds?.north].filter(isNumber);
  const boundLongitudes = [bounds?.west, bounds?.east].filter(isNumber);
  const latitudes = [
    ...boundLatitudes,
    ...sectors.map((sector) => sector.center_lat).filter(isNumber),
    ...zones.map((zone) => zone.center_lat).filter(isNumber),
    ...controlPoints.map((point) => point.lat).filter(isNumber),
    ...worldGroups.map((group) => group.lat).filter(isNumber),
    ...landmarks.map((landmark) => landmark.lat).filter(isNumber),
  ];
  const longitudes = [
    ...boundLongitudes,
    ...sectors.map((sector) => sector.center_lng).filter(isNumber),
    ...zones.map((zone) => zone.center_lng).filter(isNumber),
    ...controlPoints.map((point) => point.lng).filter(isNumber),
    ...worldGroups.map((group) => group.lng).filter(isNumber),
    ...landmarks.map((landmark) => landmark.lng).filter(isNumber),
  ];
  const defaultCenterLat = referenceCenterLat ?? 25;
  const defaultCenterLng = referenceCenterLng ?? 55;
  const referenceDelta = Math.max(((referenceRadiusNm ?? 35) / 60) * 2.6, 1);
  const minLat = latitudes.length ? Math.min(...latitudes) : defaultCenterLat - referenceDelta;
  const maxLat = latitudes.length ? Math.max(...latitudes) : defaultCenterLat + referenceDelta;
  const minLng = longitudes.length ? Math.min(...longitudes) : defaultCenterLng - referenceDelta;
  const maxLng = longitudes.length ? Math.max(...longitudes) : defaultCenterLng + referenceDelta;
  const spanLat = Math.max(maxLat - minLat || 0.5, referenceDelta * 2);
  const spanLng = Math.max(maxLng - minLng || 0.5, referenceDelta * 2);
  const terrainBySector = new Map(terrainSummary.map((item) => [item.sector_id ?? "", item]));

  const toPoint = (lat?: number | null, lng?: number | null) => ({
    x: 48 + ((((lng ?? minLng) - minLng) / spanLng) * (width - 96)),
    y: 44 + ((1 - (((lat ?? minLat) - minLat) / spanLat)) * (height - 88)),
  });

  const toLatLng = (x: number, y: number) => ({
    lng: minLng + (((x - 48) / (width - 96)) * spanLng),
    lat: minLat + ((1 - ((y - 44) / (height - 88))) * spanLat),
  });

  const coordinatesBySector = new Map(sectors.map((sector) => [sector.id, toPoint(sector.center_lat, sector.center_lng)]));
  const coordinatesByZone = new Map(zones.map((zone) => [zone.id, toPoint(zone.center_lat, zone.center_lng)]));

  const zoneRadiusPx = (radiusNm?: number) => {
    const latDegrees = (radiusNm ?? 10) / 60;
    return Math.max(10, Math.min(54, (latDegrees / spanLat) * (height - 88)));
  };

  const pointForMarker = (point: MarkerLike) => {
    if (isNumber(point.lat) && isNumber(point.lng)) {
      return toPoint(point.lat, point.lng);
    }
    return coordinatesBySector.get(point.sector_id ?? "");
  };

  const pointForGroup = (group: GroupLike) => {
    if (isNumber(group.lat) && isNumber(group.lng)) {
      return toPoint(group.lat, group.lng);
    }
    return coordinatesBySector.get(group.sector_id ?? "");
  };

  return (
    <div className="map-card">
      {title ? <div className="map-title">{title}</div> : null}
      <svg viewBox={`0 0 ${width} ${height}`} className="map-svg">
        <defs>
          <linearGradient id="paper" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#fbf5e5" />
            <stop offset="100%" stopColor="#efe1bf" />
          </linearGradient>
          <linearGradient id="mapFade" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="rgba(255,255,255,0.06)" />
            <stop offset="100%" stopColor="rgba(28, 34, 39, 0.18)" />
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
            onMapClick(toLatLng(x, y));
          }}
        />
        {basemap?.image_url ? (
          <>
            <image href={basemap.image_url} x="0" y="0" width={width} height={height} preserveAspectRatio="none" />
            <rect x="0" y="0" width={width} height={height} rx="20" fill="url(#mapFade)" />
          </>
        ) : null}
        {zones.map((zone) => {
          const position = coordinatesByZone.get(zone.id);
          if (!position) return null;
          return (
            <g key={zone.id}>
              <circle
                cx={position.x}
                cy={position.y}
                r={zoneRadiusPx(zone.radius_nm)}
                className={`zone-ring ${selectedZoneId === zone.id ? "selected" : ""} ${onZoneSelect ? "interactive" : ""}`}
                onClick={() => onZoneSelect?.(zone.id)}
              />
              <text x={position.x} y={position.y - zoneRadiusPx(zone.radius_nm) - 8} textAnchor="middle" className="marker-label">
                {zone.name ?? zone.id}
              </text>
            </g>
          );
        })}
        {airPackages.map((airPackage, index) => {
          const route = (airPackage.route_legs ?? [])
            .filter((leg) => isNumber(leg.lat) && isNumber(leg.lng))
            .map((leg) => toPoint(leg.lat, leg.lng));
          if (!route.length) return null;
          const polyline = route.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
          const lastPoint = route[route.length - 1];
          return (
            <g key={airPackage.package_id ?? `air-package-${index}`}>
              <polyline points={polyline} className={`air-package-route ${airPackage.coalition ?? "unknown"}`} />
              <circle cx={lastPoint.x} cy={lastPoint.y} r="7" className={`air-package-node ${airPackage.coalition ?? "unknown"}`} />
            </g>
          );
        })}
        {sectors.map((sector) => {
          const position = coordinatesBySector.get(sector.id);
          if (!position) return null;
          const radius = Math.max(18, Math.min(52, zoneRadiusPx(sector.radius_nm)));
          const terrain = terrainBySector.get(sector.id);
          return (
            <g key={sector.id}>
              <circle
                cx={position.x}
                cy={position.y}
                r={radius}
                className={`sector-ring sector-${sector.role ?? "unknown"} ${selectedSectorId === sector.id ? "selected" : ""} ${onSectorSelect ? "interactive" : ""}`}
                onClick={() => onSectorSelect?.(sector.id)}
              />
              {terrain?.hazard_level ? (
                <circle
                  cx={position.x}
                  cy={position.y}
                  r={radius + 6}
                  className={`terrain-halo terrain-${terrain.hazard_level}`}
                />
              ) : null}
              <text x={position.x} y={position.y + 4} textAnchor="middle" className="sector-label">
                {sector.name ?? sector.id}
              </text>
              {terrain?.center_elevation_ft_msl ? (
                <text x={position.x} y={position.y + radius + 14} textAnchor="middle" className="marker-label terrain-label">
                  {Math.round(terrain.center_elevation_ft_msl)} ft
                </text>
              ) : null}
            </g>
          );
        })}
        {landmarks.map((landmark, index) => {
          if (!isNumber(landmark.lat) || !isNumber(landmark.lng)) return null;
          const position = toPoint(landmark.lat, landmark.lng);
          return (
            <g key={landmark.id ?? `landmark-${index}`}>
              <path
                d={`M ${position.x.toFixed(1)} ${(position.y - 11).toFixed(1)} L ${(position.x - 7).toFixed(1)} ${(position.y + 5).toFixed(1)} L ${(position.x + 7).toFixed(1)} ${(position.y + 5).toFixed(1)} Z`}
                className="landmark-mark"
              />
              <text x={position.x + 10} y={position.y - 10} className="marker-label">
                {landmark.name ?? landmark.id ?? "Landmark"}
              </text>
            </g>
          );
        })}
        {controlPoints.map((point, index) => {
          const position = pointForMarker(point);
          const pointId = point.id ?? point.control_point_id;
          if (!position) return null;
          return (
            <g key={`${pointId ?? point.name}-${index}`}>
              <rect
                x={position.x - 7}
                y={position.y - 32}
                width="14"
                height="14"
                className={`control-point owner-${point.owner ?? "unknown"} ${selectedControlPointId === pointId ? "selected" : ""} ${onControlPointSelect ? "interactive" : ""}`}
                onClick={() => pointId && onControlPointSelect?.(pointId)}
              />
              <text x={position.x + 12} y={position.y - 18} className="marker-label">
                {point.name ?? pointId ?? "CP"}
              </text>
            </g>
          );
        })}
        {worldGroups.map((group, index) => {
          const position = pointForGroup(group);
          if (!position) return null;
          return (
            <circle
              key={`${group.id ?? group.sector_id}-${index}`}
              cx={position.x - 16}
              cy={position.y + 14}
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
            <circle
              key={`${track.track_id ?? sectorId}-${index}`}
              cx={position.x}
              cy={position.y + 28}
              r="7"
              className={`track-dot ${track.stale ? "stale" : "fresh"} ${track.inferred ? "inferred" : ""}`}
            />
          );
        })}
      </svg>
    </div>
  );
}
