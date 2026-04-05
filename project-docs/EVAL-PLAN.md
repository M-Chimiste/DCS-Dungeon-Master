# Evaluation Plan

## Project

DCS Dungeon Master

## Status

Draft 0.1

## Purpose

This document defines how Phase 1 of DCS Dungeon Master will be evaluated. It specifies what success looks like, which scenarios and run configurations should be tested, what metrics should be captured, how fairness and anti-cheat behavior should be reviewed, and how different models or settings should be compared.

This is the evaluation contract for the planning phase. It defines how the team will decide whether the harness is working well enough to continue, tune, or redesign.

## Scope

This plan applies to:

- The Phase 1 architecture in `project-docs/ARCHITECTURE.md`
- The Phase 1 baseline scenario in `project-docs/SCENARIO-SPEC.md`
- The commander observation contract in `project-docs/OBSERVATION-SPEC.md`
- The bounded command vocabulary in `project-docs/ACTION-SPEC.md`

Phase 1 evaluation focuses on:

- Dual-commander stability
- Strategic usefulness
- Fog-of-war integrity
- Action validity
- Resource discipline
- Replayability across runs and models

## Evaluation Goals

- Verify that both commanders can run repeated decision cycles without collapsing the loop
- Verify that both commanders act only through the bounded action contract
- Verify that neither commander behaves as if it has omniscient state
- Verify that the decisions are strategically meaningful rather than random or churn-heavy
- Verify that the baseline scenario produces observable, comparable outcomes across runs
- Verify that the system is understandable enough for developers to inspect what happened and why

## Phase 1 Success Criteria

Phase 1 should be considered successful only if most evaluation runs demonstrate:

- Both commanders complete repeated decision cycles without unhandled failure
- Accepted actions are overwhelmingly valid under scenario and resource rules
- Rejected actions are understandable and machine-classified
- Neither commander shows strong evidence of hidden-state cheating
- Sector priorities, reserve commitments, and repositioning behavior change in response to evolving conditions
- Runs are inspectable through persisted observations, actions, and execution outcomes

These criteria align with the success criteria in `project-docs/PRD.md`.

## Evaluation Categories

Phase 1 evaluation should be organized into five categories.

### 1. Loop Stability

Questions:

- Does the system keep running over a full scenario duration?
- Do both commanders continue producing usable decisions?
- Does the execution layer keep applying standing orders between cycles?

### 2. Action Quality

Questions:

- Are proposed actions legal, relevant, and scenario-appropriate?
- Does the model use the action set intelligently instead of spamming no-op or conflicting commands?
- Do accepted actions materially affect the battlefield?

### 3. Fog-Of-War Integrity

Questions:

- Does either commander behave as if it knows hidden enemy placements?
- Do stale or uncertain contacts affect decisions in believable ways?
- Are both sides constrained by incomplete information rather than omniscient truth?

### 4. Strategic Behavior

Questions:

- Do commanders change sector emphasis over time?
- Do they reposition or preserve high-value assets under threat?
- Do they commit reserves selectively rather than instantly?
- Does the center remain credibly contested in at least some runs?

### 5. Operational Observability

Questions:

- Can the team reconstruct what each commander saw?
- Can the team explain why an action was accepted or rejected?
- Can the team compare two runs with different models or settings?

## Evaluation Levels

Phase 1 should use three evaluation levels.

### Level 1: Interface Validation

Purpose:

- Verify schema correctness, action validation, rejection handling, and logging without requiring a full long run

Examples:

- Single-cycle dry runs
- Observation rendering checks
- Batched action validation checks

### Level 2: Controlled Scenario Runs

Purpose:

- Run the Phase 1 baseline scenario under a fixed configuration and observe behavior across a full session

Examples:

- RED Gemma vs BLUE Gemma
- RED local model vs BLUE hosted model
- Same models with different decision cadences

### Level 3: Comparative Evaluation

Purpose:

- Compare models, prompts, or harness settings using the same scenario and metadata template

Examples:

- Gemma structured-only vs Gemma structured-plus-visual
- Local runtime vs hosted provider
- 30-second cadence vs 60-second cadence

## Baseline Test Matrix

Phase 1 should start with a small but useful matrix.

### Required Baseline Runs

- Mirror run: same model on both sides
- Mixed-backend run: one local or LAN model versus one hosted provider
- Dry-run validation session with full action logging but no simulator execution
- Repeated run with the same scenario settings to inspect variability

### Recommended Early Comparisons

- Gemma 4 26B-A4B-it on both sides
- Gemma 4 26B-A4B-it with structured-only input
- Gemma 4 26B-A4B-it with structured input plus optional coalition-filtered visual attachment
- One run with faster cadence and one with slower cadence

## Scenario Under Test

The default evaluation scenario is `Phase 1 Baseline: Caucasus Frontier` from `project-docs/SCENARIO-SPEC.md`.

Default parameters:

- Run duration target: 45 minutes
- Decision interval: 30 seconds
- Sides: 1 REDFOR commander and 1 BLUFOR commander
- Sector count: 7
- Central objectives: 2
- Air activity level: low
- Fog-of-war strictness: high

Scenario variants should be introduced only after baseline results are understandable.

## Metrics

Phase 1 needs both quantitative and qualitative metrics.

## Quantitative Metrics

### Loop Health Metrics

- Decision cycles completed per coalition
- Number of unhandled run failures
- Number of model timeouts
- Number of source integration interruptions
- Percentage of cycles where a commander returns at least one usable action or explicit hold decision

### Action Metrics

- Total actions proposed
- Total actions accepted
- Total actions rejected
- Rejection rate by rejection code
- Average accepted actions per cycle
- Average rejected actions per cycle
- Rate of conflicting actions within a cycle
- Rate of repeated duplicate actions across adjacent cycles

### Resource Metrics

- Budget spent per coalition
- Reserve groups committed per coalition
- Time to first reserve commitment
- Rate of immediate reserve dumping
- High-value asset loss count

### Strategic Outcome Metrics

- Sector priority changes over time
- Central objective control time by coalition
- Forward support point survival time
- Rear airbase survival status
- Win, loss, or draw outcome

### Observation Integrity Metrics

- Percentage of enemy-contact decisions based on low-confidence tracks
- Average contact staleness at the time of relevant decisions
- Count of decisions later judged suspicious for hidden-state leakage

## Qualitative Review Metrics

Each full run should receive a lightweight structured review.

Review prompts:

- Did the commanders appear to act on incomplete information?
- Did the center feel contested and alive?
- Were reserve commitments sensible?
- Were repositioning decisions understandable?
- Did either side behave erratically or churn without purpose?
- Could a reviewer explain the major turning points from the logs?

Recommended rating categories:

- `excellent`
- `acceptable`
- `borderline`
- `failed`

Apply these ratings to:

- Fairness
- Strategic coherence
- Resource discipline
- Observability

## Anti-Cheat Review

Fog-of-war integrity is important enough to deserve explicit review.

### What To Look For

- A commander repeatedly reacts to enemy groups that were not observed, inferred, or publicly known
- A commander avoids ambushes or hidden concentrations with implausible consistency
- A commander issues highly precise repositioning or reinforcement decisions before legitimate detection
- A commander behaves as if stale tracks are still exact live positions

### Review Method

For suspicious runs:

- Compare the commander’s observation snapshot against the internal world state
- Check whether the action could be justified by visible or inferred information
- Flag the event as `cleared`, `suspicious`, or `confirmed_leakage`

Phase 1 should bias toward over-investigating suspicious behavior rather than hand-waving it away.

## Run Metadata Requirements

Every evaluation run should persist at minimum:

- Run ID
- Date and time
- Scenario version
- Scenario seed if used
- RED model identifier
- BLUE model identifier
- RED model hosting mode
- BLUE model hosting mode
- Decision cadence
- Prompt or system configuration version
- Observation schema version
- Action schema version
- Resource settings version
- Dry-run or live-run flag
- Outcome summary

## Per-Cycle Artifacts

For each commander cycle, persist:

- Observation snapshot ID
- Observation payload
- Model request metadata
- Raw action proposal
- Validation result
- Execution result
- Standing-order delta
- Resource delta

This is required for replay, debugging, and fairness review.

## Test Procedures

### Procedure 1: Dry-Run Validation

Purpose:

- Test the command loop without touching the simulator

Steps:

1. Load the baseline scenario state
2. Generate commander observations
3. Send observations to each model
4. Validate returned actions
5. Record results without executing simulator-side commands

Pass signals:

- No schema failures in the harness
- Rejections are machine-readable and understandable
- Proposed actions are mostly in-scope

### Procedure 2: Full Baseline Run

Purpose:

- Evaluate a normal Phase 1 live run

Steps:

1. Start the baseline scenario
2. Run both commanders on the same snapshot cadence
3. Persist all observations, actions, and outcomes
4. End at scenario completion or failure condition
5. Produce a run summary

Pass signals:

- Run completes without catastrophic loop failure
- Both commanders participate throughout the run
- Strategic decisions affect the battlefield in visible ways

### Procedure 3: Comparative Run Pair

Purpose:

- Compare models or settings under the same scenario

Steps:

1. Hold scenario and resource settings constant
2. Change exactly one major variable
3. Run enough sessions to see whether differences are meaningful
4. Compare outcome and behavior metrics

Examples of one-variable changes:

- Model backend
- Prompt version
- Decision cadence
- Visual attachment on or off

## Reporting Format

Each evaluated run should produce a short summary report with:

- Run configuration
- Outcome
- Key quantitative metrics
- Top rejection codes
- Major turning points
- Fairness notes
- Reviewer judgment

Comparative reports should include:

- What changed between runs
- Which metrics moved
- Whether the change appears beneficial, neutral, or harmful

## Pass / Fail Guidance

Phase 1 should be treated as failing if one or more of these become common:

- Frequent unhandled loop failures
- Very high action rejection rates caused by model misuse of the schema
- Strong evidence of hidden-state cheating
- Immediate reserve dumping in most runs
- Little or no sector reprioritization over time
- Runs that cannot be explained from persisted observations and actions

Phase 1 should be treated as promising if most runs show:

- Stable loop behavior
- Understandable action proposals
- Selective reserve use
- Responsive repositioning
- Credible contested-center dynamics

## Recommended Initial Thresholds

These are working thresholds for early iteration, not final product gates.

- Unhandled fatal run failures in fewer than 10% of baseline runs
- Accepted-or-deliberate-hold outcome on most decision cycles
- Rejection rate low enough that the model is usually operating inside the action contract
- No confirmed hidden-state leakage in baseline reviewed runs
- Meaningful sector priority changes in most full runs

These thresholds should be refined once the first implementation exists.

## Comparison Policy

When comparing two models or configurations:

- Keep the scenario version fixed
- Keep resource rules fixed
- Keep prompt framing fixed unless prompt framing is the variable under test
- Change only one major variable at a time when possible
- Prefer several short, inspectable comparisons over one giant benchmark sweep early on

## Open Questions

- How many full runs are enough before declaring a model/configuration better?
- Should human review be mandatory for all baseline runs or only suspicious ones?
- Should we define a formal fairness score in Phase 1 or rely on structured reviewer judgment?
- How much randomness should the scenario include before comparisons become noisy?

## Relationship To Other Documents

- `project-docs/PRD.md` defines the product goals and success criteria
- `project-docs/OBSERVATION-SPEC.md` defines what the commanders see
- `project-docs/ACTION-SPEC.md` defines what the commanders may do
- `project-docs/SCENARIO-SPEC.md` defines the battlefield being evaluated
- `project-docs/ARCHITECTURE.md` defines the system that produces these runs
