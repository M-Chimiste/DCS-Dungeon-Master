import L, { type LatLngBoundsExpression, type LatLngExpression } from "leaflet";
import {
  Circle,
  CircleMarker,
  ImageOverlay,
  MapContainer,
  Marker,
  Pane,
  Polyline,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";

type CoordTuple = [number, number];

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
  id?: string;
  last_known_sector_id?: string | null;
  estimated_sector_id?: string | null;
  stale?: boolean;
  inferred?: boolean;
};

type GroupLike = {
  id?: string;
  name?: string;
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
  tags?: string[];
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
  selectedLandmarkId?: string | null;
  selectedAirPackageId?: string | null;
  referenceCenterLat?: number | null;
  referenceCenterLng?: number | null;
  referenceRadiusNm?: number | null;
  onSectorSelect?: (sectorId: string) => void;
  onControlPointSelect?: (controlPointId: string) => void;
  onZoneSelect?: (zoneId: string) => void;
  onLandmarkSelect?: (landmarkId: string) => void;
  onAirPackageSelect?: (packageId: string) => void;
  onMapClick?: (coords: { lat: number; lng: number }) => void;
  onSectorMove?: (sectorId: string, coords: { lat: number; lng: number }) => void;
  onControlPointMove?: (controlPointId: string, coords: { lat: number; lng: number }) => void;
  onZoneMove?: (zoneId: string, coords: { lat: number; lng: number }) => void;
  onLandmarkMove?: (landmarkId: string, coords: { lat: number; lng: number }) => void;
};

const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const NM_TO_METERS = 1852;

function latLng(value?: number | null, other?: number | null): CoordTuple | null {
  if (!isNumber(value) || !isNumber(other)) return null;
  return [value, other];
}

function roleColor(role?: string) {
  switch (role) {
    case "front":
      return "#d47a62";
    case "rear":
      return "#6d9f71";
    case "support":
      return "#7390bb";
    case "contested":
      return "#d9a441";
    case "air_corridor":
      return "#7f6ab2";
    default:
      return "#8b7d6b";
  }
}

function coalitionColor(coalition?: string | null) {
  if (coalition === "red") return "#a72637";
  if (coalition === "blue") return "#2b6cb0";
  return "#4b5563";
}

function hazardColor(hazard?: string) {
  if (hazard === "high") return "rgba(146, 34, 34, 0.64)";
  if (hazard === "medium") return "rgba(180, 83, 9, 0.56)";
  return "rgba(37, 99, 235, 0.36)";
}

function dragIcon(label: string, tone: "red" | "blue" | "amber" | "stone" = "stone") {
  return L.divIcon({
    className: "",
    html: `<div class="drag-handle ${tone}">${label}</div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

function boundsFromFeatures(
  sectors: SectorLike[],
  controlPoints: MarkerLike[],
  zones: ZoneLike[],
  landmarks: LandmarkLike[],
  worldGroups: GroupLike[],
  basemap?: BasemapLike | null,
): LatLngBoundsExpression | null {
  const entries: CoordTuple[] = [];
  if (
    basemap?.bounds &&
    isNumber(basemap.bounds.south) &&
    isNumber(basemap.bounds.west) &&
    isNumber(basemap.bounds.north) &&
    isNumber(basemap.bounds.east)
  ) {
    entries.push([basemap.bounds.south, basemap.bounds.west], [basemap.bounds.north, basemap.bounds.east]);
  }
  sectors.forEach((sector) => {
    const point = latLng(sector.center_lat, sector.center_lng);
    if (point) entries.push(point);
  });
  controlPoints.forEach((point) => {
    const entry = latLng(point.lat, point.lng);
    if (entry) entries.push(entry);
  });
  zones.forEach((zone) => {
    const point = latLng(zone.center_lat, zone.center_lng);
    if (point) entries.push(point);
  });
  landmarks.forEach((landmark) => {
    const point = latLng(landmark.lat, landmark.lng);
    if (point) entries.push(point);
  });
  worldGroups.forEach((group) => {
    const point = latLng(group.lat, group.lng);
    if (point) entries.push(point);
  });
  return entries.length ? entries : null;
}

function schematicPoint(
  lat: number,
  lng: number,
  minLat: number,
  minLng: number,
  spanLat: number,
  spanLng: number,
  width: number,
  height: number,
) {
  return {
    x: 48 + (((lng - minLng) / spanLng) * (width - 96)),
    y: 44 + ((1 - ((lat - minLat) / spanLat)) * (height - 88)),
  };
}

function LegacySvgMap({
  sectors,
  controlPoints = [],
  zones = [],
  tracks = [],
  worldGroups = [],
  airPackages = [],
  landmarks = [],
  terrainSummary = [],
  title,
  selectedSectorId,
  selectedControlPointId,
  selectedZoneId,
  selectedLandmarkId,
  selectedAirPackageId,
  referenceCenterLat,
  referenceCenterLng,
  referenceRadiusNm,
  onSectorSelect,
  onControlPointSelect,
  onZoneSelect,
  onLandmarkSelect,
  onAirPackageSelect,
  onMapClick,
}: Props) {
  const width = 920;
  const height = 560;
  const latitudes = [
    ...sectors.map((sector) => sector.center_lat).filter(isNumber),
    ...zones.map((zone) => zone.center_lat).filter(isNumber),
    ...controlPoints.map((point) => point.lat).filter(isNumber),
    ...worldGroups.map((group) => group.lat).filter(isNumber),
    ...landmarks.map((landmark) => landmark.lat).filter(isNumber),
  ];
  const longitudes = [
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
  const sectorCenters = new Map(
    sectors
      .filter((sector) => isNumber(sector.center_lat) && isNumber(sector.center_lng))
      .map((sector) => [
        sector.id,
        schematicPoint(
          sector.center_lat as number,
          sector.center_lng as number,
          minLat,
          minLng,
          spanLat,
          spanLng,
          width,
          height,
        ),
      ]),
  );
  const zoneCenters = new Map(
    zones
      .filter((zone) => isNumber(zone.center_lat) && isNumber(zone.center_lng))
      .map((zone) => [
        zone.id,
        schematicPoint(
          zone.center_lat as number,
          zone.center_lng as number,
          minLat,
          minLng,
          spanLat,
          spanLng,
          width,
          height,
        ),
      ]),
  );
  const project = (lat?: number | null, lng?: number | null) => {
    if (!isNumber(lat) || !isNumber(lng)) return null;
    return schematicPoint(lat, lng, minLat, minLng, spanLat, spanLng, width, height);
  };
  const pointForControlPoint = (point: MarkerLike) => project(point.lat, point.lng) ?? sectorCenters.get(point.sector_id ?? "");
  const pointForTrack = (track: TrackLike) => sectorCenters.get(track.estimated_sector_id ?? track.last_known_sector_id ?? "");
  const pointForGroup = (group: GroupLike) => project(group.lat, group.lng) ?? sectorCenters.get(group.sector_id ?? "");
  const toLatLng = (x: number, y: number) => ({
    lng: minLng + (((x - 48) / (width - 96)) * spanLng),
    lat: minLat + ((1 - ((y - 44) / (height - 88))) * spanLat),
  });

  return (
    <div className="map-card">
      {title ? <div className="map-title">{title}</div> : null}
      <div className="map-mode-badge">Schematic fallback</div>
      <svg viewBox={`0 0 ${width} ${height}`} className="map-svg">
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
        <defs>
          <linearGradient id="paper" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#fbf5e5" />
            <stop offset="100%" stopColor="#efe1bf" />
          </linearGradient>
        </defs>
        {zones.map((zone) => {
          const point = zoneCenters.get(zone.id);
          if (!point) return null;
          const radius = Math.max(12, Math.min(56, (((zone.radius_nm ?? 12) / 60) / spanLat) * (height - 88)));
          return (
            <g key={zone.id}>
              <circle
                cx={point.x}
                cy={point.y}
                r={radius}
                className={`zone-ring ${selectedZoneId === zone.id ? "selected" : ""} ${onZoneSelect ? "interactive" : ""}`}
                onClick={() => onZoneSelect?.(zone.id)}
              />
              <text x={point.x} y={point.y - radius - 8} textAnchor="middle" className="marker-label">
                {zone.name ?? zone.id}
              </text>
            </g>
          );
        })}
        {sectors.map((sector) => {
          const point = sectorCenters.get(sector.id);
          if (!point) return null;
          const terrain = terrainBySector.get(sector.id);
          const radius = Math.max(18, Math.min(58, (((sector.radius_nm ?? 35) / 60) / spanLat) * (height - 88)));
          return (
            <g key={sector.id}>
              {terrain?.hazard_level ? (
                <circle cx={point.x} cy={point.y} r={radius + 6} className={`terrain-halo terrain-${terrain.hazard_level}`} />
              ) : null}
              <circle
                cx={point.x}
                cy={point.y}
                r={radius}
                className={`sector-ring sector-${sector.role ?? "unknown"} ${selectedSectorId === sector.id ? "selected" : ""} ${onSectorSelect ? "interactive" : ""}`}
                onClick={() => onSectorSelect?.(sector.id)}
              />
              <text x={point.x} y={point.y + 4} textAnchor="middle" className="sector-label">
                {sector.name ?? sector.id}
              </text>
            </g>
          );
        })}
        {landmarks.map((landmark, index) => {
          const point = project(landmark.lat, landmark.lng);
          if (!point) return null;
          return (
            <g key={landmark.id ?? `landmark-${index}`}>
              <path
                d={`M ${point.x.toFixed(1)} ${(point.y - 11).toFixed(1)} L ${(point.x - 7).toFixed(1)} ${(point.y + 5).toFixed(1)} L ${(point.x + 7).toFixed(1)} ${(point.y + 5).toFixed(1)} Z`}
                className={selectedLandmarkId === landmark.id ? "landmark-mark selected" : "landmark-mark"}
                onClick={() => landmark.id && onLandmarkSelect?.(landmark.id)}
              />
              <text x={point.x + 10} y={point.y - 10} className="marker-label">
                {landmark.name ?? landmark.id ?? "Landmark"}
              </text>
            </g>
          );
        })}
        {controlPoints.map((point, index) => {
          const rendered = pointForControlPoint(point);
          const pointId = point.id ?? point.control_point_id;
          if (!rendered || !pointId) return null;
          return (
            <g key={`${pointId}-${index}`}>
              <rect
                x={rendered.x - 7}
                y={rendered.y - 32}
                width="14"
                height="14"
                rx="3"
                className={`control-point owner-${point.owner ?? "unknown"} ${selectedControlPointId === pointId ? "selected" : ""} ${onControlPointSelect ? "interactive" : ""}`}
                onClick={() => onControlPointSelect?.(pointId)}
              />
              <text x={rendered.x} y={rendered.y - 18} textAnchor="middle" className="marker-label">
                {point.name ?? pointId}
              </text>
            </g>
          );
        })}
        {tracks.map((track, index) => {
          const point = pointForTrack(track);
          if (!point) return null;
          return <circle key={track.track_id ?? track.id ?? `track-${index}`} cx={point.x} cy={point.y} r="5" className="track-dot" />;
        })}
        {worldGroups.map((group, index) => {
          const point = pointForGroup(group);
          if (!point) return null;
          return <circle key={group.id ?? `group-${index}`} cx={point.x} cy={point.y} r="6" className={`world-group ${group.coalition ?? "unknown"}`} />;
        })}
        {airPackages.map((airPackage, index) => {
          const route = (airPackage.route_legs ?? [])
            .map((leg) => project(leg.lat, leg.lng))
            .filter((entry): entry is { x: number; y: number } => entry !== null);
          if (!route.length) return null;
          return (
            <g key={airPackage.package_id ?? `package-${index}`}>
              <polyline
                points={route.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ")}
                className={`air-package-route ${airPackage.coalition ?? "unknown"} ${selectedAirPackageId === airPackage.package_id ? "selected" : ""}`}
                onClick={() => airPackage.package_id && onAirPackageSelect?.(airPackage.package_id)}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function MapClickCapture({ onMapClick }: { onMapClick?: (coords: { lat: number; lng: number }) => void }) {
  useMapEvents({
    click(event) {
      onMapClick?.({ lat: event.latlng.lat, lng: event.latlng.lng });
    },
  });
  return null;
}

function FitControls({
  theaterBounds,
  frontBounds,
  packageBounds,
  selectionBounds,
}: {
  theaterBounds: [CoordTuple, CoordTuple];
  frontBounds: CoordTuple[] | null;
  packageBounds: CoordTuple[] | null;
  selectionBounds: CoordTuple[] | null;
}) {
  const map = useMap();
  const fit = (bounds: LatLngBoundsExpression | null) => {
    if (!bounds) return;
    map.fitBounds(bounds, { padding: [24, 24] });
  };
  return (
    <div className="map-floating-controls">
      <button type="button" onClick={() => fit(theaterBounds)}>
        Fit Theater
      </button>
      <button type="button" onClick={() => fit(frontBounds)} disabled={!frontBounds}>
        Fit Front
      </button>
      <button type="button" onClick={() => fit(packageBounds)} disabled={!packageBounds}>
        Fit Package
      </button>
      <button type="button" onClick={() => fit(selectionBounds)} disabled={!selectionBounds}>
        Fit Selection
      </button>
    </div>
  );
}

function SelectionHandle({
  position,
  label,
  tone,
  onMove,
}: {
  position: LatLngExpression | null;
  label: string;
  tone: "red" | "blue" | "amber" | "stone";
  onMove?: (coords: { lat: number; lng: number }) => void;
}) {
  if (!position || !onMove) return null;
  return (
    <Marker
      position={position}
      icon={dragIcon(label, tone)}
      draggable
      eventHandlers={{
        dragend(event) {
          const marker = event.target;
          const next = marker.getLatLng();
          onMove({ lat: next.lat, lng: next.lng });
        },
      }}
    />
  );
}

export function MapCanvas(props: Props) {
  const {
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
    selectedLandmarkId = null,
    selectedAirPackageId = null,
    referenceCenterLat = null,
    referenceCenterLng = null,
    referenceRadiusNm = null,
    onSectorSelect,
    onControlPointSelect,
    onZoneSelect,
    onLandmarkSelect,
    onAirPackageSelect,
    onMapClick,
    onSectorMove,
    onControlPointMove,
    onZoneMove,
    onLandmarkMove,
  } = props;

  const hasLeafletBasemap =
    !!basemap?.image_url &&
    !!basemap?.bounds &&
    isNumber(basemap.bounds.north) &&
    isNumber(basemap.bounds.south) &&
    isNumber(basemap.bounds.east) &&
    isNumber(basemap.bounds.west);

  if (!hasLeafletBasemap) {
    return <LegacySvgMap {...props} />;
  }

  const sectorById = new Map(sectors.map((sector) => [sector.id, sector]));
  const controlPointById = new Map(controlPoints.map((point) => [point.id ?? point.control_point_id ?? "", point]));
  const zoneById = new Map(zones.map((zone) => [zone.id, zone]));
  const landmarkById = new Map(landmarks.map((landmark) => [landmark.id ?? "", landmark]));
  const terrainBySector = new Map(terrainSummary.map((item) => [item.sector_id ?? "", item]));
  const sectorLatLng = new Map(
    sectors
      .filter((sector) => isNumber(sector.center_lat) && isNumber(sector.center_lng))
      .map((sector) => [sector.id, [sector.center_lat as number, sector.center_lng as number] as CoordTuple]),
  );
  const controlPointPosition = (point: MarkerLike) =>
    latLng(point.lat, point.lng) ?? sectorLatLng.get(point.sector_id ?? "") ?? null;
  const groupPosition = (group: GroupLike) => latLng(group.lat, group.lng) ?? sectorLatLng.get(group.sector_id ?? "") ?? null;
  const trackPosition = (track: TrackLike) =>
    sectorLatLng.get(track.estimated_sector_id ?? track.last_known_sector_id ?? "") ?? null;
  const theaterBounds: [CoordTuple, CoordTuple] = [
    [basemap.bounds!.south as number, basemap.bounds!.west as number],
    [basemap.bounds!.north as number, basemap.bounds!.east as number],
  ];
  const defaultCenter: LatLngExpression = [
    referenceCenterLat ?? ((basemap.bounds!.south as number) + (basemap.bounds!.north as number)) / 2,
    referenceCenterLng ?? ((basemap.bounds!.west as number) + (basemap.bounds!.east as number)) / 2,
  ];
  const selectionEntries: CoordTuple[] = [];
  const selectedSector = selectedSectorId ? sectorById.get(selectedSectorId) : undefined;
  const selectedControlPoint = selectedControlPointId ? controlPointById.get(selectedControlPointId) : undefined;
  const selectedZone = selectedZoneId ? zoneById.get(selectedZoneId) : undefined;
  const selectedLandmark = selectedLandmarkId ? landmarkById.get(selectedLandmarkId) : undefined;
  const selectedPackage = selectedAirPackageId
    ? airPackages.find((item) => item.package_id === selectedAirPackageId)
    : undefined;
  if (selectedSector && isNumber(selectedSector.center_lat) && isNumber(selectedSector.center_lng)) {
    selectionEntries.push([selectedSector.center_lat, selectedSector.center_lng]);
  }
  if (selectedControlPoint) {
    const position = controlPointPosition(selectedControlPoint);
    if (position) selectionEntries.push(position);
  }
  if (selectedZone && isNumber(selectedZone.center_lat) && isNumber(selectedZone.center_lng)) {
    selectionEntries.push([selectedZone.center_lat, selectedZone.center_lng]);
  }
  if (selectedLandmark && isNumber(selectedLandmark.lat) && isNumber(selectedLandmark.lng)) {
    selectionEntries.push([selectedLandmark.lat, selectedLandmark.lng]);
  }
  const selectionBounds = selectionEntries.length ? selectionEntries : null;
  const frontBounds: CoordTuple[] = sectors
    .filter((sector) => ["front", "contested", "air_corridor"].includes(sector.role ?? "") && isNumber(sector.center_lat) && isNumber(sector.center_lng))
    .map((sector) => [sector.center_lat as number, sector.center_lng as number] as CoordTuple);
  const packageBounds: CoordTuple[] | null = selectedPackage
    ? (selectedPackage.route_legs ?? [])
        .filter((leg) => isNumber(leg.lat) && isNumber(leg.lng))
        .map((leg) => [leg.lat as number, leg.lng as number] as CoordTuple)
    : null;
  const mapBounds =
    boundsFromFeatures(sectors, controlPoints, zones, landmarks, worldGroups, basemap) ?? theaterBounds;

  return (
    <div className="map-card interactive-map-card">
      {title ? <div className="map-title">{title}</div> : null}
      <div className="map-shell">
        <MapContainer className="leaflet-map" bounds={mapBounds} center={defaultCenter} zoom={7} scrollWheelZoom>
          <Pane name="basemap" style={{ zIndex: 100 }} />
          <Pane name="terrain" style={{ zIndex: 250 }} />
          <Pane name="sectors" style={{ zIndex: 300 }} />
          <Pane name="zones" style={{ zIndex: 320 }} />
          <Pane name="routes" style={{ zIndex: 340 }} />
          <Pane name="markers" style={{ zIndex: 400 }} />
          <Pane name="handles" style={{ zIndex: 650 }} />

          <ImageOverlay pane="basemap" url={basemap.image_url!} bounds={theaterBounds} />
          <MapClickCapture onMapClick={onMapClick} />
          <FitControls
            theaterBounds={theaterBounds}
            frontBounds={frontBounds.length ? frontBounds : null}
            packageBounds={packageBounds?.length ? packageBounds : null}
            selectionBounds={selectionBounds}
          />

          {sectors.map((sector) => {
            const center = latLng(sector.center_lat, sector.center_lng);
            if (!center) return null;
            const terrain = terrainBySector.get(sector.id);
            return (
              <Pane key={sector.id} name="sectors">
                {terrain?.hazard_level ? (
                  <Circle
                    center={center}
                    radius={((sector.radius_nm ?? 35) + 6) * NM_TO_METERS}
                    pathOptions={{ color: hazardColor(terrain.hazard_level), fillOpacity: 0, weight: 2 }}
                  />
                ) : null}
                <Circle
                  center={center}
                  radius={(sector.radius_nm ?? 35) * NM_TO_METERS}
                  pathOptions={{
                    color: selectedSectorId === sector.id ? "#8c2f39" : "#5d381d",
                    fillColor: roleColor(sector.role),
                    fillOpacity: selectedSectorId === sector.id ? 0.32 : 0.18,
                    weight: selectedSectorId === sector.id ? 3 : 2,
                  }}
                  eventHandlers={{ click: () => onSectorSelect?.(sector.id) }}
                >
                  <Tooltip permanent direction="center" className="map-tooltip sector-tooltip">
                    <div>
                      <strong>{sector.name ?? sector.id}</strong>
                      <div>{sector.role ?? "sector"}</div>
                      {terrain?.center_elevation_ft_msl ? <div>{Math.round(terrain.center_elevation_ft_msl)} ft MSL</div> : null}
                    </div>
                  </Tooltip>
                </Circle>
              </Pane>
            );
          })}

          {zones.map((zone) => {
            const center = latLng(zone.center_lat, zone.center_lng);
            if (!center) return null;
            return (
              <Circle
                key={zone.id}
                pane="zones"
                center={center}
                radius={(zone.radius_nm ?? 12) * NM_TO_METERS}
                pathOptions={{
                  color: selectedZoneId === zone.id ? "#8c2f39" : "#5d381d",
                  fillColor: "#7390bb",
                  fillOpacity: 0.1,
                  weight: selectedZoneId === zone.id ? 3 : 1.5,
                  dashArray: "6 5",
                }}
                eventHandlers={{ click: () => onZoneSelect?.(zone.id) }}
              >
                <Tooltip permanent direction="top" className="map-tooltip">
                  {zone.name ?? zone.id}
                </Tooltip>
              </Circle>
            );
          })}

          {airPackages.map((airPackage) => {
            const route = (airPackage.route_legs ?? [])
              .filter((leg) => isNumber(leg.lat) && isNumber(leg.lng))
              .map((leg) => [leg.lat as number, leg.lng as number] as LatLngExpression);
            if (!route.length) return null;
            return (
              <Polyline
                key={airPackage.package_id ?? `air-route-${Math.random()}`}
                pane="routes"
                positions={route}
                pathOptions={{
                  color: coalitionColor(airPackage.coalition),
                  weight: selectedAirPackageId === airPackage.package_id ? 5 : 3,
                  opacity: 0.9,
                  dashArray: airPackage.coalition === "blue" ? undefined : "10 6",
                }}
                eventHandlers={{
                  click: () => {
                    if (airPackage.package_id) onAirPackageSelect?.(airPackage.package_id);
                  },
                }}
              />
            );
          })}

          {controlPoints.map((point, index) => {
            const position = controlPointPosition(point);
            const pointId = point.id ?? point.control_point_id;
            if (!position || !pointId) return null;
            return (
              <CircleMarker
                key={`${pointId}-${index}`}
                pane="markers"
                center={position}
                radius={selectedControlPointId === pointId ? 9 : 7}
                pathOptions={{
                  color: "#f8f4ec",
                  fillColor: coalitionColor(point.owner),
                  fillOpacity: 0.94,
                  weight: selectedControlPointId === pointId ? 3 : 2,
                }}
                eventHandlers={{ click: () => onControlPointSelect?.(pointId) }}
              >
                <Tooltip direction="top" className="map-tooltip">
                  <div>
                    <strong>{point.name ?? pointId}</strong>
                    <div>{point.kind ?? "control point"}</div>
                  </div>
                </Tooltip>
              </CircleMarker>
            );
          })}

          {landmarks.map((landmark, index) => {
            const position = latLng(landmark.lat, landmark.lng);
            if (!position) return null;
            return (
              <CircleMarker
                key={landmark.id ?? `landmark-${index}`}
                pane="markers"
                center={position}
                radius={selectedLandmarkId === landmark.id ? 8 : 6}
                pathOptions={{
                  color: "#f7f0df",
                  fillColor: "#2563eb",
                  fillOpacity: 0.95,
                  weight: selectedLandmarkId === landmark.id ? 3 : 2,
                }}
                eventHandlers={{
                  click: () => {
                    if (landmark.id) onLandmarkSelect?.(landmark.id);
                  },
                }}
              >
                <Tooltip direction="top" className="map-tooltip">
                  {landmark.name ?? landmark.id ?? "Landmark"}
                </Tooltip>
              </CircleMarker>
            );
          })}

          {tracks.map((track, index) => {
            const position = trackPosition(track);
            if (!position) return null;
            return (
              <CircleMarker
                key={track.track_id ?? track.id ?? `track-${index}`}
                pane="markers"
                center={position}
                radius={track.stale ? 4 : 5}
                pathOptions={{
                  color: "#ffffff",
                  fillColor: track.inferred ? "#111827" : "#374151",
                  fillOpacity: track.stale ? 0.55 : 0.85,
                  weight: 1.5,
                }}
              >
                <Tooltip className="map-tooltip">Track {track.track_id ?? track.id ?? index + 1}</Tooltip>
              </CircleMarker>
            );
          })}

          {worldGroups.map((group, index) => {
            const position = groupPosition(group);
            if (!position) return null;
            return (
              <CircleMarker
                key={group.id ?? `group-${index}`}
                pane="markers"
                center={position}
                radius={6}
                pathOptions={{
                  color: "#fdf8ef",
                  fillColor: coalitionColor(group.coalition),
                  fillOpacity: 0.92,
                  weight: 1.5,
                }}
              >
                <Tooltip className="map-tooltip">{group.name ?? group.id ?? "Group"}</Tooltip>
              </CircleMarker>
            );
          })}

          <SelectionHandle
            position={selectedSector ? latLng(selectedSector.center_lat, selectedSector.center_lng) : null}
            label="S"
            tone="amber"
            onMove={selectedSectorId ? (coords) => onSectorMove?.(selectedSectorId, coords) : undefined}
          />
          <SelectionHandle
            position={selectedControlPoint ? controlPointPosition(selectedControlPoint) : null}
            label="CP"
            tone="red"
            onMove={selectedControlPointId ? (coords) => onControlPointMove?.(selectedControlPointId, coords) : undefined}
          />
          <SelectionHandle
            position={selectedZone ? latLng(selectedZone.center_lat, selectedZone.center_lng) : null}
            label="Z"
            tone="blue"
            onMove={selectedZoneId ? (coords) => onZoneMove?.(selectedZoneId, coords) : undefined}
          />
          <SelectionHandle
            position={selectedLandmark ? latLng(selectedLandmark.lat, selectedLandmark.lng) : null}
            label="L"
            tone="stone"
            onMove={selectedLandmarkId ? (coords) => onLandmarkMove?.(selectedLandmarkId, coords) : undefined}
          />
        </MapContainer>

        <div className="map-legend">
          <div className="legend-title">Legend</div>
          <div className="legend-row"><span className="legend-swatch front" /> Front / contested sectors</div>
          <div className="legend-row"><span className="legend-swatch blue" /> BLUFOR / safe landmark markers</div>
          <div className="legend-row"><span className="legend-swatch red" /> REDFOR control and route markers</div>
          <div className="legend-row"><span className="legend-swatch terrain" /> Terrain hazard halo</div>
          <div className="legend-row"><span className="legend-line" /> Air package route</div>
          <div className="legend-row"><span className="legend-dot" /> Track / unit marker</div>
        </div>
      </div>
    </div>
  );
}
