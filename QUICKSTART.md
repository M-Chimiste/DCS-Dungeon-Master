# Quick Start

This is the shortest path to verify the repo works locally.

Current repo test status: `88 passed`

## 1. Install

```bash
uv sync
uv run pytest
```

## 2. Use The Default Config

Start with:

```bash
config/milestone0.toml
```

If you want a local copy:

```bash
cp config/milestone0.toml config/local.toml
```

Or let the setup wizard generate one:

```bash
uv run dcs-dungeon-master setup-write-config --config config/milestone0.toml --output config/local.toml
```

The default config now uses a model catalog:

- shared primary preset: `local_gemma`
- shared fallback preset: `hosted_gpt5`

The normal workflow is one shared model for both REDFOR and BLUFOR. You can split them later in the Ops UI if you want different models or servers per side.

The default scenario is `phase1_baseline_persian_gulf`, but the repo also includes authored baseline scenarios for Syria, Afghanistan, Iraq, and Fulda Gap.

## 2.5. What You Need On The DCS Side

For live play, you need:

- a DCS mission running on the same map/theater as your selected scenario
- Olympus reachable from this app
- DCS-gRPC reachable from this app
- the required `autoexec.cfg` permissions for Olympus

The important design rule is:

- DCS is the live world/integration surface
- the scenario package is the authoritative source for commander force availability and campaign rules

That means the commander does not learn “what units may be used” just from whatever happens to exist in the mission editor. The allowed force catalog comes from the scenario package:

- `active_groups` are the starting controlled units
- `reserve_groups` are the deployable reserves
- `allowed_sector_ids` constrain where reserves may be committed
- `deployment_restrictions` and coalition budget further limit legal actions

So if you want to change what REDFOR or BLUFOR can place and use, edit the scenario package or use the Studio draft editor, then initialize or create the run from that scenario.

## 3. Smoke Test Without Live Integrations

Optional but recommended first:

```bash
uv run dcs-dungeon-master setup-check --config config/milestone0.toml
```

Validate the scenario:

```bash
uv run dcs-dungeon-master check-scenario --config config/milestone0.toml
```

Initialize a run:

```bash
uv run dcs-dungeon-master init-state --config config/milestone0.toml
```

Inspect the latest state:

```bash
uv run dcs-dungeon-master state-summary --config config/milestone0.toml
uv run dcs-dungeon-master world-state-summary --config config/milestone0.toml
```

Build observations:

```bash
uv run dcs-dungeon-master build-observation-pair \
  --config config/milestone0.toml \
  --decision-cycle 1
```

Validate one safe action:

```bash
uv run dcs-dungeon-master validate-actions \
  --config config/milestone0.toml \
  --coalition red \
  --decision-cycle 1 \
  --input '[{"action_id":"hold_1","action_type":"hold_action","reason":"Maintain current posture","scope":"global"}]'
```

Audit the current milestone surfaces:

```bash
uv run dcs-dungeon-master audit-milestones --config config/milestone0.toml
```

Optional extra check:

```bash
uv run dcs-dungeon-master check-model-backends --config config/milestone0.toml
```

## 4. If Your Model Backend Is Reachable

Check model backends:

```bash
uv run dcs-dungeon-master check-model-backends --config config/milestone0.toml
```

That output now includes:

- backend health
- backend capabilities
- configured model catalog entries
- effective REDFOR/BLUFOR routing

Run one dry decision cycle:

```bash
uv run dcs-dungeon-master run-decision-cycle \
  --config config/milestone0.toml \
  --decision-cycle 1
```

Inspect what happened:

```bash
uv run dcs-dungeon-master model-response-summary --config config/milestone0.toml --coalition red
uv run dcs-dungeon-master run-summary --config config/milestone0.toml
uv run dcs-dungeon-master run-timeline --config config/milestone0.toml
```

## 5. Export A Replay Bundle

```bash
uv run dcs-dungeon-master replay-export \
  --config config/milestone0.toml \
  --output /tmp/dcs-dm-bundle
```

## 6. Run Evaluation On The Latest Run

```bash
uv run dcs-dungeon-master eval-run --config config/milestone0.toml
uv run dcs-dungeon-master eval-summary --config config/milestone0.toml
uv run dcs-dungeon-master eval-closeout-status --config config/milestone0.toml
```

## 7. Live Testing

Only use these when Olympus and DCS-gRPC are actually reachable and you intend to send commands:

```bash
uv run dcs-dungeon-master check-integration --config config/milestone0.toml
uv run dcs-dungeon-master run-live-cycle --config config/milestone0.toml --decision-cycle 1
```

## 8. Web UI

Start the backend API:

```bash
uv run dcs-dungeon-master serve-web-ui --config config/milestone0.toml
```

Then start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open:

- [http://127.0.0.1:5173/setup](http://127.0.0.1:5173/setup) for Setup
- [http://127.0.0.1:5173/studio](http://127.0.0.1:5173/studio) for Campaign Studio
- [http://127.0.0.1:5173/ops](http://127.0.0.1:5173/ops) for Live Ops

Recommended Milestone 10.1 demo sequence:

1. Open `Setup`
2. Probe your environment and write `config/local.toml` if needed
3. Open `Studio`
4. Choose either `From Template` or `Blank Scenario`
5. If using blank creation, set theater, scenario id, name, and summary, then create the draft
6. Use the authoring toolbar to add sectors, control points, zones, active groups, reserve groups, restrictions, and standing orders
7. Click the map directly to place new sectors or zones when add mode is active
8. Edit geometry, neighbors, tags, force placement, reserve allowed sectors, and restrictions in the inspectors
9. Wait for the auto-save badge to settle on `Saved`
10. Run `Validate` and click any returned issue to jump back to the affected object
11. Use `Save To Repo` to write a real scenario package, or `Export TOML` to download the current draft without touching the registry
12. Open `Ops`
13. In `Continue Existing Run`, open or continue a persisted run if you already have one
14. Otherwise, in `Run Setup`, keep `Use same model for both sides` on and pick a shared catalog preset
15. Create a dry run
16. Toggle layers and use `Refresh Now` after a dry or live cycle

If you want asymmetric routing:

1. Turn off `Use same model for both sides`
2. Pick different REDFOR and BLUFOR catalog entries
3. Optionally open `Advanced Ad-hoc` and enter a one-off model/URL override for a side
4. Create the run and inspect the resolved routing from the run card

If you only want to smoke-test the API, these should respond with JSON:

```bash
curl http://127.0.0.1:8080/api/scenarios
curl http://127.0.0.1:8080/api/runs
curl http://127.0.0.1:8080/api/scenarios/drafts
curl http://127.0.0.1:8080/api/model-catalog
curl http://127.0.0.1:8080/api/setup/status
```

To export a Studio draft as TOML:

```bash
curl http://127.0.0.1:8080/api/scenarios/drafts/YOUR_DRAFT_ID/export.toml
```

Recommended shortest UI path:

1. Start `serve-web-ui`
2. Start Vite
3. Open `/setup` and run the environment probe
4. Open `/ops`
5. Either continue an existing run or keep `Use same model for both sides` enabled for a new one
6. Pick a shared catalog preset
7. Create a dry run
8. Use `Refresh Now` and inspect REDFOR / BLUFOR commander previews

## 9. Continue An Existing Run

List persisted and resumable runs:

```bash
uv run dcs-dungeon-master run-list --config config/milestone0.toml
```

Open the latest run:

```bash
uv run dcs-dungeon-master run-open-latest --config config/milestone0.toml
```

Continue a paused or created run:

```bash
uv run dcs-dungeon-master run-continue \
  --config config/milestone0.toml \
  --run-id YOUR_RUN_ID
```

This works for harness-managed runs already stored in SQLite. It does not yet attach to an unrelated live mission.

## Notes

- Default SQLite state lives at `.cache/dcs-dungeon-master/state.sqlite3`
- Default shared primary catalog preset is `local_gemma`
- Default shared fallback catalog preset is `hosted_gpt5`
- Multimodal support exists, but it is disabled globally by default in the shipped config
- The Python web server will serve a simple fallback page if `frontend/dist` does not exist; use Vite dev mode for the actual React UI during development
- Milestone 9 tooling is implemented, but true sign-off still requires a real baseline matrix run against live endpoints
- Milestone 10 / 10.1 UI and model-catalog routing work are implemented in code and available locally
