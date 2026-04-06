import { useEffect, useMemo, useState } from "react";
import { MapCanvas } from "../components/MapCanvas";
import { SummaryCard } from "../components/SummaryCard";
import { api } from "../lib/api";

type LayerState = {
  sectors: boolean;
  controlPoints: boolean;
  redKnowledge: boolean;
  blueKnowledge: boolean;
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

const DEFAULT_LAYERS: LayerState = {
  sectors: true,
  controlPoints: true,
  redKnowledge: true,
  blueKnowledge: true,
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

export function OpsPage() {
  const [runs, setRuns] = useState<any[]>([]);
  const [resumableRuns, setResumableRuns] = useState<any[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [scenarios, setScenarios] = useState<any[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>("");
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
        const defaultCatalogId = catalogPayload.catalog[0]?.catalog_id ?? "";
        setSharedPrimaryCatalogId(defaultCatalogId);
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

  const visibleTracks = useMemo(
    () => [
      ...(layers.redKnowledge ? ops?.operator_map_layers?.red_knowledge_tracks ?? [] : []),
      ...(layers.blueKnowledge ? ops?.operator_map_layers?.blue_knowledge_tracks ?? [] : []),
    ],
    [layers.blueKnowledge, layers.redKnowledge, ops],
  );

  const visibleControlPoints = layers.controlPoints ? ops?.operator_map_layers?.control_points ?? [] : [];
  const visibleSectors = layers.sectors ? ops?.operator_map_layers?.sectors ?? [] : [];
  const visibleWorldGroups = layers.worldTruth ? ops?.operator_map_layers?.world_groups ?? [] : [];

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

  return (
    <div className="grid ops-grid">
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
              <label className="field">
                <span>REDFOR Fallback</span>
                <select value={redFallbackCatalogId} onChange={(event) => setRedFallbackCatalogId(event.target.value)}>
                  <option value="">Use shared</option>
                  {catalog.map((entry) => (
                    <option key={entry.catalog_id} value={entry.catalog_id}>
                      {entry.display_name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>BLUFOR Fallback</span>
                <select value={blueFallbackCatalogId} onChange={(event) => setBlueFallbackCatalogId(event.target.value)}>
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
          tracks={visibleTracks}
          worldGroups={visibleWorldGroups}
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
          </section>
          <section className="panel preview-panel">
            <h3>BLUFOR Commander Preview</h3>
            <p>{bluePreview?.narrative ?? "No BLUE observation yet."}</p>
            <div className="summary-grid">
              <SummaryCard title="Contacts" value={bluePreview?.observation?.enemy_contacts?.length ?? 0} />
              <SummaryCard title="Friendlies" value={bluePreview?.observation?.friendly_forces?.length ?? 0} />
              <SummaryCard title="Changes" value={bluePreview?.observation?.recent_changes?.length ?? 0} />
            </div>
          </section>
        </div>
      </section>

      <aside className="rail">
        <div className="panel">
          <h2>Catalog</h2>
          <ul className="compact-list">
            {catalog.map((entry) => (
              <li key={entry.catalog_id}>
                <strong>{entry.display_name}</strong>
                <div>{entry.server_label}</div>
                <div>
                  {entry.model} | {entry.hosting_mode}
                  {entry.supports_multimodal ? " | vision" : ""}
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="panel">
          <h3>Operator Snapshot</h3>
          <div className="button-row">
            <button onClick={refreshNow}>Refresh Now</button>
            <span className="state-badge saved">{lastUpdated ? `Updated ${lastUpdated}` : "Waiting..."}</span>
          </div>
          <div className="checkbox-grid">
            {Object.entries(layers).map(([key, enabled]) => (
              <label key={key} className="checkbox-pill">
                <input type="checkbox" checked={enabled} onChange={() => toggleLayer(key as keyof LayerState)} />
                {key}
              </label>
            ))}
          </div>
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
