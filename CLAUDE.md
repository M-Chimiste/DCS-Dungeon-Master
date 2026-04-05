# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

DCS Dungeon Master is an LLM-driven strategic commander harness for DCS World. It enables LLMs to act as REDFOR and BLUFOR coalition commanders, observing the battlefield through honest fog-of-war and issuing bounded strategic actions (deploy, reposition, retask ground assets). The harness sits between DCS World (via DCS Olympus and DCS-gRPC) and interchangeable LLM backends (local, LAN, or hosted).

## Current State

Pre-implementation. The repo contains planning docs only. All design authority lives in `project-docs/`. Read `AGENTS.md` and the docs listed there before writing any code.

Implementation direction is now locked for Phase 1:

- Backend and harness code: Python
- Any future UI work: React + TypeScript

## Doc Reading Order

1. `project-docs/PRD.md` — product scope and goals
2. `project-docs/OBSERVATION-SPEC.md` — commander input contract
3. `project-docs/SCENARIO-SPEC.md` — first scenario definition
4. `project-docs/ACTION-SPEC.md` — bounded command vocabulary
5. `project-docs/ARCHITECTURE.md` — system components and data flow
6. `project-docs/EVAL-PLAN.md` — evaluation metrics and test plan
7. `project-docs/MILESTONE-PLAN.md` — implementation sequence

Reference only (do not load wholesale): `project-docs/Premise-1.md`, `project-docs/Premise-2.md`, `project-docs/pax1601-dcsolympus-8a5edab282632443.txt`.

## Architecture (Big Picture)

The system has three critical trust boundaries:

1. **Raw sim data -> Internal state**: harness ingests rich data from Olympus/gRPC but it is not automatically safe for commanders
2. **Internal state -> Commander observation**: anti-cheat boundary — only coalition-filtered, uncertainty-marked knowledge is exported
3. **Commander intent -> Simulator commands**: bounded-agency boundary — every action is validated before execution

Data flow: DCS World -> Olympus/gRPC clients -> Internal world state -> Sensor fusion (per-coalition fog-of-war) -> Observation builder (per-coalition) -> LLM -> Action validation -> Execution layer -> back to Olympus/gRPC.

Both commanders operate on a shared simulation world but see independent filtered views. Cross-coalition interaction occurs only through legitimate simulation consequences.

## Expected Module Boundaries

When implementation starts, separate concerns into roughly these areas:
`integration`, `world_state`, `sensor_fusion`, `scenario_state`, `observation`, `model_adapter`, `action_validation`, `execution`, `persistence`, `operator_control`.

## Implementation Sequence

Follow milestones 0-9 in `project-docs/MILESTONE-PLAN.md`. Do not jump to model integration or live execution before domain contracts, observation, and validation layers exist. The first proof point is the dry dual-commander loop (Milestone 6).

## Key Constraints

- Structured observations are the canonical model input; multimodal is optional/supplementary only
- The model never talks directly to Olympus or DCS-gRPC APIs
- The harness validates every action before execution
- Backend implementation should remain Python-only; do not introduce a frontend workspace until UI work is intentionally started
- Commander-visible state must be coalition-filtered and non-omniscient
- The model cannot spawn unlimited forces; resource governance lives outside the model
- Must support DCS on a separate machine from the harness
- Must support local, LAN, and hosted LLM backends through one adapter interface
- If code forces a design change, update the relevant doc in `project-docs/`

## Tradeoff Preferences

Fairness > convenience. Bounded behavior > raw capability. Structured state > opaque media. Inspectability > cleverness. Milestone alignment > speculative expansion.
