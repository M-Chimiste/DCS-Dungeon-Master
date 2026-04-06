# Observation Specification

## Project

DCS Dungeon Master

## Status

Draft 0.1

## Purpose

This document defines the observation contract for Phase 1 of the commander harness. It specifies what each coalition commander sees, how the observation is constructed, how fog-of-war is enforced, and how raw simulation state is transformed into a stable, decision-ready representation.

This is a product and interface document, not an implementation guide. It describes the required behavior of the observation layer so later architecture and coding work can target a fixed contract.

## Scope

This specification applies to:

- One REDFOR commander observation
- One BLUFOR commander observation
- Phase 1 strategic ground-command focus
- A structured observation pipeline used as the primary LLM input

This specification does not require multimodal screenshots for core operation.

Because Gemma models may be used in this project and can consume visual inputs, this specification allows an optional multimodal extension path. That path is supplementary in Phase 1 and must not replace the structured observation as the canonical commander input.

## Design Goals

- Give each commander enough information to make useful strategic decisions
- Prevent either commander from receiving omniscient or unfair state
- Keep the observation stable across local and hosted model providers
- Reduce token waste through aggregation, salience, and delta reporting
- Make observations auditable, replayable, and comparable across runs

## Core Rule

The system may maintain a richer internal world model than either commander sees.

The exported observation for a commander must be a coalition-filtered view derived from:

- Coalition-permitted Olympus state
- Detection-based sensor fusion
- Scenario metadata and map abstractions
- External campaign/resource state owned by that coalition

The exported observation must never include hidden enemy facts simply because the harness has technical access to them.

## Observation Unit

An observation is a single structured snapshot produced for one commander at one decision cycle.

There are always two separate observations in a dual-commander Phase 1 run:

- `red_observation`
- `blue_observation`

These observations are generated from the same simulation time window but filtered independently by coalition.

## Decision Cadence

- Perception ingest runs continuously
- Observation generation runs on the commander decision cadence
- Phase 1 default cadence target: every 30 to 60 seconds
- Both coalition observations should be generated from a coherent snapshot boundary whenever possible

If command execution is staggered for safety, the observation timestamp must still indicate the exact snapshot time used to build each commander view.

## Primary Input Sources

### 1. Olympus

Use Olympus as a coalition-aware source for:

- Friendly unit state available to that commander
- Commander-visible map state
- Airbase ownership and related scenario state
- Mission drawings or overlays exposed to that coalition

### 2. DCS-gRPC

Use DCS-gRPC as the perception/event source for:

- Continuous event ingest
- Sensor-derived contacts via detection queries
- Additional world-state details needed to maintain the internal model

### 3. Campaign State Layer

Use the harness-owned state layer for:

- Force pools
- Reserve availability
- Attrition
- Resource budgets
- Commander intent memory
- Prior decisions and standing orders

## Observation Philosophy

Phase 1 observations should prefer commander-level abstractions over raw unit dumps.

The model should usually receive:

- Sector summaries instead of every quiet unit
- Contact summaries instead of exact hidden enemy inventories
- Recent changes since last cycle instead of full repeated history
- Mission-relevant terrain and control abstractions instead of raw geometry

The model may still receive specific unit-level detail when that detail is strategically relevant, such as:

- A high-value SAM battery under attack
- A reserve column available for redeployment
- A newly detected enemy package approaching a defended sector

If a multimodal-capable model such as Gemma is used, visual context may be attached as a secondary aid, but the authoritative commander picture must still come from the structured observation object defined in this document.

## Observation Structure

Each observation must be represented in a stable structured format. JSON is the canonical interchange format for the observation payload even if some providers later receive an equivalent text rendering.

### Top-Level Sections

Each observation must contain:

- `meta`
- `commander_state`
- `scenario_state`
- `resource_state`
- `sector_summary`
- `friendly_forces`
- `enemy_contacts`
- `air_packages`
- `map_context`
- `terrain_summary`
- `landmarks`
- `recent_changes`
- `standing_orders`
- `requests_for_decision`

## Top-Level Field Definitions

### `meta`

Describes the observation itself.

Required contents:

- Observation schema version
- Coalition for which the observation was built
- Snapshot timestamp
- Decision cycle number
- Time since previous decision for this commander
- Confidence note indicating this is a filtered, incomplete picture

### `commander_state`

Describes the commander’s own operating context.

Required contents:

- Coalition name
- Current strategic posture if set
- High-level objectives assigned to that coalition
- Current command constraints
- Optional operator instructions specific to the run

### `scenario_state`

Describes non-cheating scenario context visible or legitimately known to the commander.

Required contents:

- Theater name
- Mission time or scenario clock
- Weather summary if relevant to commander decisions
- Known control points, airbases, or sectors
- Visible or scenario-defined front status

### `resource_state`

Describes what the commander is allowed to spend or commit.

Required contents:

- Remaining deployment budget or allowance
- Available reserve pools
- Recently lost assets relevant to allocation
- Spawn or deployment restrictions
- Key shortages or bottlenecks

### `sector_summary`

Provides the main strategic map abstraction.

Each sector entry should include:

- Sector identifier
- Sector label
- Control status
- Friendly strength estimate
- Enemy threat estimate
- Contact confidence level
- Activity level
- Strategic importance
- Notable recent events

### `friendly_forces`

Summarizes own-side assets under command or relevant to the commander.

Phase 1 should favor grouped representations such as:

- Air defense clusters
- Ground maneuver groups
- Reserve groups
- Air packages if present in the scenario

Each friendly force entry should include:

- Force identifier
- Force type
- Current altitude, heading, and speed when the asset is air-capable and the data is known

### `air_packages`

Describes active coalition-controlled air packages when Air Ops v1 is enabled.

Typical contents:

- Package identifier
- Package type
- Aircraft type and count
- Origin control point
- Current posture and ROE
- Requested and normalized route legs with `altitude_ft_msl`

### `map_context`, `terrain_summary`, and `landmarks`

These sections extend the structured map picture without replacing the canonical structured observation.

Typical contents:

- Local basemap descriptor and theater bounds
- Recommended coalition-safe image attachments
- Sector terrain summaries and coast or water context
- Named landmarks such as mountains, islands, or chokepoints
- Approximate location or assigned sector
- Readiness or health
- Current task or posture
- Mobility status
- Available actions if relevant

### `enemy_contacts`

Represents only what the coalition has observed or reasonably inferred.

Each contact entry should include:

- Contact identifier
- Contact classification level
- Last known or estimated location
- Time since last confirmation
- Confidence score or band
- Detection sources summary
- Threat estimate
- Whether type, heading, and strength are known, partial, or unknown

Enemy contacts must never silently upgrade from uncertainty to certainty without a valid sensor or rule-based basis.

### `recent_changes`

Captures delta information since the previous decision cycle.

Examples:

- Newly detected contact in Sector East
- Friendly SAM battery lost near sector boundary
- Reserve group became available
- Airbase changed ownership
- Contact confidence on a tracked enemy package dropped from high to low

### `standing_orders`

Describes currently active higher-level orders already in effect for that coalition.

Examples:

- Defend Sector Bravo with priority high
- Keep reserve group R2 uncommitted
- Reposition SAM cluster S1 to fallback zone

This helps reduce repeated or conflicting decisions.

### `requests_for_decision`

Explicitly frames the decisions the model should make this cycle.

Examples:

- Reallocate reserves across sectors
- Deploy available ground air defense assets
- Reposition mobile launchers away from a threatened area
- Adjust sector priorities based on new contact activity

## Phase 1 Map Representation

Phase 1 should not expose raw screenshots as the primary map input.

Instead, the map must be represented through structured abstractions:

- Named sectors
- Control points
- Airbases and FARPs
- Scenario-defined defensive zones
- Mission drawings converted into structured annotations when possible

### Sector Model

For Phase 1, sectors are the default map abstraction.

Air Ops v1 may additionally provide a coalition-filtered local basemap, landmark set, and terrain summary so the commander can reason about mountains, coastlines, and major geographic constraints while still relying on structured state as the source of truth.

Each sector should have:

- Stable ID
- Human-readable name
- Boundary reference owned by scenario data
- Neighbor relationships if relevant
- Strategic tags such as `airbase`, `frontline`, `logistics`, `reserve_route`, or `critical_infrastructure`

The model should reason primarily at the sector level unless a more granular asset-level decision is required.

## Optional Multimodal Extension

Phase 1 may support an experimental multimodal attachment path for Gemma or other video/image-capable models.

This path is intended to help with:

- Interpreting coalition-visible map overlays that are difficult to normalize immediately
- Comparing structured-only decisions against structured-plus-visual decisions
- Operator review and debugging of commander choices

This path must not become the only way the commander understands the world in Phase 1.

When enabled, the preferred image set is:

- One full-theater coalition-safe map image
- One front-area or active-package area-of-interest crop
- The existing abstract overlay as a debug or fallback layer

### Multimodal Rules

- The structured observation remains the canonical input
- Visual inputs must be derived from coalition-permitted views only
- Visual inputs must not expose hidden enemy state that is absent from the structured observation
- Visual inputs should be low-frequency snapshots or short clips tied to the same snapshot time, not unbounded continuous video streams
- If visual evidence conflicts with structured data, the structured data remains authoritative until the discrepancy is resolved by design

### Recommended Phase 1 Use

If Gemma video or image inputs are tested in Phase 1, use them for narrow experiments rather than the default decision loop.

Recommended experimental uses:

- A rendered sector overview for the current coalition
- A coalition-visible map image with mission drawings or defensive zones
- A short visual summary clip for post-run analysis

Not recommended as default Phase 1 behavior:

- Raw full-battlefield video as the main observation source
- Visual-only contact detection without structured confirmation
- Any multimodal feed that bypasses the fog-of-war policy in this document

## Fog-Of-War Policy

Fog-of-war is a hard product requirement, not a prompt preference.

### Allowed Inputs To The Observation Layer

The observation layer may use:

- Coalition-filtered Olympus data
- Detection-based contact data
- Scenario metadata known to both sides
- This coalition’s own resources, plans, and standing orders
- Rules-based inference derived from past valid observations

### Disallowed Inputs To The Commander Observation

The commander observation must not include:

- Full enemy unit lists from omniscient streams
- Exact positions of undetected enemy units
- Exact enemy composition when not identified
- Enemy resource counts unless legitimately inferred or scenario-public
- Enemy prompts, tool calls, or internal plans
- Hidden geometry or mission data not available to that coalition

### Contact Knowledge Levels

Every enemy contact must be represented using one of these knowledge levels:

- `unknown_presence`
- `suspected_type`
- `classified_type`
- `confirmed_type`

The schema may use equivalent names later, but the semantics must remain.

### Detection Confidence

Each enemy contact must carry a confidence band:

- `low`
- `medium`
- `high`

Confidence should be influenced by:

- Number of sensors or observations supporting the track
- Sensor quality and type
- How recently the contact was observed
- Whether position, heading, and type are currently known or extrapolated

### Staleness

Contacts degrade over time.

At minimum the observation layer must support:

- Timestamp of last confirmation
- Estimated location if extrapolation is used
- Confidence decay over time
- Eventual drop or archival of stale tracks after a configured timeout

The commander should see that a stale track is stale, not a falsely precise live position.

### Inference Rules

The observation layer may present inference, but it must be marked as inference.

Examples of allowed inference:

- A contact likely remains within Sector Delta based on last known movement
- A radar emitter likely belongs to an air defense system class
- Repeated detections suggest a group rather than a single vehicle

Inference must never be presented in the same form as confirmed fact.

## Internal Model Versus Exported Observation

The internal state manager may hold:

- Rich telemetry
- Raw event history
- Force inventories
- Sensor-level observations
- Geometry and movement history

The exported observation should contain only the minimum commander-relevant subset required for this decision cycle.

This separation is mandatory because it enables:

- Anti-cheat discipline
- Smaller prompts
- Stable replay
- Better model portability

## Aggregation Rules

### Friendly Forces

Friendly forces should usually be aggregated by function and geography.

Preferred grouping examples:

- One mobile SAM group in Sector North
- One reserve armor group near Control Point Echo
- Two short-range air defense batteries covering Airbase Alpha

Do not dump every individual unit by default when a grouped summary conveys the same strategic meaning.

### Enemy Forces

Enemy information should be aggregated even more aggressively than friendly information unless a precise contact is strategically important.

Preferred forms:

- Unknown ground contact cluster in Sector West
- Probable strike package approaching from southeast
- High-confidence radar emitter near Control Point Bravo

### Event Deltas

Repeat less, highlight more.

The observation should prioritize what changed since last cycle over static restatement.

Examples of high-salience deltas:

- New contact appeared
- A defended sector lost coverage
- An important unit was destroyed
- A reserve became available
- Control status changed

## Salience Policy

When token budget forces tradeoffs, the observation layer should prefer:

- Active engagements over quiet sectors
- High-value assets over low-value assets
- New changes over unchanged background state
- Confidence-qualified threats over speculative noise
- Decision-relevant detail over descriptive clutter

Low-salience information may be omitted from the main observation and retained only in logs or debug views.

## Dual-Commander Requirements

Because Phase 1 includes both REDFOR and BLUFOR commanders, the observation layer must enforce side isolation.

Required behavior:

- Each coalition gets its own observation
- Each coalition’s observation is filtered independently
- Shared public scenario facts may appear in both
- Private coalition resources and orders must remain private
- Enemy actions are visible only through legitimate observation, event consequences, or public scenario facts

The two commanders may act on the same world, but they must not receive each other’s privileged state.

## Observation Size Guidance

The format must be concise enough for repeated use on local and hosted models.

Phase 1 guidance:

- Prefer one structured observation object per decision cycle
- Prefer summarized sectors over full unit listings
- Prefer delta sections over repeated history
- Keep optional debug-rich fields out of the default model payload

Exact token budgets may be finalized later, but the observation design should assume prompt efficiency matters.

## Default Output Form

The canonical payload should be JSON-compatible. A text rendering may be derived from it for providers or prompts that benefit from mixed formatting.

The observation builder should produce:

- A canonical structured object for logging, replay, and testing
- A model-facing rendering derived from that object
- Optional coalition-filtered visual attachments for multimodal-capable models

The model-facing rendering must not add hidden facts that were absent from the canonical object.

## Example Observation Shape

This example is illustrative and not a final wire format.

```json
{
  "meta": {
    "schema_version": "0.1",
    "coalition": "red",
    "snapshot_time": "2026-04-05T13:30:00Z",
    "decision_cycle": 12,
    "seconds_since_last_cycle": 30,
    "picture_quality": "filtered_and_incomplete"
  },
  "commander_state": {
    "posture": "defensive",
    "objectives": ["hold_sector_bravo", "protect_airbase_mineralnye"],
    "constraints": ["reserve_group_r2_do_not_commit_without_threat_high"]
  },
  "scenario_state": {
    "theater": "Caucasus",
    "mission_clock": "09:30:00",
    "weather_summary": "good visibility, light winds",
    "known_control_points": [
      {"id": "cp_bravo", "status": "friendly"},
      {"id": "cp_delta", "status": "contested"}
    ]
  },
  "resource_state": {
    "deployment_budget_remaining": 18,
    "available_reserves": [
      {"id": "reserve_sam_1", "type": "mobile_sam_section", "count": 1},
      {"id": "reserve_mech_2", "type": "mechanized_group", "count": 1}
    ],
    "restrictions": ["no_fixed_site_spawn_outside_controlled_sectors"]
  },
  "sector_summary": [
    {
      "sector_id": "sector_bravo",
      "control_status": "friendly",
      "friendly_strength": "medium",
      "enemy_threat": "medium",
      "contact_confidence": "medium",
      "activity_level": "rising",
      "strategic_importance": "high",
      "recent_notes": ["new_radar_contact_southeast_boundary"]
    }
  ],
  "friendly_forces": [
    {
      "force_id": "sam_group_b1",
      "force_type": "mobile_sam_group",
      "assigned_sector": "sector_bravo",
      "readiness": "ready",
      "task": "air_defense_cover",
      "mobility": "mobile"
    }
  ],
  "enemy_contacts": [
    {
      "contact_id": "track_204",
      "knowledge_level": "suspected_type",
      "suspected_type": "strike_aircraft_or_escort",
      "estimated_sector": "sector_bravo",
      "time_since_last_confirmation_sec": 18,
      "confidence": "medium",
      "detection_sources": ["ewr_radar", "datalink"],
      "threat_estimate": "medium"
    }
  ],
  "recent_changes": [
    "new_air_contact_track_204_detected_near_sector_bravo",
    "reserve_sam_1_became_available"
  ],
  "standing_orders": [
    "maintain_sector_bravo_priority_high",
    "hold_reserve_mech_2_for_countermove"
  ],
  "requests_for_decision": [
    "decide_whether_to_deploy_reserve_sam_1",
    "decide_whether_to_reposition_sam_group_b1"
  ]
}
```

## Validation Requirements

An observation is valid only if:

- It includes all required top-level sections
- It is tagged with schema version
- It contains no disallowed omniscient enemy facts
- Enemy contact certainty matches supporting evidence level
- Stale data is marked as stale
- Coalition-private data for the other side is absent

## Replay And Audit Requirements

Every observation used for a model decision should be persisted with:

- Snapshot timestamp
- Coalition
- Canonical observation payload
- Source run or scenario ID
- Links to resulting model actions

This supports:

- Debugging
- Fairness review
- Prompt iteration
- Cross-model comparison

## Open Questions

- What exact sector schema should Phase 1 use in the first playable scenario?
- Should friendly force summaries expose exact unit composition or use strength bands by default?
- How aggressively should stale tracks decay for different sensor types?
- Which mission drawings should be normalized into structured annotations versus omitted in Phase 1?
- Should each commander receive a short textual narrative summary in addition to the structured payload?

## Relationship To Other Documents

- `project-docs/PRD.md` defines the product goals and scope
- `project-docs/ARCHITECTURE.md` should describe how the perception layer produces this observation
- `project-docs/ACTION-SPEC.md` should define what actions can be taken in response to this observation
- `project-docs/SCENARIO-SPEC.md` should define the sectors, control points, and starting conditions used by the observation layer
- `project-docs/EVAL-PLAN.md` should define how observation quality and anti-cheat behavior are tested
