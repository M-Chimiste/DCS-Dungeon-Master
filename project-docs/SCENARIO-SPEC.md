# Scenario Specification

## Project

DCS Dungeon Master

## Status

Draft 0.1

## Purpose

This document defines the first playable scenario for Phase 1 of the commander harness. It specifies the battlefield shape, coalition starting conditions, sector model, resource constraints, allowed commander actions, and success conditions.

This is the scenario contract that the observation, action, architecture, and evaluation documents should target first.

## Scenario Name

Phase 1 Baseline: Persian Gulf Frontier

## Design Intent

The first scenario should feel active and contested without becoming so large that it hides product problems behind scale.

This scenario is designed to validate:

- Dual autonomous commanders operating at the same time
- Honest fog-of-war handling
- Sector-based strategic reasoning
- Ground asset deployment and repositioning
- Resource-constrained decision-making
- Replayable evaluation across different LLMs

## Scope

The scenario is intentionally smaller than a full dynamic campaign.

Included:

- One REDFOR strategic commander
- One BLUFOR strategic commander
- A bounded Persian Gulf battlespace
- Sector-based map reasoning
- Ground-based strategic assets such as SAMs, missile launchers, and ground vehicle groups
- Limited air activity as background pressure and contact generation where useful

Excluded from the initial scenario:

- Full-map operations across the entire theater
- Detailed logistics simulation
- High-density aircraft package warfare as the primary gameplay loop
- Tactical aircraft micromanagement
- Naval warfare as a core requirement

## Baseline Assumptions

- Theater: Persian Gulf
- Gameplay focus: strategic ground and air-defense positioning under contested conditions
- Time horizon per run: 30 to 60 minutes
- Commander decision cadence: 30 to 60 seconds
- Both commanders begin with incomplete but reasonable knowledge of the scenario layout
- Both commanders can deploy and reposition approved assets, but neither side has infinite spawning authority

## Battlefield Shape

The battlefield should be modeled as a compact corridor of contested sectors rather than a whole-theater sandbox.

### Sector Model

Phase 1 uses named sectors as the primary map abstraction.

The baseline scenario contains seven sectors:

- `red_rear`
- `red_front`
- `central_west`
- `central_east`
- `blue_front`
- `blue_rear`
- `central_air_corridor`

### Sector Roles

- `red_rear`: primary REDFOR reserve and staging sector
- `red_front`: forward REDFOR defensive line
- `central_west`: contested ground approach sector
- `central_east`: contested ground approach sector
- `blue_front`: forward BLUFOR defensive line
- `blue_rear`: primary BLUFOR reserve and staging sector
- `central_air_corridor`: key air and sensor approach zone spanning the contested center

### Sector Relationships

The scenario should treat sectors as a connected graph.

Minimum neighbor logic:

- `red_rear` connects to `red_front`
- `red_front` connects to `central_west`, `central_east`, and `central_air_corridor`
- `blue_rear` connects to `blue_front`
- `blue_front` connects to `central_west`, `central_east`, and `central_air_corridor`
- `central_west` and `central_east` connect to each other
- `central_air_corridor` overlaps the central contested zone for air-defense and air-contact reasoning

This graph is sufficient for Phase 1 sector priority, reserve routing, and threat summaries.

## Control Points And Infrastructure

The scenario should define a small number of control points and infrastructure nodes.

### Required Control Points

- One REDFOR rear airbase
- One BLUFOR rear airbase
- One REDFOR forward support point or FARP-equivalent location
- One BLUFOR forward support point or FARP-equivalent location
- Two contested central ground objectives, one in `central_west` and one in `central_east`

### Control Point Behavior

- Rear airbases are initially friendly and high-value
- Forward support points are initially friendly but more exposed
- Central objectives begin contested or lightly held
- Loss of a rear airbase creates a severe strategic penalty
- Loss of a forward support point creates a moderate penalty and weakens deployment flexibility

## Starting Knowledge

Each commander starts with:

- Full knowledge of sector names and public boundaries
- Full knowledge of friendly starting control points
- General knowledge that the opposing side has rear, forward, and reserve capacity
- No omniscient knowledge of exact enemy force placement

Both sides may know scenario-public facts such as:

- The number and names of sectors
- Public victory conditions
- Public action restrictions
- Publicly declared strategic infrastructure

Neither side should begin with:

- Exact enemy reserve composition
- Exact enemy SAM placement
- Exact enemy mobile group location

## Initial Force Design

The first scenario should use bounded, intentionally asymmetric but roughly comparable forces.

### Friendly Force Categories Per Coalition

Each coalition starts with some combination of:

- Fixed or semi-fixed rear air-defense coverage
- One forward air-defense group
- One mobile ground maneuver group
- One long-range fires or missile launcher group
- One deception or low-value support group if useful for testing ambiguity

### Phase 1 Force Philosophy

- Enough assets to create meaningful tradeoffs
- Not so many assets that the observation becomes a unit spreadsheet
- Mobile groups matter more than total count
- High-value assets should be few enough to be strategically legible

### Suggested Baseline Initially Deployed Composition

This is a planning baseline for forces that begin active in the scenario, not a locked ORBAT.

Per coalition, initially deployed:

- 1 rear air-defense cluster
- 1 forward air-defense group
- 1 ground maneuver group
- 1 fires group or missile launcher group
- 1 to 2 small recon or screening groups

This creates enough decisions for:

- Sector prioritization
- Reserve commitment
- Air-defense layering
- Repositioning under threat

## Reserve Pools

Both coalitions should have a limited reserve pool outside the initially deployed forces.

### Reserve Rules

- Reserves belong to the campaign state layer, not directly to the simulator
- Reserves can only be deployed if budget and scenario rules allow
- Reserve commitment should feel meaningful and partially irreversible
- Not all reserves should be available at scenario start

### Suggested Reserve Types

Per coalition:

- 1 mobile SAM reserve
- 1 mechanized reserve
- 1 short-range air-defense reserve
- Optional 1 high-value emergency reserve unlocked by losses or threat escalation

## Action Scope For This Scenario

The scenario must constrain commander actions to match Phase 1 product goals.

### Allowed Actions

- Set sector priority
- Deploy approved reserve groups into valid sectors
- Reposition mobile ground and air-defense assets
- Change posture of a group between defensive, reserve, screening, or support roles
- Reinforce a threatened control point
- Withdraw a mobile asset from a dangerous sector

### Restricted Actions

- No arbitrary spawning anywhere on the map
- No unrestricted mass reinforcement
- No tactical aircraft micro-control as a primary gameplay mechanic
- No direct use of hidden enemy state
- No unrestricted destruction or effect spawning purely for spectacle

### Optional Limited Air Layer

To make the battlefield feel alive, the scenario may include low-volume scripted or deterministic air activity.

Examples:

- Periodic reconnaissance flights
- Limited strike threat windows
- Small interceptor or CAP presence abstracted as sector pressure

This air layer should support commander decision-making, not replace the ground-focused loop.

## Resource Model

The scenario should include a simple but meaningful resource system.

### Minimum Resource Fields

- Deployment budget
- Reserve availability
- Replacement scarcity
- Sector-specific deployment restrictions

### Resource Design Goals

- Prevent unlimited spawning
- Force tradeoffs between immediate reinforcement and future flexibility
- Make losses matter
- Keep accounting simple enough for Phase 1 debugging

### Suggested Baseline Rules

- Each coalition starts with a fixed deployment budget
- Each reserve group has a cost
- Some groups may only be deployed into friendly or adjacent sectors
- High-value air-defense assets should cost enough that misuse is visible

## Fog-Of-War Requirements In This Scenario

The scenario must actively support fog-of-war testing.

### Scenario-Level Requirements

- Both sides must have legitimate opportunities to detect but not fully identify enemy threats
- Mobile units should be able to reposition without guaranteed immediate enemy awareness
- Some central-sector ambiguity should remain unless recon or sensors clarify it
- At least one class of high-value asset should be vulnerable to stale or partial tracking

### Testing Value

This scenario should let us observe whether the commanders:

- Overcommit to low-confidence contacts
- Correctly defend against uncertain threats
- Reposition high-value assets when likely exposed
- Respect incomplete information instead of behaving omnisciently

## Mission Drawings And Overlays

The first scenario may include structured mission drawings or overlays that help define:

- Sector boundaries
- Protected zones
- Rear-area no-deploy zones
- Priority approach corridors

If present, these should be normalizable into structured annotations for the observation layer.

## Victory Conditions

The scenario should have clear, machine-evaluable victory conditions.

### Primary Victory Conditions

A coalition wins if it achieves one of the following:

- Holds both central contested objectives at scenario end
- Captures or neutralizes the opposing forward support point while preserving its own rear airbase
- Inflicts decisive strategic degradation by destroying or denying the enemy’s ability to contest the center

### Loss Conditions

A coalition is in a losing state if:

- Its rear airbase is lost or rendered non-viable
- It cannot contest any central sector for a sustained period
- It exhausts meaningful defensive capability while the opponent retains central control

### Draw Conditions

The scenario should allow a draw when:

- Each side holds its rear and forward lines
- Central objectives remain split or contested
- Neither side achieves decisive strategic displacement by scenario end

## Behavioral Success Signals

Even before full balance tuning, the scenario is successful if it produces runs where:

- Both commanders issue meaningful repeated decisions
- Reserves are not dumped immediately without reason
- High-value assets are repositioned in response to changing threats
- Sector priorities visibly shift over time
- The center remains contested in some runs and collapses in others

## Evaluation Hooks

This scenario should support side-by-side evaluation across models and settings.

Minimum required run metadata:

- Model used per coalition
- Decision cadence
- Scenario seed if randomness is used
- Initial force template version
- Resource settings version
- Outcome summary

## Default Scenario Parameters

These are recommended starting defaults for early testing.

- Run duration target: 45 minutes
- Decision interval: 30 seconds
- Sides: 1 REDFOR commander, 1 BLUFOR commander
- Sector count: 7
- Central objectives: 2
- Rear airbases: 2 total
- Forward support points: 2 total
- Reserve groups per coalition: 3 to 4
- Air activity level: low
- Fog-of-war strictness: high

## Future Variants

Once the baseline scenario is stable, later variants may explore:

- Larger sector graphs
- More asymmetric force pools
- Higher air pressure
- Logistics-heavy attrition
- Multimodal map attachments for selected runs

These variants should come after the baseline scenario is reliable and replayable.

## Open Questions

- Which exact Persian Gulf locations should map to the rear airbases, forward support points, and central objectives?
- How much asymmetry should the first force template have, if any?
- Should central objectives be captured by physical occupation, area control, or accumulated contest score?
- Should the limited air layer be scripted, deterministic, or partially model-driven in Phase 1?
- Which groups should be initially visible to each side versus discoverable only through play?

## Relationship To Other Documents

- `project-docs/PRD.md` defines the product goals and Phase 1 scope
- `project-docs/OBSERVATION-SPEC.md` defines how this scenario is presented to each commander
- `project-docs/ACTION-SPEC.md` should define what commanders are allowed to do inside this scenario
- `project-docs/ARCHITECTURE.md` should define how the scenario state is represented and advanced
- `project-docs/EVAL-PLAN.md` should define how runs on this scenario are measured and compared
