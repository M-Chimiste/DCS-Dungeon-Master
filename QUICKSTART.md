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

## Notes

- Default SQLite state lives at `.cache/dcs-dungeon-master/state.sqlite3`
- Default primary model backend is `local_default`
- Default fallback backend is `hosted_default`
- Multimodal is disabled globally by default
- Milestone 9 tooling is implemented, but true sign-off still requires a real baseline matrix run against live endpoints
