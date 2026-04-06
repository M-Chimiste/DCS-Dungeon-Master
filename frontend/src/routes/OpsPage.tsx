import { useEffect, useMemo, useState } from "react";
import { MapCanvas } from "../components/MapCanvas";
import { SummaryCard } from "../components/SummaryCard";
import { api } from "../lib/api";

type LayerState = {
  basemap: boolean;
  sectors: boolean;
  controlPoints: boolean;
  terrain: boolean;
  landmarks: boolean;
  tracks: boolean;
  airPackages: boolean;
  worldTruth: boolean;
  executionNotes: boolean;
  attachments: boolean;
};

type CatalogEntry = {
  catalog_id: string;
  display_name: string;
  backend_name: string;
  server_label: string;
  endpoint: string;
  model: string;
  hosting_mode: string;
  supports_structured_output: boolean;
  supports_multimodal: boolean;
  enabled: boolean;
  hidden: boolean;
  tags: string[];
};

type RouteLegDraft = {
  leg_id: string;
  reference_type: "sector" | "control_point" | "zone" | "landmark" | "coordinate";
  reference_id?: string;
  lat?: number;
  lng?: number;
  altitude_ft_msl: number;
  task?: string;
  note?: string;
};

type AirActionDraft = {
  coalition: "red" | "blue";
  actionType: "launch_air_package" | "retask_air_package";
  inventoryId: string;
  packageId: string;
  packageType: string;
  aircraftCount: number;
  posture: string;
  roe: string;
  targetReferenceType: string;
  targetReferenceId: string;
  routeLegs: RouteLegDraft[];
  presetName: string;
};

type ScenarioPayload = {
  scenario: any;
  map_reference: any;
};

const DEFAULT_LAYERS: LayerState = {
  basemap: true,
  sectors: true,
  controlPoints: true,
  terrain: true,
  landmarks: true,
  tracks: true,
  airPackages: true,
  worldTruth: true,
  executionNotes: true,
  attachments: true,
};

const DEFAULT_ADHOC = {
  display_name: "",
  endpoint: "",
  model: "",
  hosting_mode: "hosted",
  multimodal: false,
};

const emptyDraft = (): AirActionDraft => ({
  coalition: "red",
  actionType: "launch_air_package",
  inventoryId: "",
  packageId: "",
  packageType: "cap",
  aircraftCount: 2,
  posture: "push",
  roe: "tight",
  targetReferenceType: "",
  targetReferenceId: "",
  routeLegs: [],
  presetName: "",
});

const labelize = (value: string) => value.replace(/_/g, " ");

export function OpsPage() {
  const [runs, setRuns] = useState<any[]>([]);
  const [resumableRuns, setResumableRuns] = useState<any[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [scenarios, setScenarios] = useState<any[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>("");
  const [scenarioPayload, setScenarioPayload] = useState<ScenarioPayload | null>(null);
  const [ops, setOps] = useState<any | null>(null);
  const [redPreview, setRedPreview] = useState<any | null>(null);
  const [bluePreview, setBluePreview] = useState<any | null>(null);
  const [status, setStatus] = useState<string>("Loading operations console...");
  const [layers, setLayers] = useState<LayerState>(DEFAULT_LAYERS);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [sameForBoth, setSameForBoth] = useState(true);
  const [mode, setMode] = useState("dry");
  const [sharedPrimaryCatalogId, setSharedPrimaryCatalogId] = useState("");
  const [sharedFallbackCatalogId, setSharedFallbackCatalogId] = useState("");
  const [redPrimaryCatalogId, setRedPrimaryCatalogId] = useState("");
  const [bluePrimaryCatalogId, setBluePrimaryCatalogId] = useState("");
  const [redFallbackCatalogId, setRedFallbackCatalogId] = useState("");
  const [blueFallbackCatalogId, setBlueFallbackCatalogId] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [redPrimaryAdhoc, setRedPrimaryAdhoc] = useState(DEFAULT_ADHOC);
  const [bluePrimaryAdhoc, setBluePrimaryAdhoc] = useState(DEFAULT_ADHOC);
  const [selectedPackageId, setSelectedPackageId] = useState<string>("");
  const [selectedSectorId, setSelectedSectorId] = useState<string>("");
  const [selectedControlPointId, setSelectedControlPointId] = useState<string>("");
  const [selectedZoneId, setSelectedZoneId] = useState<string>("");
  const [selectedLandmarkId, setSelectedLandmarkId] = useState<string>("");
  const [captureWaypoint, setCaptureWaypoint] = useState(false);
  const [draft, setDraft] = useState<AirActionDraft>(emptyDraft());
  const [operatorResult, setOperatorResult] = useState<any | null>(null);

  const loadRuns = async () => {
    const [runsPayload, resumablePayload] = await Promise.all([
      api<{ runs: any[] }>("/api/runs"),
      api<{ runs: any[] }>("/api/runs/resumable"),
    ]);
    setRuns(runsPayload.runs);
    setResumableRuns(resumablePayload.runs);
    setSelectedRunId((current) => current || runsPayload.runs[0]?.run_id || "");
  };

  useEffect(() => {
    Promise.all([
      api<{ runs: any[] }>("/api/runs"),
      api<{ runs: any[] }>("/api/runs/resumable"),
      api<{ catalog: CatalogEntry[] }>("/api/model-catalog"),
      api<{ scenarios: any[] }>("/api/scenarios"),
    ])
      .then(([runsPayload, resumablePayload, catalogPayload, scenariosPayload]) => {
        setRuns(runsPayload.runs);
        setResumableRuns(resumablePayload.runs);
        setCatalog(catalogPayload.catalog);
        setScenarios(scenariosPayload.scenarios);
        setSelectedRunId(runsPayload.runs[0]?.run_id ?? "");
        setSelectedScenarioId(scenariosPayload.scenarios[0]?.id ?? "");
        setSharedPrimaryCatalogId(catalogPayload.catalog[0]?.catalog_id ?? "");
        setStatus("Run setup ready.");
      })
      .catch((error) => setStatus(String(error)));
  }, []);

  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    let timer: number | null = null;

    const refresh = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const [opsPayload, redPayload, bluePayload] = await Promise.all([
          api<any>(`/api/runs/${selectedRunId}/ops`),
          api<any>(`/api/runs/${selectedRunId}/commander-preview/red`),
          api<any>(`/api/runs/${selectedRunId}/commander-preview/blue`),
        ]);
        if (cancelled) return;
        setOps(opsPayload.ops);
        setRedPreview(redPayload.preview);
        setBluePreview(bluePayload.preview);
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (error) {
        if (!cancelled) setStatus(String(error));
      }
    };

    const startPolling = () => {
      if (timer !== null) window.clearInterval(timer);
      timer = window.setInterval(refresh, 2000);
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        refresh();
        startPolling();
      } else if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };

    refresh();
    startPolling();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [selectedRunId]);

  useEffect(() => {
    const scenarioId = ops?.run?.scenario_id;
    if (!scenarioId) return;
    api<ScenarioPayload>(`/api/scenarios/${scenarioId}`)
      .then((payload) => {
        setScenarioPayload(payload);
        setSelectedScenarioId(scenarioId);
      })
      .catch((error) => setStatus(String(error)));
  }, [ops?.run?.scenario_id]);

  const scenario = scenarioPayload?.scenario ?? null;
  const inventories = scenario?.air_package_inventories ?? [];
  const presets = scenario?.air_package_presets ?? [];
  const selectedInventory = inventories.find((item: any) => item.id === draft.inventoryId) ?? null;
  const selectedPackage =
    (ops?.operator_map_layers?.air_packages ?? []).find((item: any) => item.package_id === selectedPackageId) ?? null;
  const executionNotes = ops?.operator_map_layers?.current_execution_notes ?? [];
  const selectedPackageNotes = executionNotes.filter((item: any) => item.entity_id === selectedPackageId);

  useEffect(() => {
    if (!inventories.length) return;
    setDraft((current) => ({
      ...current,
      inventoryId: current.inventoryId || inventories.find((item: any) => item.coalition === current.coalition)?.id || inventories[0].id,
    }));
  }, [inventories]);

  const visibleTracks = useMemo(() => {
    if (!layers.tracks) return [];
    return [
      ...(ops?.operator_map_layers?.red_knowledge_tracks ?? []),
      ...(ops?.operator_map_layers?.blue_knowledge_tracks ?? []),
    ];
  }, [layers.tracks, ops]);

  const visibleControlPoints = layers.controlPoints ? ops?.operator_map_layers?.control_points ?? [] : [];
  const visibleSectors = layers.sectors ? ops?.operator_map_layers?.sectors ?? [] : [];
  const visibleZones = ops?.operator_map_layers?.zones ?? [];
  const visibleWorldGroups = layers.worldTruth ? ops?.operator_map_layers?.world_groups ?? [] : [];
  const visibleAirPackages = layers.airPackages ? ops?.operator_map_layers?.air_packages ?? [] : [];
  const visibleLandmarks = layers.landmarks ? ops?.operator_map_layers?.landmarks ?? [] : [];
  const visibleTerrain = layers.terrain ? ops?.operator_map_layers?.terrain_summary ?? [] : [];
  const visibleBasemap = layers.basemap ? ops?.operator_map_layers?.basemap ?? null : null;
  const latestRed = ops?.red_inspection?.latest_model_invocation;
  const latestBlue = ops?.blue_inspection?.latest_model_invocation;

  const refreshNow = async () => {
    if (!selectedRunId) return;
    setStatus("Refreshing live ops snapshot...");
    try {
      const [opsPayload, redPayload, bluePayload] = await Promise.all([
        api<any>(`/api/runs/${selectedRunId}/ops`),
        api<any>(`/api/runs/${selectedRunId}/commander-preview/red`),
        api<any>(`/api/runs/${selectedRunId}/commander-preview/blue`),
      ]);
      setOps(opsPayload.ops);
      setRedPreview(redPayload.preview);
      setBluePreview(bluePayload.preview);
      setLastUpdated(new Date().toLocaleTimeString());
      setStatus("Live ops snapshot refreshed.");
    } catch (error) {
      setStatus(String(error));
    }
  };

  const toggleLayer = (key: keyof LayerState) => {
    setLayers((current) => ({ ...current, [key]: !current[key] }));
  };

  const createRun = async () => {
    if (!selectedScenarioId || !sharedPrimaryCatalogId) return;
    setStatus("Creating run...");
    try {
      const payload = await api<any>("/api/runs", {
        method: "POST",
        body: JSON.stringify({
          scenario_id: selectedScenarioId,
          mode,
          routing: {
            same_for_both: sameForBoth,
            shared_primary_catalog_id: sharedPrimaryCatalogId,
            shared_fallback_catalog_id: sharedFallbackCatalogId || null,
            red_primary_catalog_id: redPrimaryCatalogId || null,
            blue_primary_catalog_id: bluePrimaryCatalogId || null,
            red_fallback_catalog_id: redFallbackCatalogId || null,
            blue_fallback_catalog_id: blueFallbackCatalogId || null,
            red_primary_adhoc:
              showAdvanced && redPrimaryAdhoc.endpoint && redPrimaryAdhoc.model ? redPrimaryAdhoc : null,
            blue_primary_adhoc:
              showAdvanced && bluePrimaryAdhoc.endpoint && bluePrimaryAdhoc.model ? bluePrimaryAdhoc : null,
          },
        }),
      });
      await loadRuns();
      setSelectedRunId(payload.run.run_id);
      setStatus("Run created.");
    } catch (error) {
      setStatus(String(error));
    }
  };

  const openLatestRun = async () => {
    setStatus("Opening latest run...");
    try {
      const payload = await api<any>("/api/runs/latest");
      setSelectedRunId(payload.run.run.run_id);
      setStatus(`Opened latest run ${payload.run.run.run_id}.`);
    } catch (error) {
      setStatus(String(error));
    }
  };

  const continueSelectedRun = async () => {
    if (!selectedRunId) return;
    setStatus("Continuing selected run...");
    try {
      const payload = await api<any>(`/api/runs/${selectedRunId}/continue`, { method: "POST" });
      setSelectedRunId(payload.run.run.run_id);
      await loadRuns();
      setStatus(`Run ${payload.run.run.run_id} is ready to continue.`);
    } catch (error) {
      setStatus(String(error));
    }
  };

  const resetSelections = () => {
    setSelectedSectorId("");
    setSelectedControlPointId("");
    setSelectedZoneId("");
    setSelectedLandmarkId("");
  };

  const appendRouteLeg = (leg: Partial<RouteLegDraft>) => {
    const nextLeg: RouteLegDraft = {
      leg_id: `leg_${draft.routeLegs.length + 1}`,
      reference_type: "coordinate",
      altitude_ft_msl: selectedInventory?.default_altitude_ft_msl ?? 18000,
      ...leg,
    } as RouteLegDraft;
    setDraft((current) => ({ ...current, routeLegs: [...current.routeLegs, nextLeg] }));
  };

  const addSelectedReferenceLeg = () => {
    if (selectedControlPointId) {
      appendRouteLeg({ reference_type: "control_point", reference_id: selectedControlPointId });
      return;
    }
    if (selectedZoneId) {
      appendRouteLeg({ reference_type: "zone", reference_id: selectedZoneId });
      return;
    }
    if (selectedSectorId) {
      appendRouteLeg({ reference_type: "sector", reference_id: selectedSectorId });
      return;
    }
    if (selectedLandmarkId) {
      appendRouteLeg({ reference_type: "landmark", reference_id: selectedLandmarkId });
      return;
    }
    setStatus("Select a sector, control point, zone, or landmark on the map first.");
  };

  const moveLeg = (index: number, direction: -1 | 1) => {
    setDraft((current) => {
      const next = [...current.routeLegs];
      const target = index + direction;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return { ...current, routeLegs: next };
    });
  };

  const updateLeg = (index: number, patch: Partial<RouteLegDraft>) => {
    setDraft((current) => ({
      ...current,
      routeLegs: current.routeLegs.map((leg, legIndex) => (legIndex === index ? { ...leg, ...patch } : leg)),
    }));
  };

  const deleteLeg = (index: number) => {
    setDraft((current) => ({
      ...current,
      routeLegs: current.routeLegs.filter((_, legIndex) => legIndex !== index),
    }));
  };

  const hydrateFromPreset = (presetId: string) => {
    const preset = presets.find((item: any) => item.id === presetId);
    if (!preset) return;
    setDraft((current) => ({
      ...current,
      coalition: preset.coalition,
      inventoryId: preset.inventory_id,
      packageType: preset.package_type,
      aircraftCount: preset.aircraft_count,
      posture: preset.posture ?? "push",
      roe: preset.roe ?? "tight",
      targetReferenceType: preset.target_reference_type ?? "",
      targetReferenceId: preset.target_reference_id ?? "",
      routeLegs: (preset.route_legs ?? []).map((leg: any, index: number) => ({
        leg_id: leg.leg_id ?? `leg_${index + 1}`,
        reference_type: leg.reference_type,
        reference_id: leg.reference_id,
        lat: leg.lat,
        lng: leg.lng,
        altitude_ft_msl: leg.altitude_ft_msl ?? selectedInventory?.default_altitude_ft_msl ?? 18000,
        task: leg.task ?? "",
        note: leg.note ?? "",
      })),
      presetName: preset.name,
    }));
    setStatus(`Loaded preset ${preset.name}.`);
  };

  const hydrateFromPackage = () => {
    if (!selectedPackage) return;
    setDraft((current) => ({
      ...current,
      coalition: selectedPackage.coalition,
      actionType: "retask_air_package",
      packageId: selectedPackage.package_id,
      packageType: selectedPackage.package_type,
      posture: selectedPackage.posture,
      roe: selectedPackage.roe,
      routeLegs: (selectedPackage.route_legs ?? []).map((leg: any, index: number) => ({
        leg_id: leg.leg_id ?? `leg_${index + 1}`,
        reference_type: leg.reference_type,
        reference_id: leg.reference_id,
        lat: leg.lat,
        lng: leg.lng,
        altitude_ft_msl: leg.altitude_ft_msl ?? 18000,
        task: leg.task ?? "",
        note: leg.note ?? "",
      })),
    }));
    setStatus(`Loaded package ${selectedPackage.package_id} into the retask form.`);
  };

  const savePreset = async () => {
    if (!selectedScenarioId || !draft.presetName || !draft.inventoryId) {
      setStatus("Preset name and inventory are required.");
      return;
    }
    try {
      const payload = await api<any>(`/api/scenarios/${selectedScenarioId}/air-package-presets`, {
        method: "POST",
        body: JSON.stringify({
          preset: {
            coalition: draft.coalition,
            name: draft.presetName,
            inventory_id: draft.inventoryId,
            package_type: draft.packageType,
            aircraft_count: draft.aircraftCount,
            route_legs: draft.routeLegs,
            target_reference_type: draft.targetReferenceType || null,
            target_reference_id: draft.targetReferenceId || null,
            posture: draft.posture,
            roe: draft.roe,
          },
        }),
      });
      setScenarioPayload((current) => (current ? { ...current, scenario: payload.scenario } : current));
      setStatus(`Saved preset ${payload.preset.name}.`);
    } catch (error) {
      setStatus(String(error));
    }
  };

  const submitAction = async (actionOverride?: any) => {
    if (!selectedRunId) return;
    const action =
      actionOverride ??
      (draft.actionType === "launch_air_package"
        ? {
            action_id: `ui_launch_${Date.now()}`,
            action_type: "launch_air_package",
            coalition: draft.coalition,
            reason: "Operator-authored package launch from Ops UI.",
            inventory_id: draft.inventoryId,
            package_type: draft.packageType,
            aircraft_count: draft.aircraftCount,
            route_legs: draft.routeLegs,
            target_reference_type: draft.targetReferenceType || null,
            target_reference_id: draft.targetReferenceId || null,
            posture: draft.posture,
            roe: draft.roe,
          }
        : {
            action_id: `ui_retask_${Date.now()}`,
            action_type: "retask_air_package",
            coalition: draft.coalition,
            reason: "Operator-authored package retask from Ops UI.",
            package_id: draft.packageId,
            route_legs: draft.routeLegs,
            target_reference_type: draft.targetReferenceType || null,
            target_reference_id: draft.targetReferenceId || null,
            posture: draft.posture,
            roe: draft.roe,
          });
    try {
      const payload = await api<any>(`/api/runs/${selectedRunId}/operator-actions`, {
        method: "POST",
        body: JSON.stringify({
          coalition: action.coalition,
          actions: [action],
        }),
      });
      setOperatorResult(payload);
      setOps(payload.ops);
      setStatus("Operator action submitted.");
      setLastUpdated(new Date().toLocaleTimeString());
      await refreshNow();
    } catch (error) {
      setStatus(String(error));
    }
  };

  const submitQuickAction = async (actionType: string, fields: Record<string, unknown>) => {
    if (!selectedPackage) return;
    await submitAction({
      action_id: `ui_${actionType}_${Date.now()}`,
      action_type: actionType,
      coalition: selectedPackage.coalition,
      reason: `Operator ${labelize(actionType)} from Ops UI.`,
      package_id: selectedPackage.package_id,
      ...fields,
    });
  };

  const packageGroups = useMemo(() => {
    const grouped = { red: [] as any[], blue: [] as any[] };
    for (const item of ops?.operator_map_layers?.air_packages ?? []) {
      if (item.coalition === "blue") grouped.blue.push(item);
      else grouped.red.push(item);
    }
    return grouped;
  }, [ops]);

  return (
    <div className="grid ops-grid ops-grid-expanded">
      <aside className="rail">
        <div className="panel">
          <h2>Continue Existing Run</h2>
          <p className="muted">Resume a persisted harness-managed run before creating a new one.</p>
          <div className="button-row">
            <button onClick={openLatestRun}>Open Latest</button>
            <button onClick={continueSelectedRun} disabled={!selectedRunId}>
              Continue Selected
            </button>
          </div>
          <div className="stack">
            {resumableRuns.length ? (
              resumableRuns.map((run) => (
                <button
                  key={`resumable-${run.run_id}`}
                  className={run.run_id === selectedRunId ? "list-card active" : "list-card"}
                  onClick={() => setSelectedRunId(run.run_id)}
                >
                  <strong>{run.scenario_name}</strong>
                  <span>
                    {run.mode} / {run.status}
                  </span>
                  <span>Cycle {run.latest_decision_cycle}</span>
                </button>
              ))
            ) : (
              <p className="muted">No resumable runs yet.</p>
            )}
          </div>
        </div>

        <div className="panel">
          <h2>Run Setup</h2>
          <p className="muted">{status}</p>
          <label className="field">
            <span>Scenario</span>
            <select value={selectedScenarioId} onChange={(event) => setSelectedScenarioId(event.target.value)}>
              {scenarios.map((scenario) => (
                <option key={scenario.id} value={scenario.id}>
                  {scenario.name} ({scenario.theater})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Mode</span>
            <select value={mode} onChange={(event) => setMode(event.target.value)}>
              <option value="dry">Dry</option>
              <option value="live">Live</option>
            </select>
          </label>
          <label className="checkbox-pill">
            <input type="checkbox" checked={sameForBoth} onChange={() => setSameForBoth((current) => !current)} />
            Use same model for both sides
          </label>
          <label className="field">
            <span>Shared Primary</span>
            <select value={sharedPrimaryCatalogId} onChange={(event) => setSharedPrimaryCatalogId(event.target.value)}>
              {catalog.map((entry) => (
                <option key={entry.catalog_id} value={entry.catalog_id}>
                  {entry.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Shared Fallback</span>
            <select value={sharedFallbackCatalogId} onChange={(event) => setSharedFallbackCatalogId(event.target.value)}>
              <option value="">None</option>
              {catalog.map((entry) => (
                <option key={entry.catalog_id} value={entry.catalog_id}>
                  {entry.display_name}
                </option>
              ))}
            </select>
          </label>
          {!sameForBoth ? (
            <div className="stack">
              <label className="field">
                <span>REDFOR Primary</span>
                <select value={redPrimaryCatalogId} onChange={(event) => setRedPrimaryCatalogId(event.target.value)}>
                  <option value="">Use shared</option>
                  {catalog.map((entry) => (
                    <option key={entry.catalog_id} value={entry.catalog_id}>
                      {entry.display_name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>BLUFOR Primary</span>
                <select value={bluePrimaryCatalogId} onChange={(event) => setBluePrimaryCatalogId(event.target.value)}>
                  <option value="">Use shared</option>
                  {catalog.map((entry) => (
                    <option key={entry.catalog_id} value={entry.catalog_id}>
                      {entry.display_name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}
          <div className="button-row">
            <button onClick={() => setShowAdvanced((current) => !current)}>
              {showAdvanced ? "Hide Advanced" : "Advanced Ad-hoc"}
            </button>
            <button onClick={createRun}>Create Run</button>
          </div>
          {showAdvanced ? (
            <div className="stack">
              <h3>REDFOR Ad-hoc Primary</h3>
              <input
                placeholder="Display name"
                value={redPrimaryAdhoc.display_name}
                onChange={(event) => setRedPrimaryAdhoc((current) => ({ ...current, display_name: event.target.value }))}
              />
              <input
                placeholder="Endpoint URL"
                value={redPrimaryAdhoc.endpoint}
                onChange={(event) => setRedPrimaryAdhoc((current) => ({ ...current, endpoint: event.target.value }))}
              />
              <input
                placeholder="Model id"
                value={redPrimaryAdhoc.model}
                onChange={(event) => setRedPrimaryAdhoc((current) => ({ ...current, model: event.target.value }))}
              />
              <h3>BLUFOR Ad-hoc Primary</h3>
              <input
                placeholder="Display name"
                value={bluePrimaryAdhoc.display_name}
                onChange={(event) => setBluePrimaryAdhoc((current) => ({ ...current, display_name: event.target.value }))}
              />
              <input
                placeholder="Endpoint URL"
                value={bluePrimaryAdhoc.endpoint}
                onChange={(event) => setBluePrimaryAdhoc((current) => ({ ...current, endpoint: event.target.value }))}
              />
              <input
                placeholder="Model id"
                value={bluePrimaryAdhoc.model}
                onChange={(event) => setBluePrimaryAdhoc((current) => ({ ...current, model: event.target.value }))}
              />
            </div>
          ) : null}
        </div>

        <div className="panel">
          <h2>Runs</h2>
          <div className="stack">
            {runs.map((run) => (
              <button
                key={run.run_id}
                className={run.run_id === selectedRunId ? "list-card active" : "list-card"}
                onClick={() => setSelectedRunId(run.run_id)}
              >
                <strong>{run.scenario_name}</strong>
                <span>
                  {run.mode} / {run.status}
                </span>
                <span>
                  RED: {run.red_backend_name || "none"} | BLUE: {run.blue_backend_name || "none"}
                </span>
              </button>
            ))}
          </div>
        </div>
      </aside>

      <section className="canvas-column">
        <MapCanvas
          title="Operator Console"
          sectors={visibleSectors}
          controlPoints={visibleControlPoints}
          zones={visibleZones}
          tracks={visibleTracks}
          worldGroups={visibleWorldGroups}
          airPackages={visibleAirPackages}
          landmarks={visibleLandmarks}
          terrainSummary={visibleTerrain}
          basemap={visibleBasemap}
          selectedSectorId={selectedSectorId}
          selectedControlPointId={selectedControlPointId}
          selectedZoneId={selectedZoneId}
          selectedLandmarkId={selectedLandmarkId}
          selectedAirPackageId={selectedPackageId}
          referenceCenterLat={scenarioPayload?.map_reference?.default_center_lat}
          referenceCenterLng={scenarioPayload?.map_reference?.default_center_lng}
          referenceRadiusNm={scenarioPayload?.map_reference?.default_radius_nm}
          onSectorSelect={(sectorId) => {
            resetSelections();
            setSelectedSectorId(sectorId);
          }}
          onControlPointSelect={(controlPointId) => {
            resetSelections();
            setSelectedControlPointId(controlPointId);
          }}
          onZoneSelect={(zoneId) => {
            resetSelections();
            setSelectedZoneId(zoneId);
          }}
          onLandmarkSelect={(landmarkId) => {
            resetSelections();
            setSelectedLandmarkId(landmarkId);
          }}
          onAirPackageSelect={(packageId) => setSelectedPackageId(packageId)}
          onMapClick={(coords) => {
            if (!captureWaypoint) return;
            appendRouteLeg({ reference_type: "coordinate", lat: coords.lat, lng: coords.lng });
          }}
        />

        <div className="summary-grid">
          <SummaryCard title="Cycle" value={ops?.summary?.latest_decision_cycle ?? 0} />
          <SummaryCard title="Timeline" value={ops?.timeline?.length ?? 0} />
          <SummaryCard title="RED Proposal" value={latestRed?.status ?? "none"} detail={latestRed?.backend_name ?? "No model response"} />
          <SummaryCard title="BLUE Proposal" value={latestBlue?.status ?? "none"} detail={latestBlue?.backend_name ?? "No model response"} />
          <SummaryCard title="RED Execution" value={ops?.red_inspection?.latest_execution?.status ?? "none"} />
          <SummaryCard title="BLUE Execution" value={ops?.blue_inspection?.latest_execution?.status ?? "none"} />
        </div>

        <div className="preview-row">
          <section className="panel preview-panel">
            <h3>REDFOR Commander Preview</h3>
            <p>{redPreview?.narrative ?? "No RED observation yet."}</p>
            <div className="summary-grid">
              <SummaryCard title="Contacts" value={redPreview?.observation?.enemy_contacts?.length ?? 0} />
              <SummaryCard title="Friendlies" value={redPreview?.observation?.friendly_forces?.length ?? 0} />
              <SummaryCard title="Changes" value={redPreview?.observation?.recent_changes?.length ?? 0} />
            </div>
            {redPreview?.map_image_uris?.length ? (
              <div className="preview-image-grid">
                {redPreview.map_image_uris.slice(0, 3).map((uri: string, index: number) => (
                  <figure key={uri} className="preview-image-card">
                    <img src={uri} alt="REDFOR map context" />
                    <figcaption>{index === 0 ? "Theater" : `AOI ${index}`}</figcaption>
                  </figure>
                ))}
              </div>
            ) : null}
          </section>
          <section className="panel preview-panel">
            <h3>BLUFOR Commander Preview</h3>
            <p>{bluePreview?.narrative ?? "No BLUE observation yet."}</p>
            <div className="summary-grid">
              <SummaryCard title="Contacts" value={bluePreview?.observation?.enemy_contacts?.length ?? 0} />
              <SummaryCard title="Friendlies" value={bluePreview?.observation?.friendly_forces?.length ?? 0} />
              <SummaryCard title="Changes" value={bluePreview?.observation?.recent_changes?.length ?? 0} />
            </div>
            {bluePreview?.map_image_uris?.length ? (
              <div className="preview-image-grid">
                {bluePreview.map_image_uris.slice(0, 3).map((uri: string, index: number) => (
                  <figure key={uri} className="preview-image-card">
                    <img src={uri} alt="BLUFOR map context" />
                    <figcaption>{index === 0 ? "Theater" : `AOI ${index}`}</figcaption>
                  </figure>
                ))}
              </div>
            ) : null}
          </section>
        </div>
      </section>

      <aside className="rail">
        <div className="panel">
          <h2>Operator Snapshot</h2>
          <div className="button-row">
            <button onClick={refreshNow}>Refresh Now</button>
            <span className="state-badge saved">{lastUpdated ? `Updated ${lastUpdated}` : "Waiting..."}</span>
          </div>
          <div className="checkbox-grid">
            {Object.entries(layers).map(([key, enabled]) => (
              <label key={key} className="checkbox-pill">
                <input type="checkbox" checked={enabled} onChange={() => toggleLayer(key as keyof LayerState)} />
                {labelize(key)}
              </label>
            ))}
          </div>
        </div>

        <div className="panel">
          <h2>Air Packages</h2>
          <div className="package-group">
            <h3>REDFOR</h3>
            <div className="stack">
              {packageGroups.red.map((item) => (
                <button
                  key={item.package_id}
                  className={item.package_id === selectedPackageId ? "list-card active" : "list-card"}
                  onClick={() => setSelectedPackageId(item.package_id)}
                >
                  <strong>{item.package_id}</strong>
                  <span>
                    {item.package_type} / {item.status}
                  </span>
                  <span>
                    {item.posture} / {item.roe}
                  </span>
                </button>
              ))}
            </div>
          </div>
          <div className="package-group">
            <h3>BLUFOR</h3>
            <div className="stack">
              {packageGroups.blue.map((item) => (
                <button
                  key={item.package_id}
                  className={item.package_id === selectedPackageId ? "list-card active" : "list-card"}
                  onClick={() => setSelectedPackageId(item.package_id)}
                >
                  <strong>{item.package_id}</strong>
                  <span>
                    {item.package_type} / {item.status}
                  </span>
                  <span>
                    {item.posture} / {item.roe}
                  </span>
                </button>
              ))}
            </div>
          </div>
          {selectedPackage ? (
            <div className="inset-panel package-detail-panel">
              <h3>Selected Package</h3>
              <p className="muted">
                {selectedPackage.package_type} from {selectedPackage.origin_control_point_id}
              </p>
              <div className="compact-list">
                {(selectedPackage.route_legs ?? []).map((leg: any) => (
                  <div key={leg.leg_id} className="list-row">
                    <span>{leg.reference_type}</span>
                    <span>{leg.reference_id ?? `${leg.lat?.toFixed?.(2)}, ${leg.lng?.toFixed?.(2)}`}</span>
                    <span>{leg.altitude_ft_msl ?? "?"} ft</span>
                  </div>
                ))}
              </div>
              <div className="button-row wrap-row">
                <button onClick={hydrateFromPackage}>Retask Form</button>
                <button onClick={() => submitQuickAction("abort_air_package", {})}>Abort</button>
                <button onClick={() => submitQuickAction("set_air_package_posture", { posture: "defensive" })}>Defensive</button>
                <button onClick={() => submitQuickAction("set_air_package_roe", { roe: "hold" })}>ROE Hold</button>
              </div>
              {selectedPackageNotes.length ? (
                <div className="stack">
                  {selectedPackageNotes.slice(0, 3).map((note: any) => (
                    <div key={`${note.entity_id}-${note.updated_at}`} className="callout-note">
                      <strong>{note.metadata?.status ?? note.lifecycle_state}</strong>
                      <div>{note.note}</div>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <p className="muted">Select an air package to inspect routes and quick actions.</p>
          )}
        </div>

        <div className="panel">
          <h2>Author Air Package</h2>
          <label className="field">
            <span>Coalition</span>
            <select value={draft.coalition} onChange={(event) => setDraft((current) => ({ ...current, coalition: event.target.value as "red" | "blue" }))}>
              <option value="red">REDFOR</option>
              <option value="blue">BLUFOR</option>
            </select>
          </label>
          <label className="field">
            <span>Action</span>
            <select
              value={draft.actionType}
              onChange={(event) => setDraft((current) => ({ ...current, actionType: event.target.value as AirActionDraft["actionType"] }))}
            >
              <option value="launch_air_package">Launch Package</option>
              <option value="retask_air_package">Retask Package</option>
            </select>
          </label>
          {draft.actionType === "launch_air_package" ? (
            <label className="field">
              <span>Inventory</span>
              <select value={draft.inventoryId} onChange={(event) => setDraft((current) => ({ ...current, inventoryId: event.target.value }))}>
                <option value="">Select inventory</option>
                {inventories
                  .filter((item: any) => item.coalition === draft.coalition)
                  .map((item: any) => (
                    <option key={item.id} value={item.id}>
                      {item.aircraft_type} / {item.origin_control_point_id} ({item.available_count})
                    </option>
                  ))}
              </select>
            </label>
          ) : (
            <label className="field">
              <span>Package</span>
              <select value={draft.packageId} onChange={(event) => setDraft((current) => ({ ...current, packageId: event.target.value }))}>
                <option value="">Select package</option>
                {(ops?.operator_map_layers?.air_packages ?? [])
                  .filter((item: any) => item.coalition === draft.coalition)
                  .map((item: any) => (
                    <option key={item.package_id} value={item.package_id}>
                      {item.package_id} ({item.package_type})
                    </option>
                  ))}
              </select>
            </label>
          )}
          <div className="grid two-col-grid">
            <label className="field">
              <span>Package Type</span>
              <select value={draft.packageType} onChange={(event) => setDraft((current) => ({ ...current, packageType: event.target.value }))}>
                <option value="cap">CAP</option>
                <option value="strike">Strike</option>
                <option value="sead">SEAD</option>
                <option value="escort">Escort</option>
                <option value="transport">Transport</option>
              </select>
            </label>
            <label className="field">
              <span>Aircraft Count</span>
              <input
                type="number"
                min={1}
                value={draft.aircraftCount}
                onChange={(event) => setDraft((current) => ({ ...current, aircraftCount: Number(event.target.value) }))}
              />
            </label>
          </div>
          <div className="grid two-col-grid">
            <label className="field">
              <span>Posture</span>
              <select value={draft.posture} onChange={(event) => setDraft((current) => ({ ...current, posture: event.target.value }))}>
                <option value="push">Push</option>
                <option value="hold">Hold</option>
                <option value="egress">Egress</option>
                <option value="defensive">Defensive</option>
              </select>
            </label>
            <label className="field">
              <span>ROE</span>
              <select value={draft.roe} onChange={(event) => setDraft((current) => ({ ...current, roe: event.target.value }))}>
                <option value="tight">Tight</option>
                <option value="hold">Hold</option>
                <option value="free">Free</option>
              </select>
            </label>
          </div>
          <div className="grid two-col-grid">
            <label className="field">
              <span>Target Type</span>
              <select value={draft.targetReferenceType} onChange={(event) => setDraft((current) => ({ ...current, targetReferenceType: event.target.value }))}>
                <option value="">None</option>
                <option value="sector">Sector</option>
                <option value="control_point">Control Point</option>
                <option value="zone">Zone</option>
                <option value="landmark">Landmark</option>
                <option value="contact">Visible Contact</option>
              </select>
            </label>
            <label className="field">
              <span>Target Reference</span>
              <input value={draft.targetReferenceId} onChange={(event) => setDraft((current) => ({ ...current, targetReferenceId: event.target.value }))} />
            </label>
          </div>
          <div className="button-row wrap-row">
            <button onClick={() => setCaptureWaypoint((current) => !current)}>
              {captureWaypoint ? "Stop Map Capture" : "Capture Map Clicks"}
            </button>
            <button onClick={addSelectedReferenceLeg}>Add Selected Feature</button>
            <button onClick={() => setDraft(emptyDraft())}>Reset</button>
          </div>
          <div className="inset-panel">
            <h3>Route Legs</h3>
            <div className="stack">
              {draft.routeLegs.map((leg, index) => (
                <div key={`${leg.leg_id}-${index}`} className="route-leg-card">
                  <div className="grid two-col-grid">
                    <label className="field">
                      <span>Reference Type</span>
                      <select
                        value={leg.reference_type}
                        onChange={(event) => updateLeg(index, { reference_type: event.target.value as RouteLegDraft["reference_type"] })}
                      >
                        <option value="sector">Sector</option>
                        <option value="control_point">Control Point</option>
                        <option value="zone">Zone</option>
                        <option value="landmark">Landmark</option>
                        <option value="coordinate">Coordinate</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>Altitude (ft MSL)</span>
                      <input
                        type="number"
                        value={leg.altitude_ft_msl}
                        onChange={(event) => updateLeg(index, { altitude_ft_msl: Number(event.target.value) })}
                      />
                    </label>
                  </div>
                  <div className="grid two-col-grid">
                    <label className="field">
                      <span>Reference ID</span>
                      <input value={leg.reference_id ?? ""} onChange={(event) => updateLeg(index, { reference_id: event.target.value })} />
                    </label>
                    <label className="field">
                      <span>Task</span>
                      <input value={leg.task ?? ""} onChange={(event) => updateLeg(index, { task: event.target.value })} />
                    </label>
                  </div>
                  {leg.reference_type === "coordinate" ? (
                    <div className="grid two-col-grid">
                      <label className="field">
                        <span>Lat</span>
                        <input type="number" value={leg.lat ?? 0} onChange={(event) => updateLeg(index, { lat: Number(event.target.value) })} />
                      </label>
                      <label className="field">
                        <span>Lng</span>
                        <input type="number" value={leg.lng ?? 0} onChange={(event) => updateLeg(index, { lng: Number(event.target.value) })} />
                      </label>
                    </div>
                  ) : null}
                  <label className="field">
                    <span>Note</span>
                    <input value={leg.note ?? ""} onChange={(event) => updateLeg(index, { note: event.target.value })} />
                  </label>
                  <div className="button-row">
                    <button onClick={() => moveLeg(index, -1)} disabled={index === 0}>
                      Up
                    </button>
                    <button onClick={() => moveLeg(index, 1)} disabled={index === draft.routeLegs.length - 1}>
                      Down
                    </button>
                    <button onClick={() => deleteLeg(index)}>Delete</button>
                  </div>
                </div>
              ))}
              {!draft.routeLegs.length ? <p className="muted">Capture map clicks or add selected map features to build a route.</p> : null}
            </div>
          </div>
          <div className="inset-panel">
            <h3>Presets</h3>
            <label className="field">
              <span>Preset Name</span>
              <input value={draft.presetName} onChange={(event) => setDraft((current) => ({ ...current, presetName: event.target.value }))} />
            </label>
            <div className="button-row">
              <button onClick={savePreset}>Save Preset</button>
              <button onClick={() => submitAction()} disabled={draft.actionType === "launch_air_package" ? !draft.inventoryId : !draft.packageId}>
                Submit Action
              </button>
            </div>
            <div className="stack">
              {presets
                .filter((item: any) => item.coalition === draft.coalition)
                .map((preset: any) => (
                  <button key={preset.id} className="list-card" onClick={() => hydrateFromPreset(preset.id)}>
                    <strong>{preset.name}</strong>
                    <span>
                      {preset.package_type} / {preset.inventory_id}
                    </span>
                  </button>
                ))}
            </div>
          </div>
          {operatorResult ? (
            <div className="callout-result">
              <h3>Submission Result</h3>
              <div className="summary-grid">
                <SummaryCard title="Accepted" value={operatorResult.operator_action_summary?.accepted_count ?? 0} />
                <SummaryCard title="Partial" value={operatorResult.operator_action_summary?.partially_accepted_count ?? 0} />
                <SummaryCard title="Rejected" value={operatorResult.operator_action_summary?.rejected_count ?? 0} />
                <SummaryCard title="Mode" value={operatorResult.mode ?? "unknown"} />
              </div>
              {(operatorResult.operator_action_summary?.warnings ?? []).length ? (
                <div className="stack">
                  {operatorResult.operator_action_summary.warnings.map((warning: any, index: number) => (
                    <div key={`warning-${index}`} className="callout-note warning">
                      {warning.text}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="panel">
          <h2>Timeline</h2>
          <ul className="compact-list">
            {(ops?.timeline ?? []).slice(-8).reverse().map((entry: any) => (
              <li key={`timeline-${entry.decision_cycle}`}>
                <strong>Cycle {entry.decision_cycle}</strong>: {entry.classification}
                {entry.failures?.length ? ` | failures: ${entry.failures.join(", ")}` : ""}
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}
