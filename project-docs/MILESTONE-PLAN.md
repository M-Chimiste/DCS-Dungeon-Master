# Milestone-Based Implementation Plan

## Project

DCS Dungeon Master

## Status

Draft 0.1

## Purpose

This document converts the planning documents into a milestone-based implementation plan for Phase 1. It defines the recommended build order, key workstreams, milestone goals, concrete tasks, expected deliverables, dependencies, and exit criteria.

This is the bridge between planning and coding.

## Scope

This implementation plan covers the Phase 1 system defined by:

- `project-docs/PRD.md`
- `project-docs/OBSERVATION-SPEC.md`
- `project-docs/SCENARIO-SPEC.md`
- `project-docs/ACTION-SPEC.md`
- `project-docs/ARCHITECTURE.md`
- `project-docs/EVAL-PLAN.md`

Phase 1 assumptions remain:

- One REDFOR commander
- One BLUFOR commander
- Strategic ground-command focus
- Sector-based scenario abstraction
- Structured observations as the canonical model input
- Optional multimodal experiments only after the structured path works

## Implementation Strategy

The build should proceed from the inside out:

1. Define stable internal contracts and domain models
2. Stand up ingest and state management
3. Build the observation path
4. Build the bounded action path
5. Connect model backends
6. Run dry loops before live execution
7. Add evaluation and tuning support

This avoids a common failure mode where the model is integrated too early, before the world model, validation path, and replay artifacts are trustworthy.

## Workstreams

The implementation naturally breaks into these workstreams:

- Core domain models
- Scenario and campaign state
- Simulation integration
- Sensor fusion and fog-of-war
- Observation building
- Model adapter layer
- Action validation
- Execution layer
- Persistence and replay
- Operator controls and debugging
- Evaluation tooling

Some of these can proceed in parallel after the domain model and scenario contracts are established.

## Recommended Milestones

## Milestone 0: Project Skeleton And Contracts

### Goal

Create the initial codebase shape and encode the domain contracts that everything else depends on.

### Tasks

- Choose the Phase 1 implementation language and runtime conventions
- Create the initial project structure
- Define core domain objects for scenario, sectors, control points, groups, reserves, contacts, observations, actions, and execution results
- Define config loading for local, LAN, and hosted model backends
- Define schema/version constants for observations and actions
- Add basic logging setup
- Add dry-run friendly startup path

### Deliverables

- Initial source tree
- Configuration templates
- Domain model definitions
- Basic app entrypoint
- Schema version constants

### Dependencies

- Existing planning docs only

### Exit Criteria

- The repo has a coherent source structure
- Core internal object families exist
- The app can start in a no-op or dry-run mode without crashing

## Milestone 1: Scenario State And Persistence Foundation

### Goal

Build the scenario and persistence foundation that defines what world exists and what is legal.

### Tasks

- Encode the `Phase 1 Baseline: Persian Gulf Frontier` scenario
- Represent the sector graph and control points
- Represent coalition state, starting forces, and reserve pools
- Represent resource budgets and deployment restrictions
- Choose initial persistence pattern for Phase 1
- Add event logging for state transitions
- Add current-state storage for active groups, reserves, and standing orders

### Deliverables

- Scenario loader
- Scenario definition format
- Current-state store
- Event log format
- Seeded baseline scenario state

### Dependencies

- Milestone 0

### Exit Criteria

- The harness can load the baseline scenario deterministically
- Coalition state, sectors, reserves, and resource limits are queryable
- Scenario state changes can be persisted and replayed at a basic level

## Milestone 2: Simulation Integration Layer

### Goal

Connect to DCS-adjacent interfaces and normalize raw data into the internal model.

### Tasks

- Build Olympus client for required Phase 1 reads and writes
- Build DCS-gRPC client for event and perception ingest
- Normalize source payloads into internal objects
- Handle network configuration for same-machine and remote DCS host deployments
- Add connection health reporting
- Add interface error handling and reconnect strategy

### Deliverables

- Olympus integration module
- DCS-gRPC integration module
- Source payload normalization layer
- Connectivity and health reporting

### Dependencies

- Milestone 0
- Milestone 1 for internal object targets

### Exit Criteria

- The harness can connect to Olympus and DCS-gRPC
- Raw state and events can be ingested into internal structures
- Remote DCS host topology is supported in configuration

## Milestone 3: Sensor Fusion And Fog-Of-War Enforcement

### Goal

Create the anti-cheat boundary that turns raw perception into coalition-safe knowledge.

### Tasks

- Build separate knowledge pictures for REDFOR and BLUFOR
- Implement contact tracks with confidence, staleness, and inference markers
- Enforce the rule that hidden enemy truth is not exposed to commanders
- Add track update rules based on valid detections and event consequences
- Add stale-track decay behavior
- Add debugging views for internal truth versus exported coalition picture

### Deliverables

- Sensor fusion service
- Contact track model
- Coalition-filtered knowledge state
- Fog-of-war debugging helpers

### Dependencies

- Milestone 1
- Milestone 2

### Exit Criteria

- Internal truth and commander-visible truth are clearly separated
- Coalition pictures can be generated independently
- The team can inspect why a contact is known, suspected, stale, or hidden

## Milestone 4: Observation Pipeline

### Goal

Generate the canonical commander observations defined in `project-docs/OBSERVATION-SPEC.md`.

### Tasks

- Build observation assembly for `meta`, `commander_state`, `scenario_state`, `resource_state`, `sector_summary`, `friendly_forces`, `enemy_contacts`, `recent_changes`, `standing_orders`, and `requests_for_decision`
- Implement sector summarization and salience rules
- Implement delta generation between decision cycles
- Build model-facing renderers from the canonical structured object
- Add optional visual attachment stubs for future Gemma experiments
- Persist observations for replay and review

### Deliverables

- Canonical observation builder
- Model-facing observation renderer
- Observation persistence format
- Dry-run observation inspection output

### Dependencies

- Milestone 1
- Milestone 3

### Exit Criteria

- Both coalitions can receive valid observation payloads
- Observation payloads match the spec structure
- Observations are small enough to inspect and reason about

## Milestone 5: Action Validation Layer

### Goal

Implement the bounded command vocabulary and the validation gates that protect the simulation.

### Tasks

- Define action schemas for all Phase 1 action types
- Implement schema validation
- Implement coalition validation
- Implement scenario validation
- Implement resource validation
- Implement conflict validation
- Implement normalized rejection responses
- Add partial acceptance behavior for batched actions

### Deliverables

- Action schema definitions
- Validation pipeline
- Rejection code model
- Action validation test fixtures

### Dependencies

- Milestone 1
- Milestone 4

### Exit Criteria

- All allowed action types can be parsed and validated
- Invalid actions receive machine-readable rejection reasons
- The validator can reject hidden-state or scenario-illegal requests cleanly

## Milestone 6: Model Adapter Layer And Dry Decision Loop

### Goal

Connect models to the observation and action pipeline without executing live simulator actions yet.

### Tasks

- Build a provider-neutral model adapter interface
- Implement at least one local or LAN backend
- Implement at least one hosted-provider backend
- Support structured tool use / bounded action output
- Run a dry decision loop for both commanders
- Persist model requests and responses
- Add timeout and fallback handling

### Deliverables

- Model adapter interface
- Initial backend implementations
- Dual dry-loop runner
- Request/response logging

### Dependencies

- Milestone 4
- Milestone 5

### Exit Criteria

- Both commanders can receive observations and return action proposals
- Actions flow through validation successfully in dry-run mode
- At least one local/LAN backend and one hosted backend are usable

## Milestone 7: Execution Layer And Live Command Loop

### Goal

Translate accepted strategic actions into live simulator behavior.

### Tasks

- Implement translation from validated actions to Olympus and/or DCS-gRPC operations
- Implement standing-order tracking
- Implement execution state for deployed and active groups
- Implement group repositioning behavior
- Implement deployment and reserve activation behavior
- Implement posture changes and control-point reinforcement behavior
- Implement failure handling when simulator-side commands fail
- Support live loop and dry-run loop using the same action interfaces

### Deliverables

- Execution engine
- Standing-order manager
- Live command loop
- Simulator command result tracking

### Dependencies

- Milestone 2
- Milestone 5
- Milestone 6

### Exit Criteria

- Accepted actions can result in visible simulator-side effects
- Failed executions are logged without collapsing the whole run
- The live loop can run both coalitions through multiple cycles

## Milestone 8: Operator Controls, Replay, And Inspection

### Goal

Make the system inspectable enough to debug and tune.

### Tasks

- Add run start, pause, resume, and stop controls
- Add dry-run toggle
- Add inspection views or outputs for latest observations and actions
- Add run summary generation
- Add replay-friendly export of observations, actions, and outcomes
- Add comparison-friendly metadata capture

### Deliverables

- Operator control surface
- Run summaries
- Replay artifacts
- Basic run comparison output

### Dependencies

- Milestone 6
- Milestone 7

### Exit Criteria

- A developer can inspect what each commander saw, proposed, and executed
- A run can be paused safely
- A run summary can be generated without manual log digging

## Milestone 9: Evaluation Harness And Baseline Tuning

### Goal

Make the system measurable against `project-docs/EVAL-PLAN.md`.

### Tasks

- Encode baseline run metadata capture
- Implement evaluation metrics collection
- Implement rejection-rate and loop-health summaries
- Implement fairness review hooks
- Run the required baseline test matrix
- Identify prompt, cadence, and schema issues
- Tune the baseline until the system is stable enough for continued coding

### Deliverables

- Evaluation artifact generation
- Metric summaries
- Baseline comparison reports
- Initial tuning notes

### Dependencies

- Milestone 8

### Exit Criteria

- Baseline runs can be compared across models and settings
- The team can detect likely hidden-state leakage or poor strategic behavior
- The system is stable enough to start expanding capability after Phase 1

## Parallelization Guidance

After Milestone 0 and Milestone 1, some work can proceed in parallel.

Reasonable parallel groupings:

- Simulation integration and persistence improvements
- Sensor fusion and observation builder
- Action validation and model adapter work
- Operator tooling and evaluation tooling once basic loop artifacts exist

Avoid parallelizing too aggressively before domain objects and scenario rules stabilize.

## Recommended Coding Sequence

If we want the shortest path to a useful first end-to-end result, the sequence should be:

1. Milestone 0
2. Milestone 1
3. Milestone 2
4. Milestone 3
5. Milestone 4
6. Milestone 5
7. Milestone 6
8. Milestone 7
9. Milestone 8
10. Milestone 9

The first “real proof” point is the dry dual-commander loop at Milestone 6. The first “live proof” point is Milestone 7.

## Suggested Initial Coding Sprint

The best first coding sprint is a narrow vertical slice.

Recommended Sprint 1 target:

- Basic project skeleton
- Baseline scenario loader
- Core domain models
- Stub persistence
- Stub observation object generation
- Stub action schema parsing

Why:

- It creates a stable substrate for the rest of the work
- It lets later integration code target real types instead of guesses
- It reduces refactor risk when the simulator interfaces are added

## Risks During Implementation

- Integrating model backends too early before the world contracts stabilize
- Allowing raw simulator data to bypass coalition filtering
- Letting the execution layer grow into tactical micromanagement
- Treating Olympus as the source of all truth instead of separating simulation, scenario, and commander-facing truth
- Adding multimodal inputs before the structured path is stable

## Definition Of “Ready To Code”

We are ready to begin coding now that:

- The product scope is defined
- The observation contract is defined
- The action contract is defined
- The baseline scenario is defined
- The architecture is defined
- The evaluation plan is defined
- The milestone sequence is defined

## Next Immediate Step

Begin Milestone 0 by creating the project skeleton, core domain objects, and baseline configuration layout.

## Relationship To Other Documents

- `project-docs/PRD.md` defines the product direction
- `project-docs/OBSERVATION-SPEC.md` defines the input contract for commanders
- `project-docs/ACTION-SPEC.md` defines the output contract for commanders
- `project-docs/SCENARIO-SPEC.md` defines the first battlefield and legal scenario space
- `project-docs/ARCHITECTURE.md` defines the system structure
- `project-docs/EVAL-PLAN.md` defines how implementation success will be measured
