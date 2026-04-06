# Action Specification

## Project

DCS Dungeon Master

## Status

Draft 0.1

## Purpose

This document defines the Phase 1 action contract for the commander harness. It specifies what actions a coalition commander may request, what parameters those actions require, how the harness validates them, and how invalid or infeasible requests are rejected.

This is an interface and behavior document, not an implementation guide. It defines the bounded command vocabulary that the LLM may use inside the Phase 1 scenario.

## Scope

This specification applies to:

- One REDFOR commander
- One BLUFOR commander
- The Phase 1 baseline scenario in `project-docs/SCENARIO-SPEC.md`
- Strategic ground-command actions only
- Air Ops v1 package-first air tasking with terrain-aware route validation

This specification does not authorize unrestricted use of the full Olympus or DCS-gRPC command surface.

## Design Goals

- Give the model enough agency to behave like a strategic commander
- Keep the command set narrow enough to validate deterministically
- Prevent invalid, omniscient, or degenerate actions
- Make actions auditable and replayable
- Keep the action contract stable across model providers

## Core Rule

The model expresses commander intent through a bounded set of high-level actions.

The harness owns:

- Legality checks
- Resource validation
- Coalition and scenario rule enforcement
- Translation into Olympus and/or DCS-gRPC operations
- Tactical execution details between decision cycles

The model does not control raw simulator APIs directly.

## Action Model

Each decision cycle may produce zero or more action requests.

Each action request must be:

- Coalition-scoped
- Scenario-valid
- Resource-valid
- Interpretable without hidden state
- Specific enough for deterministic execution

### Canonical Form

The canonical action payload should be JSON-compatible, even if the model provider uses a tool-calling wrapper around it.

Each action object must contain:

- `action_type`
- `action_id`
- `coalition`
- `reason`

Each action type then adds its own required parameters.

### Shared Fields

#### `action_type`

The high-level command being requested.

#### `action_id`

A unique ID for correlating validation, execution, and outcome logs.

#### `coalition`

Must match the acting commander’s coalition.

#### `reason`

A short explanation of why the commander is taking the action. This is primarily for logging, audit, and later evaluation.

## Phase 1 Allowed Action Types

The Phase 1 action set is intentionally small.

Allowed action types:

- `set_sector_priority`
- `deploy_reserve_group`
- `reposition_group`
- `set_group_posture`
- `reinforce_control_point`
- `withdraw_group`
- `hold_action`
- `launch_air_package`
- `retask_air_package`
- `abort_air_package`
- `set_air_package_posture`
- `set_air_package_roe`

## Action Definitions

### 1. `set_sector_priority`

Sets the strategic importance or attention level for a sector.

Required fields:

- `sector_id`
- `priority`

Allowed values:

- `priority`: `low`, `medium`, `high`

Intent:

- Signal strategic emphasis for future execution and planning
- Rebalance attention across the sector graph

Validation rules:

- The sector must exist in the active scenario
- The sector must be visible or scenario-known to the acting coalition
- The priority value must be valid

Typical result:

- Update coalition standing orders and decision context
- No immediate simulator command may be required

### 2. `deploy_reserve_group`

Commits an available reserve group into the scenario.

Required fields:

- `reserve_group_id`
- `target_sector_id`
- `role`

Allowed values:

- `role`: `defensive`, `screening`, `support`, `fires`, `reserve`

Intent:

- Introduce a not-yet-deployed force into a valid sector under scenario rules

Validation rules:

- The reserve group must exist
- The reserve group must belong to the acting coalition
- The reserve group must be available for deployment
- The target sector must be valid for deployment under scenario rules
- The coalition must have sufficient budget or allowance
- The requested role must be compatible with the group type

Typical result:

- Deduct resource cost
- Mark reserve as committed
- Spawn or activate the group through the harness execution layer

### 3. `reposition_group`

Moves a currently active group to a new sector, control point, zone, or fallback location.

Required fields:

- `group_id`
- `destination_type`
- `destination_id`

Allowed values:

- `destination_type`: `sector`, `control_point`, `zone`

Intent:

- Shift an existing group to a more useful or safer position

Validation rules:

- The group must exist and belong to the acting coalition
- The group must be mobile or reposition-capable
- The destination must exist
- The movement must comply with scenario routing rules
- The destination must not be a prohibited no-deploy or no-route area
- The request must not conflict with a hard lock such as destruction, withdrawal, or unavailable state

Typical result:

- The execution layer translates the reposition request into waypoints, route assignment, and movement posture

### 4. `set_group_posture`

Changes the role or behavioral posture of an existing active group.

Required fields:

- `group_id`
- `posture`

Allowed values:

- `posture`: `defensive`, `reserve`, `screening`, `support`, `fallback`

Intent:

- Change how a group should behave strategically without necessarily moving it

Validation rules:

- The group must exist and belong to the acting coalition
- The posture value must be valid
- The posture must be compatible with the group type and current state

Typical result:

- Update standing orders for that group
- May trigger downstream changes to ROE, alarm state, movement permissiveness, or support assignment

### 5. `reinforce_control_point`

Assigns one or more active or newly deployed groups to support a named control point.

Required fields:

- `control_point_id`
- `group_ids`
- `reinforcement_role`

Allowed values:

- `reinforcement_role`: `defend`, `screen`, `support`, `fallback_cover`

Intent:

- Strengthen a rear or forward position without requiring the model to script detailed unit routes

Validation rules:

- The control point must exist
- The control point must be friendly, contested, or otherwise valid for reinforcement under scenario rules
- Every listed group must belong to the acting coalition
- Every listed group must be available and compatible with the requested reinforcement role

Typical result:

- Convert the action into movement, posture, and local defense assignment for the selected groups

### 6. `withdraw_group`

Pulls an active group out of a threatened area toward a safer location.

Required fields:

- `group_id`
- `fallback_destination_type`
- `fallback_destination_id`

Allowed values:

- `fallback_destination_type`: `sector`, `control_point`, `zone`

Intent:

- Preserve a high-value or threatened asset rather than leaving it exposed

Validation rules:

- The group must exist and belong to the acting coalition
- The fallback destination must exist and be legal for that group
- The group must not already be destroyed, withdrawn, or otherwise unavailable

Typical result:

- Route the group away from the threatened location
- Update group posture to a withdrawal or fallback-compatible mode internally

### 7. `hold_action`

Explicitly chooses not to change the current plan this cycle.

Required fields:

- `scope`

Allowed values:

- `scope`: `global`, `sector`, `group`

Optional fields:

- `sector_id`
- `group_id`

Intent:

- Allow the model to make a deliberate no-change decision
- Avoid forcing meaningless churn every cycle

Validation rules:

- The scope must be valid
- If scoped to a sector or group, the referenced entity must exist and belong to the acting coalition when relevant

Typical result:

- No new simulator-side movement or deployment
- Log that the commander intentionally preserved current posture

## Air Ops v1 Extension

Air Ops v1 keeps the same bounded-control rule while allowing package-first air tasking:

- The model may request route legs and `altitude_ft_msl` values
- The harness resolves sectors, control points, zones, landmarks, and coalition-visible contacts into concrete geometry
- The harness samples terrain along each leg and raises unsafe altitudes when a safe correction is possible
- Raw coordinates are allowed for transit or orbit legs, not for hidden-target attack authoring

### `launch_air_package`

Required fields:

- `inventory_id`
- `package_type`
- `aircraft_count`
- `route_legs`

Optional fields:

- `target_reference_type`
- `target_reference_id`
- `posture`
- `roe`

### `retask_air_package`

Required fields:

- `package_id`
- `route_legs`

Optional fields:

- `target_reference_type`
- `target_reference_id`

### `abort_air_package`

Required fields:

- `package_id`
- `abort_reason`

### `set_air_package_posture`

Required fields:

- `package_id`
- `posture`

### `set_air_package_roe`

Required fields:

- `package_id`
- `roe`

## Phase 1 Disallowed Action Types

The following are explicitly out of scope for the model in Phase 1:

- Arbitrary raw unit spawning
- Direct effect spawning for smoke, flares, explosions, or spectacle
- Per-aircraft freeform micromanagement outside the bounded package contract
- Direct attack-task authoring against exact hidden enemy positions
- Arbitrary deletion of friendly or enemy units
- Access to raw Olympus or DCS-gRPC methods not wrapped by this action contract

## Coalition Isolation

An action may affect only the acting coalition’s own assets and standing orders.

The model must not be able to:

- Move enemy groups directly
- Change enemy priorities or posture
- Query enemy-only identifiers not exposed in its observation
- Submit mixed-coalition action payloads

## Action Batching

Multiple actions may be submitted in one decision cycle.

Phase 1 guidance:

- Prefer a small number of meaningful actions per cycle
- Avoid action spam for tiny tactical adjustments
- The harness may impose a max actions per cycle limit

Suggested initial limit:

- Up to 3 accepted non-hold actions per commander per cycle

This limit may be tuned later in evaluation.

## Validation Stages

Every action request must pass all of the following stages before execution.

### 1. Schema Validation

Checks:

- Required fields present
- Allowed enum values only
- Correct field types
- No unsupported action types

### 2. Coalition Validation

Checks:

- Action coalition matches acting commander
- Referenced groups and reserves belong to the acting coalition
- Referenced entities are visible or scenario-valid for that coalition

### 3. Scenario Validation

Checks:

- Referenced sectors and control points exist
- Destination and deployment rules are respected
- No-deploy and no-route constraints are respected
- The action is in scope for the active scenario

### 4. Resource Validation

Checks:

- Budget or allowance is sufficient
- Reserve is still available
- Group is not destroyed, exhausted, or already committed in an incompatible way

### 5. Conflict Validation

Checks:

- Action does not conflict with another accepted action in the same cycle
- Action does not duplicate an already active standing order without purpose
- Action sequence is internally coherent

### 6. Execution Readiness Validation

Checks:

- The execution layer can translate the action into bounded simulator operations
- Required target entities or map references are resolvable

## Rejection Model

Invalid or infeasible actions must be rejected with machine-readable reasons.

Each rejected action should return:

- `action_id`
- `status`
- `rejection_code`
- `message`

### `status`

Must be one of:

- `accepted`
- `rejected`
- `partially_accepted`

### Standard Rejection Codes

Phase 1 should support at minimum:

- `unknown_action_type`
- `missing_required_field`
- `invalid_field_value`
- `coalition_mismatch`
- `unknown_entity`
- `entity_not_owned`
- `entity_unavailable`
- `destination_invalid`
- `destination_restricted`
- `insufficient_budget`
- `reserve_not_available`
- `action_conflict`
- `scenario_rule_violation`
- `execution_not_supported`

## Partial Acceptance

If a batched submission contains multiple actions, the harness may accept some and reject others.

The harness should not silently rewrite action meaning. If it must narrow or adapt an action for safety, it should do so only under explicit, documented normalization rules.

## Normalization Rules

Phase 1 normalization should be minimal and predictable.

Allowed examples:

- Convert a control-point reinforcement request into a legal nearby zone or route target
- Convert a broad reposition request into the nearest scenario-approved destination

Not allowed:

- Inventing undeclared targets
- Upgrading a vague request into a more aggressive action than requested
- Spending resources the commander did not ask to spend

## Action Outcomes

Every accepted action should produce an execution result record.

Minimum fields:

- `action_id`
- `status`
- `execution_summary`
- `resulting_entities`
- `resource_delta`
- `standing_order_delta`

This allows later replay and evaluation to connect intent with world changes.

## Example Action Payload

This example is illustrative and not a final wire format.

```json
[
  {
    "action_type": "set_sector_priority",
    "action_id": "act_001",
    "coalition": "red",
    "sector_id": "central_west",
    "priority": "high",
    "reason": "Enemy contact confidence is rising in the western approach."
  },
  {
    "action_type": "deploy_reserve_group",
    "action_id": "act_002",
    "coalition": "red",
    "reserve_group_id": "reserve_sam_1",
    "target_sector_id": "red_front",
    "role": "defensive",
    "reason": "Forward air defense coverage is insufficient for likely incoming threat."
  },
  {
    "action_type": "reposition_group",
    "action_id": "act_003",
    "coalition": "red",
    "group_id": "fires_group_r1",
    "destination_type": "zone",
    "destination_id": "fallback_zone_alpha",
    "reason": "Current location is too exposed to likely counterfire."
  }
]
```

## Example Validation Result

```json
[
  {
    "action_id": "act_001",
    "status": "accepted",
    "execution_summary": "Sector priority updated to high for central_west."
  },
  {
    "action_id": "act_002",
    "status": "accepted",
    "execution_summary": "reserve_sam_1 committed to red_front as defensive group.",
    "resource_delta": {"deployment_budget_remaining": -6}
  },
  {
    "action_id": "act_003",
    "status": "rejected",
    "rejection_code": "destination_restricted",
    "message": "fallback_zone_alpha is not legal for fires_group_r1 in the active scenario."
  }
]
```

## Logging Requirements

The harness should persist, per action:

- Raw action payload
- Validation result
- Rejection reason or execution summary
- Resource changes
- Resulting standing-order changes
- Simulator execution correlation data where available

## Dual-Commander Requirements

Because both coalitions act in Phase 1, the action layer must support two independent command streams.

Required behavior:

- Each commander submits only its own coalition actions
- Validation is applied independently per coalition
- Conflicts are resolved at the scenario/world level, not by leaking side-private context
- Accepted actions from one side may affect the next observation for the other side only through legitimate game consequences

## Open Questions

- Should Phase 1 include an explicit `commit_emergency_reserve` action or keep that under `deploy_reserve_group`?
- Should `reinforce_control_point` accept exactly one group or a small list of groups by default?
- Should the harness cap reposition distance per cycle for large mobile groups?
- Should any limited air-layer actions be exposed in Phase 1, or remain fully deterministic and external to the commander tool set?

## Relationship To Other Documents

- `project-docs/PRD.md` defines the product goals and bounded-command philosophy
- `project-docs/OBSERVATION-SPEC.md` defines the inputs that drive these actions
- `project-docs/SCENARIO-SPEC.md` defines where these actions are legal and useful
- `project-docs/ARCHITECTURE.md` should define how actions are validated and translated into simulator commands
- `project-docs/EVAL-PLAN.md` should define how action quality, validity, and usefulness are measured
