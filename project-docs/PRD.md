# Product Requirements Document

## Project

DCS Dungeon Master

## Status

Draft 0.1

This document defines the product direction for an LLM-driven strategic commander harness for DCS World. It is intentionally product-focused. It describes what we are building, why it matters, what is in scope for the first version, and what decisions still need follow-up documents before implementation begins.

## Product Summary

Build a harness that allows LLMs to act as strategic-to-operational coalition commanders in DCS World using DCS Olympus for map/control actions and, where needed, DCS-gRPC for perception and event/state ingestion. The harness must work with three model-hosting modes:

- Local same-machine model runtimes such as LM Studio, Ollama, and vLLM
- Local networked model runtimes such as LM Studio, Ollama, and vLLM on another machine in the LAN
- Hosted providers such as OpenAI, Anthropic, and Gemini

The first product goal is not human-like tactical micromanagement. The first goal is a reliable strategic commander loop for both REDFOR and BLUFOR so the battlefield feels alive, with each side observing the battlespace through honest fog-of-war constraints, reasoning about resource allocation and sector priorities, and deploying or repositioning theater assets such as SAM systems, missile launchers, and ground vehicles.

## Problem Statement

DCS World has powerful simulation fidelity, and Olympus already exposes useful control primitives, but there is no practical commander harness that lets an LLM operate as a non-cheating strategic opponent. The missing product is not just "an AI model connected to an API." The missing product is a complete control loop that:

- Forms a constrained battlefield picture from incomplete information
- Converts map, unit, and event data into model-usable context
- Prevents omniscience and other unfair behaviors
- Lets the model issue only valid, bounded commander actions
- Evaluates whether the resulting behavior is believable, useful, and fun

## Product Vision

Create a modular commander harness that can run against DCS World in live scenarios and eventually support dynamic campaign play. The harness should make it possible to swap models and providers without rewriting the rest of the system, enforce game-appropriate constraints, and scale from a narrow strategic prototype into a richer campaign AI over time.

## Users And Stakeholders

- Primary developer-user: the project team building and tuning the harness
- Primary end-user: mission hosts or players who want a live AI opponent or AI coalition commander
- Secondary user: researchers or hobbyists testing local and hosted models against the same harness

## Product Principles

- Fairness over spectacle: the commander should not cheat via omniscient state access
- Strategic clarity over tactical chatter: the first version should make good theater-level choices, not perfect micro-decisions
- Provider neutrality: the harness should support local and hosted LLM APIs through one abstraction layer
- Deterministic guardrails: the LLM proposes intent, while the harness validates legality, availability, and safety
- Progressive complexity: start with a narrow but complete loop, then scale scenario size and command vocabulary

## Goals

- Support interchangeable LLM backends across local, LAN, and hosted providers
- Ingest battlefield state from DCS-adjacent interfaces and convert it into an LLM-ready observation
- Enforce fog-of-war and coalition restrictions so the model only sees what it should reasonably know
- Allow the model to deploy approved assets such as SAMs, missile launchers, and ground vehicles
- Allow the model to reposition or retask those assets over time
- Introduce a strategy-layer resource model that prevents unlimited spawning and supports campaign-style reasoning
- Produce auditable decisions, tool calls, and world-state transitions for debugging and evaluation

## Non-Goals For Phase 1

- Fine-grained tactical aircraft maneuvering or dogfight control
- Voice control, ATC, AWACS, or natural-language radio simulation
- Full dynamic campaign economy with deep logistics realism
- Human-facing polished UI beyond what is required for operator visibility and debugging
- Perfect realism in target identification, sensor fusion, or doctrine modeling

## Scope

### In Scope For The First Build

- A paired commander harness with one strategic commander for REDFOR and one strategic commander for BLUFOR
- Model adapter layer supporting OpenAI-compatible APIs plus provider-specific adapters where required
- Observation builder that summarizes sectors, known contacts, friendly assets, available reserves, and recent changes for each coalition separately
- Local theater basemap, landmark, and terrain context as optional coalition-filtered map support for the UI and multimodal-capable backends
- Action layer that turns approved LLM decisions into Olympus and/or DCS-gRPC operations
- Resource and force-availability layer outside the model
- Ground-based deployment and movement of strategic assets
- Package-first air tasking with terrain-aware route validation and altitude normalization
- Simultaneous decision cycles for both coalition commanders, with controls to stagger or serialize execution if needed for safety
- Logging, replay, and offline evaluation support

### Out Of Scope For The First Build

- Fully autonomous unconstrained air war planning across the full theater
- Detailed logistics and maintenance simulation
- Automated mission generation
- Multi-modal vision of arbitrary screenshots as a required dependency for core operation

## Core Product Decision

The first release should treat map understanding as a product problem, not only a model capability problem.

The harness should not depend on raw screenshots being interpreted directly by the model as the primary observation channel. Instead, the primary observation should be a structured world representation derived from simulation state, map geometry, unit positions, detection confidence, zone ownership, and relevant mission drawings.

Multi-modal inputs may still be valuable, but they should be treated as an optional enhancement area rather than a core dependency. Limited Phase 1 experiments are acceptable, but the system should not require multimodal inputs to function. This is especially true for:

- Interpreting mission drawings or complex map overlays not yet normalized into data
- Assisting with operator review or debugging
- Comparing structured-state decisions versus image-assisted decisions

This means Phase 1 should prefer a "state-to-text and state-to-JSON" observation pipeline rather than a "map screenshot to vision model" pipeline.

## Why This Direction

- It is easier to enforce fog-of-war on structured data than on rendered imagery
- It is easier to validate and replay decisions when the model sees explicit state inputs
- It keeps the provider layer flexible because not every target model is multimodal
- It reduces token waste by sending only salient information instead of whole images
- It keeps the first version aligned with strategic command instead of tactical visual interpretation

## Functional Requirements

### 1. Model Connectivity

- The harness must support models exposed through local HTTP APIs on the same machine
- The harness must support models exposed through local network HTTP APIs
- The harness must support major hosted providers at minimum OpenAI, Anthropic, and Gemini
- The harness must abstract model invocation behind a common interface for chat, tool use, and structured outputs
- The harness must expose per-provider configuration for endpoint, authentication, model name, timeout, and context/token limits

### 2. Observation And Context

- The harness must build a commander-facing battlefield summary on a fixed decision cadence
- The observation must include only coalition-permitted information
- The observation must include friendly unit and asset disposition at a commander-appropriate level of abstraction
- The observation must include known or suspected enemy contacts with uncertainty and staleness
- The observation must include important terrain or control abstractions such as sectors, airbases, fronts, and protected zones
- The observation may include coalition-filtered map imagery, landmarks, and terrain summaries as secondary context
- The observation must include resource availability and current deployment capacity
- The observation must include recent changes since the previous decision cycle
- The observation format must be stable enough for offline evaluation and prompt iteration

### 3. Fog Of War

- The system must prevent the model from seeing omniscient world state during decision-making
- Unknown enemy units must not be represented as confirmed facts
- Stale contacts must degrade over time or be removed according to configured rules
- The system must keep a clear distinction between observed, inferred, and unknown state
- The product must define acceptable approximations for coalition-limited visibility in Phase 1

### 4. Command Vocabulary

- The model must be able to request deployment of approved ground assets
- The model must be able to request repositioning or retasking of existing controlled assets
- The model must be able to set strategic priorities such as sector emphasis or defensive posture
- The model may request package-first air routes and altitudes, but the harness remains responsible for terrain safety normalization and rejection
- The command layer must validate every action against available resources, location rules, and coalition permissions
- Invalid or infeasible actions must be rejected with machine-readable reasons

### 5. Resource Governance

- The system must maintain an external record of deployable assets and their availability
- The model must not be able to spawn unlimited forces directly
- The system must support configurable force pools, reserve commitments, and attrition
- The system should support scenario-defined budgets or deployment allowances even if the underlying simulator is more permissive

### 6. Safety And Control

- The operator must be able to start, stop, and pause the commander loop
- The system must support dry-run mode where actions are proposed but not executed
- The system must log prompts, observations, actions, validation results, and execution outcomes
- The system must support bounded execution so the model cannot call arbitrary simulator operations

### 7. Evaluation

- The system must support replayable scenario tests
- The system must support measuring latency, action validity, command usefulness, and resource discipline
- The system must support qualitative review of whether the commander behaves credibly and non-cheating

## User Stories

- As a developer, I want to point the harness at a local or hosted LLM endpoint so I can compare models without changing the rest of the system
- As a mission host, I want the AI commander to behave within fog-of-war so the fight feels fair
- As an operator, I want to see why the commander made a deployment or repositioning decision
- As a tester, I want to replay the same scenario with different models and compare results
- As a designer, I want to constrain force pools and allowed actions so the AI behaves within scenario rules

## Phase Plan

### Phase 0: Planning And Interfaces

- Finalize PRD
- Write architecture overview
- Define provider adapter interface
- Define observation schema
- Define action schema
- Define evaluation scenarios

### Phase 1: Strategic Ground Commander Prototype

- Two-coalition commander loop with one REDFOR commander and one BLUFOR commander
- Honest fog-of-war approximation
- Sector-based observation summaries
- Controlled spawning of SAMs, missile launchers, and ground vehicles
- Repositioning and simple retasking of those assets
- Action validation and logging
- Replayable evaluation scenarios

### Phase 2: Expanded Operational Layer

- More mature reserve logic and attrition
- Better contact fusion and confidence tracking
- Larger scenarios and longer-running sessions
- Optional use of multimodal context where it clearly improves performance

### Phase 3: Campaign Expansion

- Persistent campaign state across sessions
- Reinforcement pipelines
- More advanced package planning
- Additional coalition roles or multi-agent coordination

## Initial Success Criteria

- The harness can connect to at least one local runtime and one hosted provider through the same high-level interface
- Both commanders can complete repeated decision cycles without crashing or issuing unbounded actions
- Both commanders can deploy and reposition approved ground assets in a valid way
- Neither commander uses clearly omniscient information in evaluation scenarios
- The team can inspect a run and understand what the model saw, decided, and executed

## Open Product Questions

- What is the exact Phase 1 definition of "strategic" versus "tactical" for ground assets?
- Should sector control be modeled as explicit zones, graph nodes, named regions, or map overlays?
- Which Olympus capabilities should be treated as core supported actions versus deferred actions?
- How much of the fog-of-war model should come from Olympus alone versus DCS-gRPC augmentation?
- What is the minimum useful scenario size for a believable prototype?
- Do we want an operator approval mode in the first release, or only full-auto and dry-run?
- When we later add multimodal support, what problem must it solve that structured state cannot?

## Risks

- The observation format may become too verbose or too lossy for good decisions
- The model may overfit to schema compliance while still making poor strategic choices
- Olympus and DCS-gRPC may overlap or disagree on certain world-state details
- Fog-of-war enforcement may be weaker than expected if not carefully defined
- Ground deployment authority may need additional scenario-specific safeguards to avoid degenerate play
- Hosted provider latency or cost may slow iteration compared with local models

## Required Follow-Up Documents

This PRD should be followed by a small set of focused documents before coding begins:

- `project-docs/ARCHITECTURE.md`: system components, data flow, and service boundaries
- `project-docs/OBSERVATION-SPEC.md`: exact commander context schema, salience rules, and fog-of-war treatment
- `project-docs/ACTION-SPEC.md`: allowed tool calls, validation rules, and rejection reasons
- `project-docs/EVAL-PLAN.md`: benchmark scenarios, metrics, and test procedure
- `project-docs/SCENARIO-SPEC.md`: first playable scenario assumptions, force pools, sectors, and objectives

## Notes From Current References

- The premise docs strongly support a design where Olympus is the control surface and DCS-gRPC is the richer perception/event layer
- Olympus already appears to support coalition-limited views, direct unit/static spawning, tasking, and access to mission drawings
- The Olympus configuration also suggests straightforward support for local and networked deployment patterns
- The current evidence supports starting with structured map state rather than requiring multimodal map screenshots

## References

- `project-docs/Premise-1.md`
- `project-docs/Premise-2.md`
- `project-docs/pax1601-dcsolympus-8a5edab282632443.txt`
