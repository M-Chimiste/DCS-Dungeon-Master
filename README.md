# DCS Dungeon Master

Python backend for a two-sided DCS command harness.

The system sits between DCS/Olympus/DCS-gRPC and one or more LLM backends. It builds coalition-safe observations, validates bounded strategic actions, optionally executes accepted actions, and persists enough state for replay, inspection, and evaluation.

## Current Status

Milestones 0-8 are implemented, and Milestone 10 now adds a first web UI/API slice for Campaign Studio and Live Ops.

Milestone 9 evaluation tooling is implemented, but the real baseline matrix still needs to be run against live-capable simulator/model endpoints before it should be considered operationally signed off.

What already works in-repo:

- authored Persian Gulf scenario and SQLite-backed scenario state
- Olympus and DCS-gRPC integration layers
- internal world state, evidence ledger, and fog-of-war fusion
- canonical observation building with optional image attachments
- Phase 1 action validation
- OpenAI-compatible model adapter with model catalog, shared-default routing, and per-side fallback
- dry and live command loops
- operator controls, replay export, and run inspection
- evaluation summaries, fairness review, and matrix tooling
- web UI API plus a React/Vite Studio and Ops shell

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)

Optional for real integration and live testing:

- DCS World
- DCS Olympus
- DCS-gRPC
- at least one reachable OpenAI-compatible model backend such as LM Studio

## Install

```bash
uv sync
```

Run tests:

```bash
uv run pytest
```

## Default Config

The default config is [config/milestone0.toml](/Users/c/software_projects/DCS-Dungeon-Master/config/milestone0.toml).

Important defaults:

- scenario: `phase1_baseline_persian_gulf`
- SQLite DB: `.cache/dcs-dungeon-master/state.sqlite3`
- shared primary catalog entry: `local_gemma`
- shared fallback catalog entry: `hosted_gpt5`
- multimodal attachments: disabled globally by default
- runtime mode: `dry`

If you want to test quickly, it is usually easiest to copy that file and make a local variant:

```bash
cp config/milestone0.toml config/local.toml
```

Then edit:

- model catalog entries
- model endpoints
- enabled backends
- `OPENAI_API_KEY` usage if you keep `hosted_default`
- `runtime.dry_run`
- `multimodal.enabled`

## Fastest Smoke Test

1. Validate the scenario:

```bash
uv run dcs-dungeon-master check-scenario --config config/milestone0.toml
```

2. Initialize state:

```bash
uv run dcs-dungeon-master init-state --config config/milestone0.toml
```

3. Inspect current state:

```bash
uv run dcs-dungeon-master state-summary --config config/milestone0.toml
```

4. Audit implemented milestones against the latest run:

```bash
uv run dcs-dungeon-master audit-milestones --config config/milestone0.toml
```

That path does not require a live simulator or model backend.

## Common Local Workflows

### 1. Dry backend boot

```bash
uv run dcs-dungeon-master run-dry --config config/milestone0.toml
```

### 2. Integration health

Use this when Olympus and DCS-gRPC should be reachable:

```bash
uv run dcs-dungeon-master check-integration --config config/milestone0.toml
```

Run one normalized ingest step:

```bash
uv run dcs-dungeon-master ingest-step --config config/milestone0.toml
```

### 3. Model backend health

```bash
uv run dcs-dungeon-master check-model-backends --config config/milestone0.toml
```

This reports:

- backend health
- backend capabilities
- configured catalog entries
- effective RED and BLUE primary/fallback routing

### 3.5. Web UI

Run the Python API server:

```bash
uv run dcs-dungeon-master serve-web-ui \
  --config config/milestone0.toml \
  --host 127.0.0.1 \
  --port 8080
```

Then, in a second terminal, run the frontend:

```bash
cd frontend
npm install
npm run dev
```

The Vite app defaults to [http://127.0.0.1:5173](http://127.0.0.1:5173) and proxies `/api` to the Python server on port `8080`.

Current Milestone 10.1 QoL improvements:

- Campaign Studio drafts auto-save after a short delay
- draft lifecycle actions: rename, duplicate, revert, delete
- click-to-select sector and control-point editing on the map
- structured editing for zones, force placement, reserve allowed sectors, and restrictions
- validation issues link back to the affected authored object
- Live Ops layer toggles, manual refresh, and visibility-aware polling
- catalog-driven run creation with one shared model for both sides by default
- optional split REDFOR/BLUFOR routing and advanced run-scoped ad-hoc model overrides

Useful direct API checks:

```bash
curl http://127.0.0.1:8080/api/scenarios
curl http://127.0.0.1:8080/api/runs
curl http://127.0.0.1:8080/api/scenarios/drafts
curl http://127.0.0.1:8080/api/model-catalog
```

Live Ops run setup now supports:

- scenario + dry/live selection
- a shared catalog preset for both sides by default
- optional side-specific primary/fallback overrides
- optional advanced ad-hoc model/URL overrides for one run

### 4. Observation and fusion inspection

Build one observation:

```bash
uv run dcs-dungeon-master build-observation \
  --config config/milestone0.toml \
  --coalition red \
  --decision-cycle 1
```

Build both coalition observations from one snapshot boundary:

```bash
uv run dcs-dungeon-master build-observation-pair \
  --config config/milestone0.toml \
  --decision-cycle 1
```

Inspect current world/fusion state:

```bash
uv run dcs-dungeon-master world-state-summary --config config/milestone0.toml
uv run dcs-dungeon-master fusion-summary --config config/milestone0.toml --coalition red
uv run dcs-dungeon-master compare-truth-vs-knowledge --config config/milestone0.toml --coalition red
```

### 5. Action validation

Validate inline JSON:

```bash
uv run dcs-dungeon-master validate-actions \
  --config config/milestone0.toml \
  --coalition red \
  --decision-cycle 1 \
  --input '[{"action_id":"hold_1","action_type":"hold_action","reason":"Maintain current posture","scope":"global"}]'
```

View the latest persisted validation summary:

```bash
uv run dcs-dungeon-master action-validation-summary \
  --config config/milestone0.toml \
  --coalition red
```

### 6. Dry decision loop

Run one dry cycle:

```bash
uv run dcs-dungeon-master run-decision-cycle \
  --config config/milestone0.toml \
  --decision-cycle 1
```

Run several dry cycles:

```bash
uv run dcs-dungeon-master run-decision-loop \
  --config config/milestone0.toml \
  --start-decision-cycle 1 \
  --cycles 3
```

Inspect the latest model response:

```bash
uv run dcs-dungeon-master model-response-summary \
  --config config/milestone0.toml \
  --coalition red
```

### 7. Live loop

Only do this when Olympus is reachable and you intend to send simulator-side commands.

Run one live cycle:

```bash
uv run dcs-dungeon-master run-live-cycle \
  --config config/milestone0.toml \
  --decision-cycle 1
```

Inspect execution results:

```bash
uv run dcs-dungeon-master execution-summary --config config/milestone0.toml --coalition red
uv run dcs-dungeon-master standing-orders-summary --config config/milestone0.toml --coalition red
uv run dcs-dungeon-master execution-records --config config/milestone0.toml
```

## Run Control And Replay

Run lifecycle:

```bash
uv run dcs-dungeon-master run-status --config config/milestone0.toml
uv run dcs-dungeon-master run-start --config config/milestone0.toml
uv run dcs-dungeon-master run-pause --config config/milestone0.toml
uv run dcs-dungeon-master run-resume --config config/milestone0.toml
uv run dcs-dungeon-master run-stop --config config/milestone0.toml --reason "done"
```

Run inspection:

```bash
uv run dcs-dungeon-master run-summary --config config/milestone0.toml
uv run dcs-dungeon-master run-timeline --config config/milestone0.toml
uv run dcs-dungeon-master run-inspect --config config/milestone0.toml --coalition red
```

Replay export:

```bash
uv run dcs-dungeon-master replay-export \
  --config config/milestone0.toml \
  --output /tmp/dcs-dm-bundle
```

The output directory must not already contain files.

## Evaluation

Evaluate one run:

```bash
uv run dcs-dungeon-master eval-run --config config/milestone0.toml
uv run dcs-dungeon-master eval-summary --config config/milestone0.toml
```

Review suspicious findings:

```bash
uv run dcs-dungeon-master eval-review-queue --config config/milestone0.toml
uv run dcs-dungeon-master eval-review --config config/milestone0.toml --finding-id 1 --status cleared --note "Reviewed locally"
```

Run the configured evaluation matrix:

```bash
uv run dcs-dungeon-master eval-run-matrix --config config/milestone0.toml --profile phase1_baseline
uv run dcs-dungeon-master eval-matrix-report --config config/milestone0.toml --profile phase1_baseline
uv run dcs-dungeon-master eval-closeout-status --config config/milestone0.toml --profile phase1_baseline
```

Important caveat:

- the matrix runner is implemented
- true Milestone 9 sign-off still depends on running it against real reachable endpoints and then updating [project-docs/eval/BASELINE-REPORT.md](/Users/c/software_projects/DCS-Dungeon-Master/project-docs/eval/BASELINE-REPORT.md)

## Multimodal Testing

Multimodal is optional and image-only.

To test it:

1. set `[multimodal].enabled = true`
2. keep a backend enabled with `multimodal = true`
3. build observations or run dry/live cycles

Generated artifacts will be written under the configured `output_dir`, and if the selected backend supports multimodal submission they will be sent alongside the structured observation payload.

## Recommended First Manual Test Sequence

If you want the least risky first pass:

1. `uv sync`
2. `uv run pytest`
3. `uv run dcs-dungeon-master check-scenario --config config/milestone0.toml`
4. `uv run dcs-dungeon-master init-state --config config/milestone0.toml`
5. `uv run dcs-dungeon-master build-observation-pair --config config/milestone0.toml --decision-cycle 1`
6. `uv run dcs-dungeon-master validate-actions --config config/milestone0.toml --coalition red --decision-cycle 1 --input '[{"action_id":"hold_1","action_type":"hold_action","reason":"Maintain current posture","scope":"global"}]'`
7. If your model backend is reachable: `uv run dcs-dungeon-master run-decision-cycle --config config/milestone0.toml --decision-cycle 1`
8. `uv run dcs-dungeon-master replay-export --config config/milestone0.toml --output /tmp/dcs-dm-bundle`
9. `uv run dcs-dungeon-master eval-run --config config/milestone0.toml`

## More Detail

See:

- [QUICKSTART.md](/Users/c/software_projects/DCS-Dungeon-Master/QUICKSTART.md)
- [ARCHITECTURE.md](/Users/c/software_projects/DCS-Dungeon-Master/project-docs/ARCHITECTURE.md)
- [MILESTONE-PLAN.md](/Users/c/software_projects/DCS-Dungeon-Master/project-docs/MILESTONE-PLAN.md)
- [EVAL-PLAN.md](/Users/c/software_projects/DCS-Dungeon-Master/project-docs/EVAL-PLAN.md)
