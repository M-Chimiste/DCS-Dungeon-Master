import { useEffect, useState } from "react";
import { api } from "../lib/api";

type SetupCheck = {
  step: string;
  status: string;
  healthy: boolean;
  detail: string;
  detected_value?: string | null;
  metadata?: Record<string, unknown>;
};

type SetupStatus = {
  config_path: string;
  saved_games_path: SetupCheck;
  autoexec_status: SetupCheck;
  olympus_status: SetupCheck;
  grpc_status: SetupCheck;
  config_status: SetupCheck;
  overall_status: string;
  recommended_actions: Array<{ code: string; message: string; action?: string | null }>;
};

export function SetupPage() {
  const [savedGamesPath, setSavedGamesPath] = useState("");
  const [olympusUrl, setOlympusUrl] = useState("");
  const [grpcHost, setGrpcHost] = useState("");
  const [grpcPort, setGrpcPort] = useState("50051");
  const [outputPath, setOutputPath] = useState("config/local.toml");
  const [status, setStatus] = useState("Loading setup readiness...");
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  const [writeResult, setWriteResult] = useState<any | null>(null);

  const loadStatus = async () => {
    try {
      const payload = await api<{ setup: SetupStatus }>("/api/setup/status");
      setSetup(payload.setup);
      setStatus(`Setup status: ${payload.setup.overall_status}`);
    } catch (error) {
      setStatus(String(error));
    }
  };

  useEffect(() => {
    void loadStatus();
  }, []);

  const probe = async () => {
    setStatus("Probing local DCS setup...");
    setWriteResult(null);
    try {
      const payload = await api<{ setup: SetupStatus }>("/api/setup/probe", {
        method: "POST",
        body: JSON.stringify({
          saved_games_path: savedGamesPath || null,
          olympus_url: olympusUrl || null,
          grpc_host: grpcHost || null,
          grpc_port: grpcPort || null,
        }),
      });
      setSetup(payload.setup);
      setStatus(`Setup status: ${payload.setup.overall_status}`);
    } catch (error) {
      setStatus(String(error));
    }
  };

  const writeConfig = async () => {
    setStatus("Writing generated local config...");
    try {
      const payload = await api<{ write_result: any }>("/api/setup/write-config", {
        method: "POST",
        body: JSON.stringify({
          output: outputPath,
          saved_games_path: savedGamesPath || null,
          olympus_url: olympusUrl || null,
          grpc_host: grpcHost || null,
          grpc_port: grpcPort || null,
        }),
      });
      setWriteResult(payload.write_result);
      setStatus(payload.write_result.detail);
      await loadStatus();
    } catch (error) {
      setStatus(String(error));
    }
  };

  const checks = setup
    ? [
        setup.saved_games_path,
        setup.autoexec_status,
        setup.olympus_status,
        setup.grpc_status,
        setup.config_status,
      ]
    : [];

  return (
    <div className="grid setup-grid">
      <aside className="rail">
        <div className="panel">
          <h2>Setup Wizard</h2>
          <p className="muted">{status}</p>
          <label className="field">
            <span>Saved Games Path</span>
            <input value={savedGamesPath} onChange={(event) => setSavedGamesPath(event.target.value)} placeholder="C:\\Users\\You\\Saved Games\\DCS.openbeta" />
          </label>
          <label className="field">
            <span>Olympus URL</span>
            <input value={olympusUrl} onChange={(event) => setOlympusUrl(event.target.value)} placeholder="http://127.0.0.1:4512" />
          </label>
          <label className="field">
            <span>gRPC Host</span>
            <input value={grpcHost} onChange={(event) => setGrpcHost(event.target.value)} placeholder="127.0.0.1" />
          </label>
          <label className="field">
            <span>gRPC Port</span>
            <input value={grpcPort} onChange={(event) => setGrpcPort(event.target.value)} placeholder="50051" />
          </label>
          <label className="field">
            <span>Output Config</span>
            <input value={outputPath} onChange={(event) => setOutputPath(event.target.value)} placeholder="config/local.toml" />
          </label>
          <div className="button-row">
            <button onClick={probe}>Probe Setup</button>
            <button onClick={writeConfig}>Write Config</button>
          </div>
          {writeResult ? (
            <div className="panel inset-panel">
              <h3>Last Write</h3>
              <p className="muted">{writeResult.output_path}</p>
              <p>{writeResult.detail}</p>
            </div>
          ) : null}
        </div>
      </aside>
      <section className="canvas-column">
        <div className="panel">
          <h2>Readiness</h2>
          <p className="muted">Config file: {setup?.config_path ?? "unknown"}</p>
          <div className="stack">
            {checks.map((check) => (
              <div key={check.step} className="panel inset-panel">
                <div className="list-row">
                  <strong>{check.step}</strong>
                  <span className={`state-badge ${check.healthy ? "saved" : "error"}`}>{check.status}</span>
                </div>
                <p>{check.detail}</p>
                {check.detected_value ? <p className="muted">{check.detected_value}</p> : null}
                {check.metadata?.missing_lines ? (
                  <pre>{JSON.stringify(check.metadata.missing_lines, null, 2)}</pre>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </section>
      <aside className="rail">
        <div className="panel">
          <h2>Recommendations</h2>
          <div className="stack">
            {(setup?.recommended_actions ?? []).map((item) => (
              <div key={item.code} className="panel inset-panel">
                <strong>{item.code}</strong>
                <p>{item.message}</p>
                {item.action ? <p className="muted">{item.action}</p> : null}
              </div>
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}
