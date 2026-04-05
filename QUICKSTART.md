# Quick Start

This is the shortest path to verify the repo works locally.

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

## 3. Smoke Test Without Live Integrations

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

## 4. If Your Model Backend Is Reachable

Check model backends:

```bash
uv run dcs-dungeon-master check-model-backends --config config/milestone0.toml
```

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

- [http://127.0.0.1:5173/studio](http://127.0.0.1:5173/studio) for Campaign Studio
- [http://127.0.0.1:5173/ops](http://127.0.0.1:5173/ops) for Live Ops

Recommended Milestone 10.1 demo sequence:

1. Open `Studio`
2. Select a scenario and create a draft
3. Click sectors and control points directly on the map
4. Change a sector radius, move an active group, or toggle reserve allowed sectors
5. Wait for the auto-save badge to settle on `Saved`
6. Run `Validate` and click any returned issue to jump back to the affected object
7. Open `Ops`, toggle layers, and use `Refresh Now` after a dry or live cycle

If you only want to smoke-test the API, these should respond with JSON:

```bash
curl http://127.0.0.1:8080/api/scenarios
curl http://127.0.0.1:8080/api/runs
```

## Notes

- Default SQLite state lives at `.cache/dcs-dungeon-master/state.sqlite3`
- Default primary model backend is `local_default`
- Default fallback backend is `hosted_default`
- Multimodal is disabled globally by default
- The Python web server will serve a simple fallback page if `frontend/dist` does not exist; use Vite dev mode for the actual React UI during development
- Milestone 9 tooling is implemented, but true sign-off still requires a real baseline matrix run against live endpoints
