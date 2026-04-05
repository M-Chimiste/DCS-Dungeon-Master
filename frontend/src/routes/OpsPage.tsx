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

const DEFAULT_LAYERS: LayerState = {
  sectors: true,
  controlPoints: true,
  redKnowledge: true,
  blueKnowledge: true,
  worldTruth: true,
  executionNotes: true,
  attachments: true,
};

export function OpsPage() {
  const [runs, setRuns] = useState<any[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [ops, setOps] = useState<any | null>(null);
  const [redPreview, setRedPreview] = useState<any | null>(null);
  const [bluePreview, setBluePreview] = useState<any | null>(null);
  const [status, setStatus] = useState<string>("Loading runs...");
  const [layers, setLayers] = useState<LayerState>(DEFAULT_LAYERS);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  useEffect(() => {
    api<{ runs: any[] }>("/api/runs")
      .then((payload) => {
        setRuns(payload.runs);
        setSelectedRunId(payload.runs[0]?.run_id ?? "");
        setStatus("Run catalog ready.");
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

  return (
    <div className="grid ops-grid">
      <aside className="rail">
        <div className="panel">
          <h2>Runs</h2>
          <p className="muted">{status}</p>
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
              </button>
            ))}
          </div>
        </div>

        <div className="panel">
          <h3>Operator Snapshot</h3>
          <div className="button-row">
            <button onClick={refreshNow}>Refresh Now</button>
            <span className="state-badge saved">{lastUpdated ? `Updated ${lastUpdated}` : "Waiting..."}</span>
          </div>
          <div className="summary-grid">
            <SummaryCard title="Cycle" value={ops?.summary?.latest_decision_cycle ?? 0} />
            <SummaryCard title="Timeline" value={ops?.timeline?.length ?? 0} />
            <SummaryCard title="RED Contacts" value={ops?.red_knowledge?.contact_tracks?.length ?? 0} />
            <SummaryCard title="BLUE Contacts" value={ops?.blue_knowledge?.contact_tracks?.length ?? 0} />
          </div>
        </div>

        <div className="panel">
          <h3>Layers</h3>
          <div className="checkbox-grid">
            {Object.entries(layers).map(([key, enabled]) => (
              <label key={key} className="checkbox-pill">
                <input type="checkbox" checked={enabled} onChange={() => toggleLayer(key as keyof LayerState)} />
                {key}
              </label>
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
          <SummaryCard title="RED Proposal" value={latestRed?.status ?? "none"} detail={latestRed?.backend_name ?? "No model response"} />
          <SummaryCard title="BLUE Proposal" value={latestBlue?.status ?? "none"} detail={latestBlue?.backend_name ?? "No model response"} />
          <SummaryCard title="RED Validation" value={ops?.red_inspection?.latest_action_validation?.accepted_count ?? 0} detail="accepted" />
          <SummaryCard title="BLUE Validation" value={ops?.blue_inspection?.latest_action_validation?.accepted_count ?? 0} detail="accepted" />
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
            <ul className="compact-list">
              {(redPreview?.observation?.recent_changes ?? []).slice(0, 5).map((item: string) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>
          <section className="panel preview-panel">
            <h3>BLUFOR Commander Preview</h3>
            <p>{bluePreview?.narrative ?? "No BLUE observation yet."}</p>
            <div className="summary-grid">
              <SummaryCard title="Contacts" value={bluePreview?.observation?.enemy_contacts?.length ?? 0} />
              <SummaryCard title="Friendlies" value={bluePreview?.observation?.friendly_forces?.length ?? 0} />
              <SummaryCard title="Changes" value={bluePreview?.observation?.recent_changes?.length ?? 0} />
            </div>
            <ul className="compact-list">
              {(bluePreview?.observation?.recent_changes ?? []).slice(0, 5).map((item: string) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>
        </div>
      </section>

      <aside className="rail">
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

        <div className="panel">
          <h2>Warnings & Notes</h2>
          {layers.executionNotes ? (
            <ul className="compact-list">
              {(ops?.operator_map_layers?.current_execution_notes ?? []).slice(0, 8).map((note: any) => (
                <li key={`${note.entity_id}-${note.action_id}`}>
                  <strong>{note.entity_id}</strong>: {note.note}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Execution notes layer hidden.</p>
          )}
        </div>

        <div className="panel">
          <h2>Attachments</h2>
          {layers.attachments ? (
            <ul className="compact-list">
              {(ops?.operator_map_layers?.attachments ?? []).slice(0, 4).map((entry: any) => (
                <li key={`${entry.observation_id}-${entry.coalition}`}>
                  <strong>{entry.coalition}</strong> cycle {entry.decision_cycle}: {entry.attachments?.length ?? 0} attachment(s)
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Attachment layer hidden.</p>
          )}
        </div>
      </aside>
    </div>
  );
}
