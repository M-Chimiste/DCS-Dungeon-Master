import { useEffect, useMemo, useState } from "react";
import { MapCanvas } from "../components/MapCanvas";
import { SummaryCard } from "../components/SummaryCard";
import { api } from "../lib/api";

type ScenarioListResponse = {
  scenarios: Array<{ id: string; name: string; theater: string; summary: string }>;
  drafts: Array<any>;
};

type ScenarioPayload = {
  scenario: any;
  map_reference: any;
  sectors: any[];
  control_points: any[];
  force_policies: any[];
};

type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

export function StudioPage() {
  const [catalog, setCatalog] = useState<ScenarioListResponse | null>(null);
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>("");
  const [scenarioPayload, setScenarioPayload] = useState<ScenarioPayload | null>(null);
  const [draft, setDraft] = useState<any | null>(null);
  const [selectedSectorId, setSelectedSectorId] = useState<string>("");
  const [selectedControlPointId, setSelectedControlPointId] = useState<string>("");
  const [selectedZoneId, setSelectedZoneId] = useState<string>("");
  const [validation, setValidation] = useState<any | null>(null);
  const [statusText, setStatusText] = useState<string>("Loading scenario catalog...");
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const refreshCatalog = async () => {
    const payload = await api<ScenarioListResponse>("/api/scenarios");
    setCatalog(payload);
    if (!selectedScenarioId && payload.scenarios[0]?.id) {
      setSelectedScenarioId(payload.scenarios[0].id);
    }
  };

  useEffect(() => {
    refreshCatalog()
      .then(() => setStatusText("Scenario catalog ready."))
      .catch((error) => setStatusText(String(error)));
  }, []);

  useEffect(() => {
    if (!selectedScenarioId) return;
    if (draft?.source_scenario_id === selectedScenarioId) return;
    api<ScenarioPayload>(`/api/scenarios/${selectedScenarioId}`)
      .then((payload) => {
        setScenarioPayload(payload);
        setSelectedSectorId(payload.scenario.sectors[0]?.id ?? "");
        setSelectedControlPointId("");
        setSelectedZoneId(payload.scenario.zones?.[0]?.id ?? "");
        setDraft(null);
        setValidation(null);
        setSaveState("idle");
      })
      .catch((error) => setStatusText(String(error)));
  }, [selectedScenarioId, draft?.source_scenario_id]);

  useEffect(() => {
    if (!draft || saveState !== "dirty") return;
    const timer = window.setTimeout(async () => {
      try {
        setSaveState("saving");
        const payload = await api<{ draft: any }>(`/api/scenarios/drafts/${draft.draft_id}`, {
          method: "PUT",
          body: JSON.stringify({ scenario: draft.scenario, pydcs_mapping: draft.pydcs_mapping ?? {} }),
        });
        setDraft(payload.draft);
        setSaveState("saved");
        setStatusText(`Draft ${payload.draft.display_name} saved.`);
        await refreshCatalog();
      } catch (error) {
        setSaveState("error");
        setStatusText(String(error));
      }
    }, 700);
    return () => window.clearTimeout(timer);
  }, [draft, saveState]);

  const currentScenario = draft?.scenario ?? scenarioPayload?.scenario;
  const sectors = currentScenario?.sectors ?? [];
  const controlPoints = currentScenario?.control_points ?? [];
  const zones = currentScenario?.zones ?? [];
  const activeGroups = currentScenario?.active_groups ?? [];
  const reserveGroups = currentScenario?.reserve_groups ?? [];
  const restrictions = currentScenario?.deployment_restrictions ?? [];
  const coalitions = currentScenario?.coalitions ?? [];
  const draftsForScenario = useMemo(
    () => (catalog?.drafts ?? []).filter((item: any) => item.source_scenario_id === selectedScenarioId),
    [catalog, selectedScenarioId],
  );

  const selectedSector = sectors.find((item: any) => item.id === selectedSectorId) ?? null;
  const selectedControlPoint = controlPoints.find((item: any) => item.id === selectedControlPointId) ?? null;
  const selectedZone = zones.find((item: any) => item.id === selectedZoneId) ?? null;

  const markDraftScenario = (nextScenario: any) => {
    if (!draft) return;
    setDraft({
      ...draft,
      scenario: nextScenario,
      name: nextScenario.name,
      scenario_id: nextScenario.id,
      theater: nextScenario.theater,
      dirty: true,
    });
    setSaveState("dirty");
  };

  const createDraft = async () => {
    if (!selectedScenarioId) return;
    const payload = await api<{ draft: any }>("/api/scenarios/drafts", {
      method: "POST",
      body: JSON.stringify({ source_scenario_id: selectedScenarioId }),
    });
    setDraft(payload.draft);
    setValidation(null);
    setSaveState("saved");
    setStatusText(`Draft ${payload.draft.display_name} created.`);
    await refreshCatalog();
  };

  const openDraft = async (draftId: string) => {
    const payload = await api<{ draft: any }>(`/api/scenarios/drafts/${draftId}`);
    setDraft(payload.draft);
    setSelectedScenarioId(payload.draft.source_scenario_id);
    setSelectedSectorId(payload.draft.scenario.sectors[0]?.id ?? "");
    setSelectedControlPointId("");
    setSelectedZoneId(payload.draft.scenario.zones?.[0]?.id ?? "");
    setValidation(null);
    setSaveState("idle");
    setStatusText(`Loaded ${payload.draft.display_name}.`);
  };

  const renameDraft = async () => {
    if (!draft) return;
    const displayName = window.prompt("Draft display name", draft.display_name);
    if (!displayName) return;
    const payload = await api<{ draft: any }>(`/api/scenarios/drafts/${draft.draft_id}/rename`, {
      method: "PATCH",
      body: JSON.stringify({ display_name: displayName }),
    });
    setDraft(payload.draft);
    setStatusText(`Renamed draft to ${payload.draft.display_name}.`);
    await refreshCatalog();
  };

  const duplicateDraft = async () => {
    if (!draft) return;
    const payload = await api<{ draft: any }>(`/api/scenarios/drafts/${draft.draft_id}/duplicate`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setDraft(payload.draft);
    setStatusText(`Duplicated draft as ${payload.draft.display_name}.`);
    await refreshCatalog();
  };

  const revertDraft = async () => {
    if (!draft) return;
    const payload = await api<{ draft: any }>(`/api/scenarios/drafts/${draft.draft_id}/revert`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setDraft(payload.draft);
    setSaveState("saved");
    setStatusText(`Reverted ${payload.draft.display_name} to source scenario.`);
  };

  const deleteDraft = async () => {
    if (!draft) return;
    if (!window.confirm(`Delete ${draft.display_name}?`)) return;
    await api(`/api/scenarios/drafts/${draft.draft_id}`, { method: "DELETE" });
    setDraft(null);
    setSaveState("idle");
    setStatusText("Draft deleted.");
    await refreshCatalog();
  };

  const validateDraft = async () => {
    if (!draft) return;
    const payload = await api<any>(`/api/scenarios/drafts/${draft.draft_id}/validate`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setValidation(payload);
    setStatusText(payload.valid ? "Draft validates cleanly." : "Draft has validation issues.");
  };

  const saveAsScenario = async () => {
    if (!draft) return;
    const scenarioId = window.prompt("New scenario id", `${draft.scenario.id}_draft`);
    const name = window.prompt("Scenario name", `${draft.scenario.name} (Edited)`);
    if (!scenarioId || !name) return;
    const payload = await api<any>(`/api/scenarios/drafts/${draft.draft_id}/save-as`, {
      method: "POST",
      body: JSON.stringify({ scenario_id: scenarioId, name }),
    });
    setStatusText(`Scenario saved to ${payload.scenario_path}`);
    await refreshCatalog();
  };

  const focusIssue = (issue: any) => {
    if (issue.entity_type === "sector") {
      setSelectedSectorId(issue.entity_id);
      setSelectedControlPointId("");
      setSelectedZoneId("");
    } else if (issue.entity_type === "control_point") {
      setSelectedControlPointId(issue.entity_id);
      setSelectedSectorId("");
      setSelectedZoneId("");
    } else if (issue.entity_type === "zone") {
      setSelectedZoneId(issue.entity_id);
      setSelectedSectorId("");
      setSelectedControlPointId("");
    }
  };

  const updateSectorField = (field: string, value: string | number) => {
    if (!draft || !selectedSectorId) return;
    markDraftScenario({
      ...draft.scenario,
      sectors: sectors.map((sector: any) =>
        sector.id === selectedSectorId ? { ...sector, [field]: value } : sector,
      ),
    });
  };

  const updateControlPointField = (controlPointId: string, field: string, value: string | null) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      control_points: controlPoints.map((point: any) =>
        point.id === controlPointId ? { ...point, [field]: value } : point,
      ),
    });
  };

  const updateZoneField = (field: string, value: string | number) => {
    if (!draft || !selectedZoneId) return;
    markDraftScenario({
      ...draft.scenario,
      zones: zones.map((zone: any) =>
        zone.id === selectedZoneId ? { ...zone, [field]: value } : zone,
      ),
    });
  };

  const updateActiveGroup = (groupId: string, field: string, value: string | null | boolean) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      active_groups: activeGroups.map((group: any) =>
        group.id === groupId ? { ...group, [field]: value } : group,
      ),
    });
  };

  const toggleReserveAllowedSector = (reserveId: string, sectorId: string) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      reserve_groups: reserveGroups.map((reserve: any) => {
        if (reserve.id !== reserveId) return reserve;
        const current = new Set<string>(reserve.allowed_sector_ids ?? []);
        if (current.has(sectorId)) {
          current.delete(sectorId);
        } else {
          current.add(sectorId);
        }
        return { ...reserve, allowed_sector_ids: Array.from(current).sort() };
      }),
    });
  };

  const updateCoalitionField = (coalitionId: string, field: string, value: string | number) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      coalitions: coalitions.map((coalition: any) =>
        coalition.coalition === coalitionId ? { ...coalition, [field]: value } : coalition,
      ),
    });
  };

  const updateRestrictionField = (restrictionId: string, field: string, value: string | boolean | null) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      deployment_restrictions: restrictions.map((restriction: any) =>
        restriction.id === restrictionId ? { ...restriction, [field]: value } : restriction,
      ),
    });
  };

  const toggleRestrictionSector = (restrictionId: string, sectorId: string) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      deployment_restrictions: restrictions.map((restriction: any) => {
        if (restriction.id !== restrictionId) return restriction;
        const current = new Set<string>(restriction.sector_ids ?? []);
        if (current.has(sectorId)) {
          current.delete(sectorId);
        } else {
          current.add(sectorId);
        }
        return { ...restriction, sector_ids: Array.from(current).sort() };
      }),
    });
  };

  const toggleRestrictionControlPoint = (restrictionId: string, controlPointId: string) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      deployment_restrictions: restrictions.map((restriction: any) => {
        if (restriction.id !== restrictionId) return restriction;
        const current = new Set<string>(restriction.control_point_ids ?? []);
        if (current.has(controlPointId)) {
          current.delete(controlPointId);
        } else {
          current.add(controlPointId);
        }
        return { ...restriction, control_point_ids: Array.from(current).sort() };
      }),
    });
  };

  const saveBadge =
    saveState === "dirty"
      ? "Unsaved changes"
      : saveState === "saving"
        ? "Saving..."
        : saveState === "saved"
          ? "Saved"
          : saveState === "error"
            ? "Save failed"
            : "Draft idle";

  return (
    <div className="grid studio-grid">
      <aside className="rail">
        <div className="panel">
          <h2>Scenario Catalog</h2>
          <p className="muted">{statusText}</p>
          <div className="stack">
            {catalog?.scenarios.map((scenario) => (
              <button
                key={scenario.id}
                className={selectedScenarioId === scenario.id && !draft ? "list-card active" : "list-card"}
                onClick={() => {
                  setSelectedScenarioId(scenario.id);
                  setDraft(null);
                }}
              >
                <strong>{scenario.name}</strong>
                <span>{scenario.theater}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="panel">
          <h3>Drafts</h3>
          <div className="button-row">
            <button onClick={createDraft}>Create Draft</button>
            <span className={`state-badge ${saveState}`}>{saveBadge}</span>
          </div>
          <div className="stack">
            {draftsForScenario.map((item: any) => (
              <button
                key={item.draft_id}
                className={draft?.draft_id === item.draft_id ? "list-card active" : "list-card"}
                onClick={() => openDraft(item.draft_id)}
              >
                <strong>{item.display_name}</strong>
                <span>{new Date(item.updated_at).toLocaleString()}</span>
              </button>
            ))}
            {!draftsForScenario.length ? <p className="muted">No drafts for this scenario yet.</p> : null}
          </div>
        </div>
        {draft ? (
          <div className="panel">
            <h3>Draft Actions</h3>
            <div className="button-row">
              <button onClick={renameDraft}>Rename</button>
              <button onClick={duplicateDraft}>Duplicate</button>
            </div>
            <div className="button-row">
              <button onClick={revertDraft}>Revert</button>
              <button onClick={deleteDraft}>Delete</button>
            </div>
            <div className="button-row">
              <button onClick={validateDraft}>Validate</button>
              <button onClick={saveAsScenario}>Save As</button>
            </div>
          </div>
        ) : null}
      </aside>

      <section className="canvas-column">
        <MapCanvas
          sectors={sectors}
          controlPoints={controlPoints}
          title={currentScenario?.name ?? "Scenario"}
          selectedSectorId={selectedSectorId}
          selectedControlPointId={selectedControlPointId}
          onSectorSelect={(sectorId) => {
            setSelectedSectorId(sectorId);
            setSelectedControlPointId("");
            setSelectedZoneId("");
          }}
          onControlPointSelect={(controlPointId) => {
            setSelectedControlPointId(controlPointId);
            setSelectedSectorId("");
            setSelectedZoneId("");
          }}
        />
        <div className="summary-grid">
          <SummaryCard title="Sectors" value={sectors.length} />
          <SummaryCard title="Control Points" value={controlPoints.length} />
          <SummaryCard title="Zones" value={zones.length} />
          <SummaryCard title="Active Groups" value={activeGroups.length} />
          <SummaryCard title="Reserve Groups" value={reserveGroups.length} />
          <SummaryCard title="Restrictions" value={restrictions.length} />
        </div>
        {draft?.map_reference ? (
          <div className="panel">
            <h3>Reference Layer</h3>
            <div className="summary-grid">
              <SummaryCard title="Status" value={draft.map_reference.reference_status} detail={draft.map_reference.message} />
              <SummaryCard title="Airports" value={draft.map_reference.airports?.length ?? 0} />
              <SummaryCard title="Theater" value={draft.map_reference.theater_id} />
            </div>
          </div>
        ) : null}
      </section>

      <aside className="rail">
        <div className="panel">
          <h2>Sector Inspector</h2>
          {selectedSector ? (
            <>
              <label>
                Name
                <input value={selectedSector.name} disabled={!draft} onChange={(event) => updateSectorField("name", event.target.value)} />
              </label>
              <label>
                Role
                <input value={selectedSector.role} disabled={!draft} onChange={(event) => updateSectorField("role", event.target.value)} />
              </label>
              <label>
                Radius (nm)
                <input
                  type="number"
                  value={selectedSector.radius_nm}
                  disabled={!draft}
                  onChange={(event) => updateSectorField("radius_nm", Number(event.target.value))}
                />
              </label>
              <label>
                Center Lat
                <input
                  type="number"
                  value={selectedSector.center_lat ?? 0}
                  disabled={!draft}
                  onChange={(event) => updateSectorField("center_lat", Number(event.target.value))}
                />
              </label>
              <label>
                Center Lng
                <input
                  type="number"
                  value={selectedSector.center_lng ?? 0}
                  disabled={!draft}
                  onChange={(event) => updateSectorField("center_lng", Number(event.target.value))}
                />
              </label>
            </>
          ) : (
            <p className="muted">Click a sector on the map to edit it.</p>
          )}
        </div>

        <div className="panel">
          <h2>Control Point Inspector</h2>
          {selectedControlPoint ? (
            <>
              <label>
                Name
                <input
                  value={selectedControlPoint.name}
                  disabled={!draft}
                  onChange={(event) => updateControlPointField(selectedControlPoint.id, "name", event.target.value)}
                />
              </label>
              <label>
                Strategic Value
                <select
                  value={selectedControlPoint.strategic_value}
                  disabled={!draft}
                  onChange={(event) => updateControlPointField(selectedControlPoint.id, "strategic_value", event.target.value)}
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </label>
              <label>
                Owner
                <select
                  value={selectedControlPoint.owner ?? ""}
                  disabled={!draft}
                  onChange={(event) => updateControlPointField(selectedControlPoint.id, "owner", event.target.value || null)}
                >
                  <option value="">Unowned</option>
                  <option value="red">REDFOR</option>
                  <option value="blue">BLUFOR</option>
                </select>
              </label>
            </>
          ) : (
            <p className="muted">Click a control point on the map to edit it.</p>
          )}
        </div>

        <div className="panel">
          <h2>Zone Inspector</h2>
          {zones.length ? (
            <>
              <select value={selectedZoneId} onChange={(event) => setSelectedZoneId(event.target.value)}>
                {zones.map((zone: any) => (
                  <option key={zone.id} value={zone.id}>
                    {zone.name}
                  </option>
                ))}
              </select>
              {selectedZone ? (
                <>
                  <label>
                    Name
                    <input value={selectedZone.name} disabled={!draft} onChange={(event) => updateZoneField("name", event.target.value)} />
                  </label>
                  <label>
                    Sector
                    <select value={selectedZone.sector_id} disabled={!draft} onChange={(event) => updateZoneField("sector_id", event.target.value)}>
                      {sectors.map((sector: any) => (
                        <option key={sector.id} value={sector.id}>
                          {sector.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Radius (nm)
                    <input
                      type="number"
                      value={selectedZone.radius_nm}
                      disabled={!draft}
                      onChange={(event) => updateZoneField("radius_nm", Number(event.target.value))}
                    />
                  </label>
                </>
              ) : null}
            </>
          ) : (
            <p className="muted">No zones in this scenario.</p>
          )}
        </div>

        <div className="panel">
          <h2>Force Policies</h2>
          <div className="stack">
            {coalitions.map((coalition: any) => (
              <div key={coalition.coalition} className="subpanel">
                <h3>{coalition.coalition.toUpperCase()}</h3>
                <label>
                  Budget
                  <input
                    type="number"
                    value={coalition.budget_remaining}
                    disabled={!draft}
                    onChange={(event) => updateCoalitionField(coalition.coalition, "budget_remaining", Number(event.target.value))}
                  />
                </label>
                <div className="mini-table">
                  {activeGroups
                    .filter((group: any) => group.coalition === coalition.coalition)
                    .map((group: any) => (
                      <div key={group.id} className="list-row">
                        <div>
                          <strong>{group.id}</strong>
                          <div className="muted">{group.group_type}</div>
                        </div>
                        <div className="inline-fields">
                          <select
                            value={group.sector_id ?? ""}
                            disabled={!draft}
                            onChange={(event) => updateActiveGroup(group.id, "sector_id", event.target.value || null)}
                          >
                            <option value="">No sector</option>
                            {sectors.map((sector: any) => (
                              <option key={sector.id} value={sector.id}>
                                {sector.name}
                              </option>
                            ))}
                          </select>
                          <select
                            value={group.control_point_id ?? ""}
                            disabled={!draft}
                            onChange={(event) => updateActiveGroup(group.id, "control_point_id", event.target.value || null)}
                          >
                            <option value="">No control point</option>
                            {controlPoints.map((point: any) => (
                              <option key={point.id} value={point.id}>
                                {point.name}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                    ))}
                </div>
                <div className="mini-table">
                  {reserveGroups
                    .filter((reserve: any) => reserve.coalition === coalition.coalition)
                    .map((reserve: any) => (
                      <div key={reserve.id} className="restriction-block">
                        <strong>{reserve.id}</strong>
                        <div className="checkbox-grid">
                          {sectors.map((sector: any) => (
                            <label key={`${reserve.id}-${sector.id}`} className="checkbox-pill">
                              <input
                                type="checkbox"
                                checked={(reserve.allowed_sector_ids ?? []).includes(sector.id)}
                                disabled={!draft}
                                onChange={() => toggleReserveAllowedSector(reserve.id, sector.id)}
                              />
                              {sector.name}
                            </label>
                          ))}
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <h2>Restrictions</h2>
          <div className="stack">
            {restrictions.map((restriction: any) => (
              <div key={restriction.id} className="restriction-block">
                <label>
                  Description
                  <input
                    value={restriction.description}
                    disabled={!draft}
                    onChange={(event) => updateRestrictionField(restriction.id, "description", event.target.value)}
                  />
                </label>
                <label>
                  Coalition
                  <select
                    value={restriction.coalition ?? ""}
                    disabled={!draft}
                    onChange={(event) => updateRestrictionField(restriction.id, "coalition", event.target.value || null)}
                  >
                    <option value="">All</option>
                    <option value="red">REDFOR</option>
                    <option value="blue">BLUFOR</option>
                  </select>
                </label>
                <label className="checkbox-inline">
                  <input
                    type="checkbox"
                    checked={Boolean(restriction.adjacency_limited)}
                    disabled={!draft}
                    onChange={(event) => updateRestrictionField(restriction.id, "adjacency_limited", event.target.checked)}
                  />
                  Adjacency limited
                </label>
                <div className="checkbox-grid">
                  {sectors.map((sector: any) => (
                    <label key={`${restriction.id}-${sector.id}`} className="checkbox-pill">
                      <input
                        type="checkbox"
                        checked={(restriction.sector_ids ?? []).includes(sector.id)}
                        disabled={!draft}
                        onChange={() => toggleRestrictionSector(restriction.id, sector.id)}
                      />
                      {sector.name}
                    </label>
                  ))}
                </div>
                <div className="checkbox-grid">
                  {controlPoints.map((point: any) => (
                    <label key={`${restriction.id}-${point.id}`} className="checkbox-pill">
                      <input
                        type="checkbox"
                        checked={(restriction.control_point_ids ?? []).includes(point.id)}
                        disabled={!draft}
                        onChange={() => toggleRestrictionControlPoint(restriction.id, point.id)}
                      />
                      {point.name}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {validation ? (
          <div className="panel">
            <h2>Validation</h2>
            {validation.valid ? (
              <p className="muted">No validation issues.</p>
            ) : (
              <div className="stack">
                {validation.issues.map((issue: any, index: number) => (
                  <button key={`${issue.issue_code}-${index}`} className="issue-card" onClick={() => focusIssue(issue)}>
                    <strong>{issue.issue_code}</strong>
                    <span>{issue.entity_type}: {issue.entity_id}</span>
                    <span>{issue.message}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : null}
      </aside>
    </div>
  );
}
