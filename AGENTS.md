# AGENTS.md

## Project

DCS Dungeon Master

## Purpose

This file gives coding agents the minimum context needed to work safely and effectively in this repository.

Read this before making code changes.

## Current Repo State

This repo is still in pre-implementation planning.

At the moment, the main source of truth is the documentation set in `project-docs/`. Coding work should follow those docs rather than inventing a different Phase 1 shape during implementation.

## Source Of Truth Docs

Read these first, in roughly this order:

1. `project-docs/PRD.md`
2. `project-docs/OBSERVATION-SPEC.md`
3. `project-docs/SCENARIO-SPEC.md`
4. `project-docs/ACTION-SPEC.md`
5. `project-docs/ARCHITECTURE.md`
6. `project-docs/EVAL-PLAN.md`
7. `project-docs/MILESTONE-PLAN.md`

Reference material:

- `project-docs/Premise-1.md`
- `project-docs/Premise-2.md`
- `project-docs/pax1601-dcsolympus-8a5edab282632443.txt`

Do not treat the Olympus dump as something to load wholesale into context. It is a reference source to consult selectively.

## Phase 1 Summary

Phase 1 is:

- One REDFOR commander
- One BLUFOR commander
- Strategic ground-command focused
- Sector-based
- Structured observation first
- Bounded action vocabulary
- Honest fog-of-war enforced by the harness

Phase 1 is not:

- Full-theater package warfare
- Tactical aircraft micromanagement
- Unlimited spawning through Olympus
- Vision-only or video-only control

## Core Architecture Rules

- Structured observations are the canonical model input.
- Multimodal inputs are optional and experimental only after the structured path works.
- The model never talks directly to raw simulator APIs.
- The harness validates every action before execution.
- The harness may know more internally than either commander sees.
- Commander-visible state must remain coalition-filtered and non-omniscient.

## Implementation Priority

Follow the milestone sequence in `project-docs/MILESTONE-PLAN.md`.

The intended order is:

1. Project skeleton and domain contracts
2. Scenario state and persistence
3. Simulation integration
4. Sensor fusion and fog-of-war
5. Observation pipeline
6. Action validation
7. Model adapter and dry loop
8. Live execution loop
9. Operator/replay tooling
10. Evaluation harness and tuning

Do not jump straight to model integration or live simulator execution before the domain, observation, and validation layers exist.

## Working Rules For Agents

- Keep docs and implementation aligned. If code forces a design change, update the relevant document.
- Prefer small, reversible steps that preserve replayability and inspectability.
- Do not weaken fog-of-war rules for convenience.
- Do not bypass the bounded action layer by calling Olympus or DCS-gRPC directly from model-facing code.
- Do not introduce multimodal inputs as a required dependency for Phase 1.
- Do not silently expand Phase 1 scope into tactical control or full campaign complexity.

## Expected Code Areas

When implementation starts, the codebase should likely separate concerns roughly into:

- `integration`
- `world_state`
- `sensor_fusion`
- `scenario_state`
- `observation`
- `model_adapter`
- `action_validation`
- `execution`
- `persistence`
- `operator_control`

These names are not mandatory, but the separation of concerns is.

## Evaluation Expectations

Code should make it easy to inspect:

- what each commander saw
- what each commander proposed
- what was accepted or rejected
- what was executed
- what changed in resources and standing orders

If a change makes those harder to recover, it is probably moving in the wrong direction.

## Remote Deployment Expectation

The design must support:

- DCS World on the same machine as the harness
- DCS World on a separate machine
- Local, LAN-hosted, and cloud-hosted model backends

Do not hardcode assumptions that DCS, the harness, and the model all run on one host.

## If You Need To Make A Tradeoff

Prefer:

- fairness over convenience
- bounded behavior over raw capability
- structured state over opaque media
- inspectability over cleverness
- milestone alignment over speculative expansion

## First Coding Target

If no more specific task is given, start with Milestone 0 from `project-docs/MILESTONE-PLAN.md`:

- project skeleton
- core domain objects
- baseline configuration layout
- dry-run friendly app startup
