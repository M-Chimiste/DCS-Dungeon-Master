import { useEffect, useMemo, useState } from "react";
import { MapCanvas } from "../components/MapCanvas";
import { SummaryCard } from "../components/SummaryCard";
import { api, downloadText } from "../lib/api";

type ScenarioListResponse = {
  scenarios: Array<{ id: string; name: string; theater: string; summary: string; path: string; draft_count: number }>;
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
type CreateMode = "template" | "blank";
type MapAddMode = "sector" | "zone" | null;

const BLANK_SOURCE_PREFIX = "__blank__:";

export function StudioPage() {
  const [catalog, setCatalog] = useState<ScenarioListResponse | null>(null);
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>("");
  const [scenarioPayload, setScenarioPayload] = useState<ScenarioPayload | null>(null);
  const [draft, setDraft] = useState<any | null>(null);
  const [selectedSectorId, setSelectedSectorId] = useState<string>("");
  const [selectedControlPointId, setSelectedControlPointId] = useState<string>("");
  const [selectedZoneId, setSelectedZoneId] = useState<string>("");
  const [selectedActiveGroupId, setSelectedActiveGroupId] = useState<string>("");
  const [selectedReserveGroupId, setSelectedReserveGroupId] = useState<string>("");
  const [selectedRestrictionId, setSelectedRestrictionId] = useState<string>("");
  const [selectedStandingOrderId, setSelectedStandingOrderId] = useState<string>("");
  const [validation, setValidation] = useState<any | null>(null);
  const [statusText, setStatusText] = useState<string>("Loading scenario catalog...");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [createMode, setCreateMode] = useState<CreateMode>("template");
  const [mapAddMode, setMapAddMode] = useState<MapAddMode>(null);
  const [toolbarCoalition, setToolbarCoalition] = useState<string>("red");
  const [blankScenario, setBlankScenario] = useState({
    scenario_id: "new_scenario",
    name: "New Scenario",
    theater: "",
    summary: "New blank scenario.",
    version: "1",
  });

  const refreshCatalog = async () => {
    const payload = await api<ScenarioListResponse>("/api/scenarios");
    setCatalog(payload);
    const defaultScenarioId = payload.scenarios[0]?.id ?? "";
    setSelectedScenarioId((current) => current || defaultScenarioId);
    setBlankScenario((current) => ({
      ...current,
      theater: current.theater || payload.scenarios[0]?.theater || "",
    }));
  };

  useEffect(() => {
    refreshCatalog()
      .then(() => setStatusText("Scenario catalog ready."))
      .catch((error) => setStatusText(String(error)));
  }, []);

  useEffect(() => {
    if (!selectedScenarioId) return;
    if (draft?.source_scenario_id === selectedScenarioId) return;
    if (selectedScenarioId.startsWith(BLANK_SOURCE_PREFIX)) return;
    api<ScenarioPayload>(`/api/scenarios/${selectedScenarioId}`)
      .then((payload) => {
        setScenarioPayload(payload);
        setSelectedSectorId(payload.scenario.sectors[0]?.id ?? "");
        setSelectedControlPointId("");
        setSelectedZoneId(payload.scenario.zones?.[0]?.id ?? "");
        setSelectedActiveGroupId(payload.scenario.active_groups?.[0]?.id ?? "");
        setSelectedReserveGroupId(payload.scenario.reserve_groups?.[0]?.id ?? "");
        setSelectedRestrictionId(payload.scenario.deployment_restrictions?.[0]?.id ?? "");
        setSelectedStandingOrderId(payload.scenario.coalitions?.flatMap((item: any) => item.standing_orders ?? [])[0]?.id ?? "");
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
  const reference = draft?.map_reference ?? scenarioPayload?.map_reference ?? null;
  const sectors = currentScenario?.sectors ?? [];
  const controlPoints = currentScenario?.control_points ?? [];
  const zones = currentScenario?.zones ?? [];
  const activeGroups = currentScenario?.active_groups ?? [];
  const reserveGroups = currentScenario?.reserve_groups ?? [];
  const restrictions = currentScenario?.deployment_restrictions ?? [];
  const coalitions = currentScenario?.coalitions ?? [];
  const standingOrders = coalitions.flatMap((coalition: any) =>
    (coalition.standing_orders ?? []).map((order: any) => ({ ...order, coalition: coalition.coalition })),
  );
  const allDrafts = catalog?.drafts ?? [];
  const theaters = useMemo(() => Array.from(new Set((catalog?.scenarios ?? []).map((item) => item.theater))), [catalog]);

  const selectedSector = sectors.find((item: any) => item.id === selectedSectorId) ?? null;
  const selectedControlPoint = controlPoints.find((item: any) => item.id === selectedControlPointId) ?? null;
  const selectedZone = zones.find((item: any) => item.id === selectedZoneId) ?? null;
  const selectedActiveGroup = activeGroups.find((item: any) => item.id === selectedActiveGroupId) ?? null;
  const selectedReserveGroup = reserveGroups.find((item: any) => item.id === selectedReserveGroupId) ?? null;
  const selectedRestriction = restrictions.find((item: any) => item.id === selectedRestrictionId) ?? null;
  const selectedStandingOrder = standingOrders.find((item: any) => item.id === selectedStandingOrderId) ?? null;

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
  const labelForObjectType = (value: string) => value.replace(/_/g, " ");

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

  const syncSelectionFromDraft = (nextDraft: any) => {
    setDraft(nextDraft);
    setSelectedScenarioId(nextDraft.source_scenario_id.startsWith(BLANK_SOURCE_PREFIX) ? "" : nextDraft.source_scenario_id);
    setSelectedSectorId(nextDraft.scenario.sectors?.[0]?.id ?? "");
    setSelectedControlPointId(nextDraft.scenario.control_points?.[0]?.id ?? "");
    setSelectedZoneId(nextDraft.scenario.zones?.[0]?.id ?? "");
    setSelectedActiveGroupId(nextDraft.scenario.active_groups?.[0]?.id ?? "");
    setSelectedReserveGroupId(nextDraft.scenario.reserve_groups?.[0]?.id ?? "");
    setSelectedRestrictionId(nextDraft.scenario.deployment_restrictions?.[0]?.id ?? "");
    setSelectedStandingOrderId(
      nextDraft.scenario.coalitions?.flatMap((item: any) => item.standing_orders ?? [])[0]?.id ?? "",
    );
    setValidation(null);
    setSaveState("idle");
  };

  const createDraft = async () => {
    if (createMode === "template") {
      if (!selectedScenarioId) return;
      const payload = await api<{ draft: any }>("/api/scenarios/drafts", {
        method: "POST",
        body: JSON.stringify({ source_scenario_id: selectedScenarioId }),
      });
      syncSelectionFromDraft(payload.draft);
      setStatusText(`Draft ${payload.draft.display_name} created.`);
    } else {
      const payload = await api<{ draft: any }>("/api/scenarios/drafts", {
        method: "POST",
        body: JSON.stringify({ blank_scenario: blankScenario }),
      });
      syncSelectionFromDraft(payload.draft);
      setStatusText(`Blank scenario draft ${payload.draft.display_name} created.`);
    }
    await refreshCatalog();
  };

  const openDraft = async (draftId: string) => {
    const payload = await api<{ draft: any }>(`/api/scenarios/drafts/${draftId}`);
    syncSelectionFromDraft(payload.draft);
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
    syncSelectionFromDraft(payload.draft);
    setStatusText(`Duplicated draft as ${payload.draft.display_name}.`);
    await refreshCatalog();
  };

  const revertDraft = async () => {
    if (!draft) return;
    const payload = await api<{ draft: any }>(`/api/scenarios/drafts/${draft.draft_id}/revert`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    syncSelectionFromDraft(payload.draft);
    setSaveState("saved");
    setStatusText(`Reverted ${payload.draft.display_name}.`);
  };

  const deleteDraft = async () => {
    if (!draft) return;
    if (!window.confirm(`Delete ${draft.display_name}?`)) return;
    await api(`/api/scenarios/drafts/${draft.draft_id}`, { method: "DELETE" });
    setDraft(null);
    setValidation(null);
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
    const scenarioId = window.prompt("Scenario id", draft.scenario.id);
    const name = window.prompt("Scenario name", draft.scenario.name);
    if (!scenarioId || !name) return;
    const overwrite = window.confirm("Allow overwrite if this scenario id already exists?");
    const payload = await api<any>(`/api/scenarios/drafts/${draft.draft_id}/save-as`, {
      method: "POST",
      body: JSON.stringify({ scenario_id: scenarioId, name, overwrite }),
    });
    setStatusText(`Scenario saved to ${payload.scenario_path}`);
    await refreshCatalog();
  };

  const exportScenarioToml = async () => {
    if (!draft) return;
    await downloadText(`/api/scenarios/drafts/${draft.draft_id}/export.toml`, `${draft.scenario.id}.toml`);
    setStatusText(`Exported ${draft.scenario.id}.toml`);
  };

  const createObject = async (objectType: string, extra: Record<string, unknown> = {}) => {
    if (!draft) return;
    const payload = await api<any>(`/api/scenarios/drafts/${draft.draft_id}/objects`, {
      method: "POST",
      body: JSON.stringify({ object_type: objectType, ...extra }),
    });
    syncSelectionFromDraft(payload.draft);
    const createdId = payload.created_object?.object?.id ?? "";
    if (objectType === "sector") setSelectedSectorId(createdId);
    if (objectType === "control_point") setSelectedControlPointId(createdId);
    if (objectType === "zone") setSelectedZoneId(createdId);
    if (objectType === "active_group") setSelectedActiveGroupId(createdId);
    if (objectType === "reserve_group") setSelectedReserveGroupId(createdId);
    if (objectType === "deployment_restriction") setSelectedRestrictionId(createdId);
    if (objectType === "standing_order") setSelectedStandingOrderId(createdId);
    setStatusText(`Created ${labelForObjectType(objectType)} ${createdId}.`);
  };

  const duplicateObject = async (objectType: string, objectId: string, extra: Record<string, unknown> = {}) => {
    if (!draft || !objectId) return;
    const payload = await api<any>(
      `/api/scenarios/drafts/${draft.draft_id}/objects/${objectType}/${objectId}/duplicate`,
      {
        method: "POST",
        body: JSON.stringify(extra),
      },
    );
    syncSelectionFromDraft(payload.draft);
    const createdId = payload.created_object?.object?.id ?? "";
    if (objectType === "sector") setSelectedSectorId(createdId);
    if (objectType === "control_point") setSelectedControlPointId(createdId);
    if (objectType === "zone") setSelectedZoneId(createdId);
    if (objectType === "active_group") setSelectedActiveGroupId(createdId);
    if (objectType === "reserve_group") setSelectedReserveGroupId(createdId);
    if (objectType === "deployment_restriction") setSelectedRestrictionId(createdId);
    if (objectType === "standing_order") setSelectedStandingOrderId(createdId);
    setStatusText(`Duplicated ${labelForObjectType(objectType)} ${objectId}.`);
  };

  const deleteObject = async (objectType: string, objectId: string, extra: Record<string, unknown> = {}) => {
    if (!draft || !objectId) return;
    if (!window.confirm(`Delete ${labelForObjectType(objectType)} ${objectId}?`)) return;
    const payload = await api<any>(`/api/scenarios/drafts/${draft.draft_id}/objects/${objectType}/${objectId}`, {
      method: "DELETE",
      body: JSON.stringify(extra),
    });
    syncSelectionFromDraft(payload.draft);
    if (objectType === "sector") setSelectedSectorId(payload.draft.scenario.sectors?.[0]?.id ?? "");
    if (objectType === "control_point") setSelectedControlPointId(payload.draft.scenario.control_points?.[0]?.id ?? "");
    if (objectType === "zone") setSelectedZoneId(payload.draft.scenario.zones?.[0]?.id ?? "");
    if (objectType === "active_group") setSelectedActiveGroupId(payload.draft.scenario.active_groups?.[0]?.id ?? "");
    if (objectType === "reserve_group") setSelectedReserveGroupId(payload.draft.scenario.reserve_groups?.[0]?.id ?? "");
    if (objectType === "deployment_restriction") setSelectedRestrictionId(payload.draft.scenario.deployment_restrictions?.[0]?.id ?? "");
    if (objectType === "standing_order") {
      const nextStandingOrder =
        payload.draft.scenario.coalitions?.flatMap((item: any) => item.standing_orders ?? [])[0]?.id ?? "";
      setSelectedStandingOrderId(nextStandingOrder);
    }
    setStatusText(`Deleted ${labelForObjectType(objectType)} ${objectId}.`);
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
    } else if (issue.entity_type === "active_group") {
      setSelectedActiveGroupId(issue.entity_id);
    } else if (issue.entity_type === "reserve_group") {
      setSelectedReserveGroupId(issue.entity_id);
    }
  };

  const updateSectorField = (field: string, value: string | number | string[]) => {
    if (!draft || !selectedSectorId) return;
    markDraftScenario({
      ...draft.scenario,
      sectors: sectors.map((sector: any) => (sector.id === selectedSectorId ? { ...sector, [field]: value } : sector)),
    });
  };

  const updateControlPointField = (controlPointId: string, field: string, value: string | null | number) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      control_points: controlPoints.map((point: any) => (point.id === controlPointId ? { ...point, [field]: value } : point)),
    });
  };

  const updateZoneField = (field: string, value: string | number | string[]) => {
    if (!draft || !selectedZoneId) return;
    markDraftScenario({
      ...draft.scenario,
      zones: zones.map((zone: any) => (zone.id === selectedZoneId ? { ...zone, [field]: value } : zone)),
    });
  };

  const updateActiveGroup = (groupId: string, field: string, value: string | null | boolean) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      active_groups: activeGroups.map((group: any) => (group.id === groupId ? { ...group, [field]: value } : group)),
    });
  };

  const updateReserveGroup = (reserveId: string, field: string, value: string | number | boolean) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      reserve_groups: reserveGroups.map((group: any) => (group.id === reserveId ? { ...group, [field]: value } : group)),
    });
  };

  const toggleReserveAllowedSector = (reserveId: string, sectorId: string) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      reserve_groups: reserveGroups.map((reserve: any) => {
        if (reserve.id !== reserveId) return reserve;
        const current = new Set<string>(reserve.allowed_sector_ids ?? []);
        if (current.has(sectorId)) current.delete(sectorId);
        else current.add(sectorId);
        return { ...reserve, allowed_sector_ids: Array.from(current).sort() };
      }),
    });
  };

  const updateCoalitionField = (coalitionId: string, field: string, value: string | number | string[]) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      coalitions: coalitions.map((coalition: any) =>
        coalition.coalition === coalitionId ? { ...coalition, [field]: value } : coalition,
      ),
    });
  };

  const updateStandingOrder = (orderId: string, coalitionId: string, field: string, value: string | boolean) => {
    if (!draft) return;
    markDraftScenario({
      ...draft.scenario,
      coalitions: coalitions.map((coalition: any) =>
        coalition.coalition === coalitionId
          ? {
              ...coalition,
              standing_orders: (coalition.standing_orders ?? []).map((order: any) =>
                order.id === orderId ? { ...order, [field]: value } : order,
              ),
            }
          : coalition,
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
        if (current.has(sectorId)) current.delete(sectorId);
        else current.add(sectorId);
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
        if (current.has(controlPointId)) current.delete(controlPointId);
        else current.add(controlPointId);
        return { ...restriction, control_point_ids: Array.from(current).sort() };
      }),
    });
  };

  const csvList = (value: string) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

  return (
    <div className="grid studio-grid">
      <aside className="rail">
        <div className="panel">
          <h2>New Scenario</h2>
          <p className="muted">{statusText}</p>
          <label>
            Create Mode
            <select value={createMode} onChange={(event) => setCreateMode(event.target.value as CreateMode)}>
              <option value="template">From Template</option>
              <option value="blank">Blank Scenario</option>
            </select>
          </label>
          {createMode === "template" ? (
            <label>
              Template
              <select value={selectedScenarioId} onChange={(event) => setSelectedScenarioId(event.target.value)}>
                {(catalog?.scenarios ?? []).map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name} ({scenario.theater})
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <div className="stack">
              <label>
                Theater
                <select
                  value={blankScenario.theater}
                  onChange={(event) => setBlankScenario((current) => ({ ...current, theater: event.target.value }))}
                >
                  {theaters.map((theater) => (
                    <option key={theater} value={theater}>
                      {theater}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Scenario ID
                <input
                  value={blankScenario.scenario_id}
                  onChange={(event) => setBlankScenario((current) => ({ ...current, scenario_id: event.target.value }))}
                />
              </label>
              <label>
                Name
                <input value={blankScenario.name} onChange={(event) => setBlankScenario((current) => ({ ...current, name: event.target.value }))} />
              </label>
              <label>
                Summary
                <input
                  value={blankScenario.summary}
                  onChange={(event) => setBlankScenario((current) => ({ ...current, summary: event.target.value }))}
                />
              </label>
            </div>
          )}
          <div className="button-row">
            <button onClick={createDraft}>{createMode === "template" ? "Create Draft" : "Create Blank Draft"}</button>
            <span className={`state-badge ${saveState}`}>{saveBadge}</span>
          </div>
        </div>

        <div className="panel">
          <h3>Scenario Catalog</h3>
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
          <h3>All Drafts</h3>
          <div className="stack">
            {allDrafts.map((item: any) => (
              <button
                key={item.draft_id}
                className={draft?.draft_id === item.draft_id ? "list-card active" : "list-card"}
                onClick={() => openDraft(item.draft_id)}
              >
                <strong>{item.display_name}</strong>
                <span>{item.source_scenario_name ?? item.source_scenario_id}</span>
                <span>{new Date(item.updated_at).toLocaleString()}</span>
              </button>
            ))}
            {!allDrafts.length ? <p className="muted">No drafts yet.</p> : null}
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
              <button onClick={saveAsScenario}>Save To Repo</button>
            </div>
            <div className="button-row">
              <button onClick={exportScenarioToml}>Export TOML</button>
            </div>
          </div>
        ) : null}
      </aside>

      <section className="canvas-column">
        <div className="panel">
          <h2>Authoring Toolbar</h2>
          <div className="button-row wrap-row">
            <button onClick={() => setMapAddMode("sector")} disabled={!draft}>
              {mapAddMode === "sector" ? "Click Map To Place Sector" : "Add Sector"}
            </button>
            <button
              onClick={() => {
                if (!draft || !selectedSectorId) {
                  setStatusText("Select a sector before adding a control point.");
                  return;
                }
                void createObject("control_point", { sector_id: selectedSectorId });
              }}
              disabled={!draft}
            >
              Add Control Point
            </button>
            <button
              onClick={() => {
                if (!draft || !selectedSectorId) {
                  setStatusText("Select a sector before placing a zone.");
                  return;
                }
                setMapAddMode("zone");
              }}
              disabled={!draft}
            >
              {mapAddMode === "zone" ? "Click Map To Place Zone" : "Add Zone"}
            </button>
            <select value={toolbarCoalition} onChange={(event) => setToolbarCoalition(event.target.value)} disabled={!draft}>
              <option value="red">REDFOR</option>
              <option value="blue">BLUFOR</option>
            </select>
            <button onClick={() => void createObject("active_group", { coalition: toolbarCoalition })} disabled={!draft}>
              Add Active Group
            </button>
            <button onClick={() => void createObject("reserve_group", { coalition: toolbarCoalition })} disabled={!draft}>
              Add Reserve Group
            </button>
            <button onClick={() => void createObject("deployment_restriction", {})} disabled={!draft}>
              Add Restriction
            </button>
            <button onClick={() => void createObject("standing_order", { coalition: toolbarCoalition })} disabled={!draft}>
              Add Standing Order
            </button>
          </div>
        </div>

        <MapCanvas
          sectors={sectors}
          controlPoints={controlPoints}
          zones={zones}
          basemap={reference?.basemap}
          landmarks={reference?.landmarks ?? []}
          terrainSummary={reference?.terrain_summary ?? []}
          title={currentScenario?.name ?? "Scenario"}
          selectedSectorId={selectedSectorId}
          selectedControlPointId={selectedControlPointId}
          selectedZoneId={selectedZoneId}
          referenceCenterLat={reference?.default_center_lat}
          referenceCenterLng={reference?.default_center_lng}
          referenceRadiusNm={reference?.default_radius_nm}
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
          onZoneSelect={(zoneId) => {
            setSelectedZoneId(zoneId);
            setSelectedSectorId("");
            setSelectedControlPointId("");
          }}
          onMapClick={(coords) => {
            if (mapAddMode === "sector") {
              void createObject("sector", { center_lat: coords.lat, center_lng: coords.lng });
            } else if (mapAddMode === "zone" && selectedSectorId) {
              void createObject("zone", { sector_id: selectedSectorId, center_lat: coords.lat, center_lng: coords.lng });
            }
            setMapAddMode(null);
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
        {reference ? (
          <div className="panel">
            <h3>Reference Layer</h3>
            <div className="summary-grid">
              <SummaryCard title="Status" value={reference.reference_status} detail={reference.message} />
              <SummaryCard title="Airports" value={reference.airports?.length ?? 0} />
              <SummaryCard title="Landmarks" value={reference.landmarks?.length ?? 0} />
              <SummaryCard title="Theater" value={reference.theater_id} />
            </div>
          </div>
        ) : null}
      </section>

      <aside className="rail">
        <div className="panel">
          <h2>Sector Inspector</h2>
          {selectedSector ? (
            <>
              <div className="button-row">
                <button onClick={() => void duplicateObject("sector", selectedSector.id)}>Duplicate</button>
                <button onClick={() => void deleteObject("sector", selectedSector.id)}>Delete</button>
              </div>
              <label>
                Name
                <input value={selectedSector.name} disabled={!draft} onChange={(event) => updateSectorField("name", event.target.value)} />
              </label>
              <label>
                Role
                <input value={selectedSector.role} disabled={!draft} onChange={(event) => updateSectorField("role", event.target.value)} />
              </label>
              <label>
                Neighbor IDs
                <input
                  value={(selectedSector.neighbor_ids ?? []).join(", ")}
                  disabled={!draft}
                  onChange={(event) => updateSectorField("neighbor_ids", csvList(event.target.value))}
                />
              </label>
              <label>
                Tags
                <input
                  value={(selectedSector.tags ?? []).join(", ")}
                  disabled={!draft}
                  onChange={(event) => updateSectorField("tags", csvList(event.target.value))}
                />
              </label>
              <label>
                Radius (nm)
                <input type="number" value={selectedSector.radius_nm} disabled={!draft} onChange={(event) => updateSectorField("radius_nm", Number(event.target.value))} />
              </label>
              <label>
                Center Lat
                <input type="number" value={selectedSector.center_lat ?? 0} disabled={!draft} onChange={(event) => updateSectorField("center_lat", Number(event.target.value))} />
              </label>
              <label>
                Center Lng
                <input type="number" value={selectedSector.center_lng ?? 0} disabled={!draft} onChange={(event) => updateSectorField("center_lng", Number(event.target.value))} />
              </label>
            </>
          ) : (
            <p className="muted">Select a sector or create one from the toolbar.</p>
          )}
        </div>

        <div className="panel">
          <h2>Control Point Inspector</h2>
          {selectedControlPoint ? (
            <>
              <div className="button-row">
                <button onClick={() => void duplicateObject("control_point", selectedControlPoint.id)}>Duplicate</button>
                <button onClick={() => void deleteObject("control_point", selectedControlPoint.id)}>Delete</button>
              </div>
              <label>
                Name
                <input value={selectedControlPoint.name} disabled={!draft} onChange={(event) => updateControlPointField(selectedControlPoint.id, "name", event.target.value)} />
              </label>
              <label>
                Kind
                <input value={selectedControlPoint.kind} disabled={!draft} onChange={(event) => updateControlPointField(selectedControlPoint.id, "kind", event.target.value)} />
              </label>
              <label>
                Sector
                <select value={selectedControlPoint.sector_id} disabled={!draft} onChange={(event) => updateControlPointField(selectedControlPoint.id, "sector_id", event.target.value)}>
                  {sectors.map((sector: any) => (
                    <option key={sector.id} value={sector.id}>
                      {sector.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Strategic Value
                <select value={selectedControlPoint.strategic_value} disabled={!draft} onChange={(event) => updateControlPointField(selectedControlPoint.id, "strategic_value", event.target.value)}>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </label>
              <label>
                Owner
                <select value={selectedControlPoint.owner ?? ""} disabled={!draft} onChange={(event) => updateControlPointField(selectedControlPoint.id, "owner", event.target.value || null)}>
                  <option value="">Unowned</option>
                  <option value="red">REDFOR</option>
                  <option value="blue">BLUFOR</option>
                </select>
              </label>
              <label>
                Lat
                <input type="number" value={selectedControlPoint.lat ?? 0} disabled={!draft} onChange={(event) => updateControlPointField(selectedControlPoint.id, "lat", Number(event.target.value))} />
              </label>
              <label>
                Lng
                <input type="number" value={selectedControlPoint.lng ?? 0} disabled={!draft} onChange={(event) => updateControlPointField(selectedControlPoint.id, "lng", Number(event.target.value))} />
              </label>
            </>
          ) : (
            <p className="muted">Select a control point or add one from the toolbar.</p>
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
                  <div className="button-row">
                    <button onClick={() => void duplicateObject("zone", selectedZone.id)}>Duplicate</button>
                    <button onClick={() => void deleteObject("zone", selectedZone.id)}>Delete</button>
                  </div>
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
                    Tags
                    <input value={(selectedZone.tags ?? []).join(", ")} disabled={!draft} onChange={(event) => updateZoneField("tags", csvList(event.target.value))} />
                  </label>
                  <label>
                    Center Lat
                    <input type="number" value={selectedZone.center_lat ?? 0} disabled={!draft} onChange={(event) => updateZoneField("center_lat", Number(event.target.value))} />
                  </label>
                  <label>
                    Center Lng
                    <input type="number" value={selectedZone.center_lng ?? 0} disabled={!draft} onChange={(event) => updateZoneField("center_lng", Number(event.target.value))} />
                  </label>
                  <label>
                    Radius (nm)
                    <input type="number" value={selectedZone.radius_nm} disabled={!draft} onChange={(event) => updateZoneField("radius_nm", Number(event.target.value))} />
                  </label>
                </>
              ) : null}
            </>
          ) : (
            <p className="muted">No zones yet. Add one from the toolbar.</p>
          )}
        </div>

        <div className="panel">
          <h2>Active Groups</h2>
          {activeGroups.length ? (
            <>
              <select value={selectedActiveGroupId} onChange={(event) => setSelectedActiveGroupId(event.target.value)}>
                {activeGroups.map((group: any) => (
                  <option key={group.id} value={group.id}>
                    {group.id}
                  </option>
                ))}
              </select>
              {selectedActiveGroup ? (
                <>
                  <div className="button-row">
                    <button onClick={() => void duplicateObject("active_group", selectedActiveGroup.id)}>Duplicate</button>
                    <button onClick={() => void deleteObject("active_group", selectedActiveGroup.id)}>Delete</button>
                  </div>
                  <label>
                    Coalition
                    <select value={selectedActiveGroup.coalition} disabled={!draft} onChange={(event) => updateActiveGroup(selectedActiveGroup.id, "coalition", event.target.value)}>
                      <option value="red">REDFOR</option>
                      <option value="blue">BLUFOR</option>
                    </select>
                  </label>
                  <label>
                    Group Type
                    <input value={selectedActiveGroup.group_type} disabled={!draft} onChange={(event) => updateActiveGroup(selectedActiveGroup.id, "group_type", event.target.value)} />
                  </label>
                  <label>
                    Posture
                    <input value={selectedActiveGroup.posture} disabled={!draft} onChange={(event) => updateActiveGroup(selectedActiveGroup.id, "posture", event.target.value)} />
                  </label>
                  <label>
                    Sector
                    <select value={selectedActiveGroup.sector_id ?? ""} disabled={!draft} onChange={(event) => updateActiveGroup(selectedActiveGroup.id, "sector_id", event.target.value || null)}>
                      <option value="">No sector</option>
                      {sectors.map((sector: any) => (
                        <option key={sector.id} value={sector.id}>
                          {sector.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Control Point
                    <select value={selectedActiveGroup.control_point_id ?? ""} disabled={!draft} onChange={(event) => updateActiveGroup(selectedActiveGroup.id, "control_point_id", event.target.value || null)}>
                      <option value="">No control point</option>
                      {controlPoints.map((point: any) => (
                        <option key={point.id} value={point.id}>
                          {point.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="checkbox-inline">
                    <input type="checkbox" checked={Boolean(selectedActiveGroup.mobile)} disabled={!draft} onChange={(event) => updateActiveGroup(selectedActiveGroup.id, "mobile", event.target.checked)} />
                    Mobile
                  </label>
                  <label>
                    Status
                    <input value={selectedActiveGroup.status} disabled={!draft} onChange={(event) => updateActiveGroup(selectedActiveGroup.id, "status", event.target.value)} />
                  </label>
                </>
              ) : null}
            </>
          ) : (
            <p className="muted">No active groups yet. Add one from the toolbar.</p>
          )}
        </div>

        <div className="panel">
          <h2>Reserve Groups</h2>
          {reserveGroups.length ? (
            <>
              <select value={selectedReserveGroupId} onChange={(event) => setSelectedReserveGroupId(event.target.value)}>
                {reserveGroups.map((reserve: any) => (
                  <option key={reserve.id} value={reserve.id}>
                    {reserve.id}
                  </option>
                ))}
              </select>
              {selectedReserveGroup ? (
                <>
                  <div className="button-row">
                    <button onClick={() => void duplicateObject("reserve_group", selectedReserveGroup.id)}>Duplicate</button>
                    <button onClick={() => void deleteObject("reserve_group", selectedReserveGroup.id)}>Delete</button>
                  </div>
                  <label>
                    Coalition
                    <select value={selectedReserveGroup.coalition} disabled={!draft} onChange={(event) => updateReserveGroup(selectedReserveGroup.id, "coalition", event.target.value)}>
                      <option value="red">REDFOR</option>
                      <option value="blue">BLUFOR</option>
                    </select>
                  </label>
                  <label>
                    Group Type
                    <input value={selectedReserveGroup.group_type} disabled={!draft} onChange={(event) => updateReserveGroup(selectedReserveGroup.id, "group_type", event.target.value)} />
                  </label>
                  <label>
                    Cost
                    <input type="number" value={selectedReserveGroup.cost} disabled={!draft} onChange={(event) => updateReserveGroup(selectedReserveGroup.id, "cost", Number(event.target.value))} />
                  </label>
                  <label>
                    Status
                    <input value={selectedReserveGroup.status} disabled={!draft} onChange={(event) => updateReserveGroup(selectedReserveGroup.id, "status", event.target.value)} />
                  </label>
                  <label className="checkbox-inline">
                    <input type="checkbox" checked={Boolean(selectedReserveGroup.available)} disabled={!draft} onChange={(event) => updateReserveGroup(selectedReserveGroup.id, "available", event.target.checked)} />
                    Available
                  </label>
                  <label className="checkbox-inline">
                    <input type="checkbox" checked={Boolean(selectedReserveGroup.emergency)} disabled={!draft} onChange={(event) => updateReserveGroup(selectedReserveGroup.id, "emergency", event.target.checked)} />
                    Emergency
                  </label>
                  <div className="checkbox-grid">
                    {sectors.map((sector: any) => (
                      <label key={`${selectedReserveGroup.id}-${sector.id}`} className="checkbox-pill">
                        <input
                          type="checkbox"
                          checked={(selectedReserveGroup.allowed_sector_ids ?? []).includes(sector.id)}
                          disabled={!draft}
                          onChange={() => toggleReserveAllowedSector(selectedReserveGroup.id, sector.id)}
                        />
                        {sector.name}
                      </label>
                    ))}
                  </div>
                </>
              ) : null}
            </>
          ) : (
            <p className="muted">No reserve groups yet. Add one from the toolbar.</p>
          )}
        </div>

        <div className="panel">
          <h2>Restrictions</h2>
          {restrictions.length ? (
            <>
              <select value={selectedRestrictionId} onChange={(event) => setSelectedRestrictionId(event.target.value)}>
                {restrictions.map((restriction: any) => (
                  <option key={restriction.id} value={restriction.id}>
                    {restriction.id}
                  </option>
                ))}
              </select>
              {selectedRestriction ? (
                <>
                  <div className="button-row">
                    <button onClick={() => void duplicateObject("deployment_restriction", selectedRestriction.id)}>Duplicate</button>
                    <button onClick={() => void deleteObject("deployment_restriction", selectedRestriction.id)}>Delete</button>
                  </div>
                  <label>
                    Description
                    <input value={selectedRestriction.description} disabled={!draft} onChange={(event) => updateRestrictionField(selectedRestriction.id, "description", event.target.value)} />
                  </label>
                  <label>
                    Type
                    <input value={selectedRestriction.restriction_type} disabled={!draft} onChange={(event) => updateRestrictionField(selectedRestriction.id, "restriction_type", event.target.value)} />
                  </label>
                  <label>
                    Coalition
                    <select value={selectedRestriction.coalition ?? ""} disabled={!draft} onChange={(event) => updateRestrictionField(selectedRestriction.id, "coalition", event.target.value || null)}>
                      <option value="">All</option>
                      <option value="red">REDFOR</option>
                      <option value="blue">BLUFOR</option>
                    </select>
                  </label>
                  <label className="checkbox-inline">
                    <input type="checkbox" checked={Boolean(selectedRestriction.adjacency_limited)} disabled={!draft} onChange={(event) => updateRestrictionField(selectedRestriction.id, "adjacency_limited", event.target.checked)} />
                    Adjacency limited
                  </label>
                  <div className="checkbox-grid">
                    {sectors.map((sector: any) => (
                      <label key={`${selectedRestriction.id}-${sector.id}`} className="checkbox-pill">
                        <input
                          type="checkbox"
                          checked={(selectedRestriction.sector_ids ?? []).includes(sector.id)}
                          disabled={!draft}
                          onChange={() => toggleRestrictionSector(selectedRestriction.id, sector.id)}
                        />
                        {sector.name}
                      </label>
                    ))}
                  </div>
                  <div className="checkbox-grid">
                    {controlPoints.map((point: any) => (
                      <label key={`${selectedRestriction.id}-${point.id}`} className="checkbox-pill">
                        <input
                          type="checkbox"
                          checked={(selectedRestriction.control_point_ids ?? []).includes(point.id)}
                          disabled={!draft}
                          onChange={() => toggleRestrictionControlPoint(selectedRestriction.id, point.id)}
                        />
                        {point.name}
                      </label>
                    ))}
                  </div>
                </>
              ) : null}
            </>
          ) : (
            <p className="muted">No restrictions yet. Add one from the toolbar.</p>
          )}
        </div>

        <div className="panel">
          <h2>Standing Orders</h2>
          {standingOrders.length ? (
            <>
              <select value={selectedStandingOrderId} onChange={(event) => setSelectedStandingOrderId(event.target.value)}>
                {standingOrders.map((order: any) => (
                  <option key={`${order.coalition}-${order.id}`} value={order.id}>
                    {order.coalition.toUpperCase()}: {order.id}
                  </option>
                ))}
              </select>
              {selectedStandingOrder ? (
                <>
                  <div className="button-row">
                    <button onClick={() => void duplicateObject("standing_order", selectedStandingOrder.id, { coalition: selectedStandingOrder.coalition })}>Duplicate</button>
                    <button onClick={() => void deleteObject("standing_order", selectedStandingOrder.id, { coalition: selectedStandingOrder.coalition })}>Delete</button>
                  </div>
                  <label>
                    Text
                    <input
                      value={selectedStandingOrder.text}
                      disabled={!draft}
                      onChange={(event) => updateStandingOrder(selectedStandingOrder.id, selectedStandingOrder.coalition, "text", event.target.value)}
                    />
                  </label>
                  <label className="checkbox-inline">
                    <input
                      type="checkbox"
                      checked={Boolean(selectedStandingOrder.active)}
                      disabled={!draft}
                      onChange={(event) => updateStandingOrder(selectedStandingOrder.id, selectedStandingOrder.coalition, "active", event.target.checked)}
                    />
                    Active
                  </label>
                </>
              ) : null}
            </>
          ) : (
            <p className="muted">No standing orders yet. Add one from the toolbar.</p>
          )}
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
                    <span>
                      {issue.entity_type}: {issue.entity_id}
                    </span>
                    <span>{issue.message}</span>
                    {issue.suggestion ? <span>{issue.suggestion}</span> : null}
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
