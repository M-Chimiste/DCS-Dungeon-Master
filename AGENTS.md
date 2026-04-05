# AGENTS.md

## Project

DCS Dungeon Master

## Purpose

This file gives coding agents the minimum context needed to work safely and effectively in this repository.

Read this before making code changes.

## Current Repo State

Milestones 0 through 9 are implemented.

The repo now includes:

- Python backend skeleton and CLI
- Authored Persian Gulf baseline scenario
- SQLite current-state and event persistence
- Olympus and DCS-gRPC integration layers
- Internal world-state and fog-of-war fusion
- Canonical observation building and replay artifacts
- Phase 1 action validation
- OpenAI-compatible model adapter support
- Dry dual-commander decision loop
- Live execution and command loop
- CLI-first operator control, replay bundles, and run inspection
- Evaluation harness, fairness review, and baseline matrix tooling

There is no higher numbered milestone defined yet in `project-docs/MILESTONE-PLAN.md`.

The main source of truth is still the documentation set in `project-docs/`, but agents should assume the codebase is active and should keep implementation and docs aligned.

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

Current milestone status:

- Milestones 0-9: implemented

Do not jump straight to live simulator execution before the existing domain, observation, validation, and dry-loop layers are respected.

## Working Rules For Agents

- Keep docs and implementation aligned. If code forces a design change, update the relevant document.
- Prefer small, reversible steps that preserve replayability and inspectability.
- Do not weaken fog-of-war rules for convenience.
- Do not bypass the bounded action layer by calling Olympus or DCS-gRPC directly from model-facing code.
- Do not introduce multimodal inputs as a required dependency for Phase 1.
- Do not silently expand Phase 1 scope into tactical control or full campaign complexity.

## Expected Code Areas

The codebase currently separates concerns roughly into:

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

## Current Coding Target

If no more specific task is given, the default focus should be post-Milestone-9 hardening and tuning work around the implemented system:

- keep contracts aligned with the docs
- harden integration resilience and auditability
- preserve coalition-safe observation and validation behavior
- avoid bypassing the dry-loop and validation boundaries
- improve baseline evaluation quality and tuning workflows
