# Building an LLM REDFOR commander for DCS World

**A local LLM can serve as a viable operational-level REDFOR commander in DCS World today**, using DCS-gRPC for real-time perception and DCS Olympus for spawning and high-level commands. The sweet spot for dual RTX 3090s is Qwen 3.5's **35B-A3B MoE model** (3B active parameters, 112 tok/s at Q4 on a single 3090) or Gemma 4's **26B-A4B** (3.8B active, 119 tok/s at Q4), both of which leave one GPU entirely free for DCS. On a 512GB Mac Studio, Qwen 3.5's **397B-A17B flagship at 4-bit** fits comfortably in 214GB with 300GB of headroom for context and KV cache. The harness architecture follows a two-tier pattern proven in SwarmBrain and TextStarCraft II: the LLM issues operational-level standing orders every 30–60 seconds while a deterministic tactical layer translates those orders into DCS-gRPC `SetTask`/`SetCommand` RPCs between decision cycles.

---

## Qwen 3.5 brings a hybrid MoE architecture purpose-built for agents

Qwen 3.5, released February–March 2026 under Apache 2.0, represents a fundamental architecture shift from prior Qwen generations. It uses a **hybrid Gated DeltaNet + sparse Mixture-of-Experts** design — three layers of linear-complexity Gated DeltaNet attention for every one layer of full quadratic attention — yielding near-linear scaling on long contexts. The model lineup differs entirely from Qwen 2.5: dense models at 0.8B, 2B, 4B, 9B, and 27B, with MoE variants at **35B-A3B** (256 experts, 8 routed + 1 shared per token), **122B-A10B**, and the **397B-A17B flagship** (512 experts, 10 routed + 1 shared). All models natively handle text, images, and video through early-fusion multimodal training. The context window is **262K tokens native**, extensible to ~1M via YaRN RoPE scaling.

For the REDFOR commander use case, Qwen 3.5's agentic capabilities are the key draw. The model was explicitly designed as an "agent platform" and scores **86.7% on TAU2-Bench** (agent tasks) and **72.9% on BFCL-V4** (function calling). Tool calling uses `<tool_call>` tags with JSON-formatted arguments, supports parallel function calls, and integrates with vLLM via `--tool-call-parser qwen3_coder` and SGLang via the same parser flag. The native thinking mode (`<think>...</think>` tags) provides chain-of-thought reasoning before tool invocation — directly useful for operational planning where the LLM should reason about threat axes and force ratios before issuing orders.

**Hardware fit on dual 3090s (48GB VRAM total):**

| Model | Quantization | VRAM needed | Fits? | Speed |
|-------|-------------|-------------|-------|-------|
| 35B-A3B | Q4_K_XL | ~20GB | Single GPU | **112 tok/s** |
| 35B-A3B | Q8_0 | ~38GB | Both GPUs | ~60 tok/s est. |
| 27B (dense) | Q6_K | ~24GB | Single GPU | ~30 tok/s est. |
| 122B-A10B | Q3 | ~60GB | Needs CPU offload | Slow |

The **35B-A3B at Q4 is the clear winner** for the 3090 setup: it fits on one GPU, leaving the other entirely for DCS World, and at 112 tok/s generation speed a 500-token operational order takes under 5 seconds. On the **512GB Mac Studio**, the 397B-A17B at 4-bit (214GB) runs via MLX at approximately 35 tok/s with full 256K context, delivering flagship-grade reasoning with massive headroom.

---

## Gemma 4 offers a compelling alternative with native tool calling

Google's Gemma 4, released April 2, 2026 under Apache 2.0, provides four variants: edge models E2B and E4B (with Per-Layer Embeddings), and the larger **26B-A4B** (MoE: 128 experts + 1 shared, top-8 routing, 3.8B active) and **31B** (dense). Both larger models support **256K token context windows**. The architecture uses hybrid sliding-window + global attention, with unified Keys/Values in global layers and Proportional RoPE for long-context optimization.

Gemma 4 introduced **native function calling with dedicated special tokens** (`<|tool_call|>`) — a significant upgrade over Gemma 3, which required prompt engineering. Tools can be defined via JSON schema or raw Python functions (auto-generating schema from type hints). The vLLM parser is `--tool-call-parser gemma4`, and it supports the standard OpenAI `tools` parameter. Benchmark improvements over Gemma 3 are dramatic: **AIME 2026 jumped from 20.8% to 89.2%**, LiveCodeBench v6 from 29.1% to 80.0%, and GPQA Diamond from 42.4% to 84.3%.

**Hardware fit on dual 3090s:**

| Model | Quantization | VRAM needed | Speed (single 3090) |
|-------|-------------|-------------|---------------------|
| 26B-A4B | Q4 | ~18GB | **119 tok/s** generation |
| 31B | Q4 | ~20GB | 34 tok/s generation |
| 31B | Q8 | ~36GB | ~28 tok/s (needs both GPUs) |

The **26B-A4B at Q4 is the standout**: 119 tok/s on a single 3090, with only 18GB of VRAM consumed. It's 2–3x faster than the dense 31B on identical hardware because only 3.8B parameters fire per token. At 256K context, the full model needs only 23GB — still single-GPU territory. On the Mac Studio, every Gemma 4 model runs at **full BF16 precision** with hundreds of gigabytes to spare.

**Choosing between Qwen 3.5 and Gemma 4:** Qwen 3.5's 35B-A3B has a slightly larger expert pool (256 vs 128 experts) and more mature tool-calling infrastructure (Hermes format with extensive vLLM testing). Gemma 4's 26B-A4B has dedicated special tokens for tool calls and slightly faster throughput. Both are excellent choices. For the Mac Studio, Qwen 3.5's 397B-A17B is uniquely compelling — no Gemma 4 model exceeds 31B total parameters, while Qwen offers a 397B flagship that fits in 214GB at 4-bit.

---

## DCS-gRPC exposes 14 services with two critical streaming RPCs

DCS-gRPC (the `DCS-gRPC/rust-server` project) is a Rust DLL that runs inside DCS World, exposing the game's Lua scripting API via gRPC on **port 50051**. Proto files live at `protos/dcs/{service}/v0/{service}.proto` covering 14 services: Mission, Coalition, Group, Unit, Controller, Trigger, World, Hook, Net, Atmosphere, Timer, Metadata, SRS, and Custom.

The two **server-streaming RPCs** form the backbone of real-time perception:

- **`MissionService.StreamUnits`** — continuously pushes unit position/state updates at a configurable `PollRate` (seconds between polls), with optional `GroupCategory` filtering (airplane, helicopter, ground, ship). This is the primary telemetry feed.
- **`MissionService.StreamEvents`** — streams all mission events: Shot, Hit, Explosion, Dead, PilotDead, BaseCaptured, Birth, TakeOff, Landing, MarkAdd, PlayerSendChat, and more. This is the event bus for situational awareness.

For the REDFOR commander, the critical **unary RPCs** are:

- **`ControllerService.GetDetectedTargets`** — returns targets detected by a specific unit/group's sensors. Essential for building a fog-of-war-accurate picture of what RED forces can actually see.
- **`ControllerService.SetTask` / `PushTask` / `SetCommand`** — issues tasks and commands to group controllers. Tasks are persistent (patrol, orbit, engage); commands are one-shot actions.
- **`ControllerService.SetOption`** — sets ROE, reaction-to-threat level, and other behavioral options.
- **`CoalitionService.AddGroup`** — dynamically spawns a group with full waypoint and skill configuration. Critical for reinforcement injection.
- **`CoalitionService.GetGroups`** / **`GroupService.GetUnits`** — queries force composition.
- **`WorldService.GetAirbases`** — retrieves airbase ownership and status.

**Python integration** requires generating stubs from the proto files since no maintained pip package exists:

```bash
pip install grpcio grpcio-tools
git clone https://github.com/DCS-gRPC/rust-server.git
python -m grpc_tools.protoc \
  -I./rust-server/protos \
  --python_out=./generated \
  --pyi_out=./generated \
  --grpc_python_out=./generated \
  ./rust-server/protos/dcs/dcs.proto
```

For cleaner async Python, use `betterproto` instead, which generates dataclass-based stubs with native `asyncio` support. The connection is plaintext (`grpc.insecure_channel('localhost:50051')`) with optional API key authentication via `X-API-Key` metadata header. The server enforces a **throughput limit of 600 calls/sec** in the mission scripting environment — more than sufficient for a 30-second decision cycle but relevant if polling detected targets across many units simultaneously.

---

## DCS Olympus provides REST-based spawning and command on port 4512

DCS Olympus runs a C++ HTTP backend inside DCS World using the Windows HTTP Server API, serving endpoints under the `/olympus/` prefix on **port 4512** (configurable in `olympus.json`). A Node.js/Express frontend on port 3000 proxies all `/olympus/*` requests to the backend, so external clients can use either port. Authentication uses **SHA-256 hashed passwords** per role: Game Master (full control), Blue Commander, Red Commander, and Admin.

The API is not formally documented — it's consumed by the project's own web frontend. Based on the architecture and documented functionality, the key endpoint categories are:

- **State queries**: `GET /olympus/units` (positions, types, health, coalition), `GET /olympus/mission` (time, weather, theatre), `GET /olympus/airfields` (runways, ILS, ownership)
- **Spawning**: `POST /olympus/spawn` with coalition, category (aircraft/helicopter/ground/navy), unit type, location (lat/lng), altitude, heading, skill level, loadout, and role
- **Commands**: `POST /olympus/command` supporting move/waypoint, attack, orbit/racetrack, land, refuel, formation, ROE setting, radar toggle, and delete operations
- **Effects**: Smoke, explosion, flare, illumination at specified coordinates

The Olympus data model represents units with id, name, type, category, coalition, position (lat/lng/alt), heading, speed, fuel (0.0–1.0), health (0.0–1.0), ROE, alarm state, and group membership. For the REDFOR commander, Olympus is best used as the **command and spawn interface** (its REST API is simpler for issuing orders than constructing gRPC task protobufs), while DCS-gRPC provides the **perception layer** (streaming telemetry and detected targets that Olympus doesn't expose as richly).

The recommended integration pattern: DCS-gRPC `StreamUnits` + `StreamEvents` for continuous perception → LLM decision engine → DCS Olympus REST POST for spawning and commanding. Use DCS-gRPC's `ControllerService.GetDetectedTargets` to supplement Olympus with fog-of-war-accurate sensor data.

---

## The harness follows a two-tier architecture with standing orders

The research literature on LLM game agents — particularly SwarmBrain (StarCraft II), TextStarCraft II, and MASMP — converges on a clear pattern for real-time environments: **LLM operates at the macro/operational level on slow decision cycles while a deterministic tactical layer handles fast execution between decisions**. SwarmBrain calls these the "Overmind Intelligence Matrix" and "Swarm ReflexNet" respectively.

For a 30–60 second REDFOR decision cycle, the architecture decomposes into three layers:

**Perception layer** (continuous, every 1–5 seconds): Two async tasks consume DCS-gRPC streams. One ingests `StreamUnits` at a 1-second poll rate, maintaining an in-memory world model of all unit positions, types, and states. The other ingests `StreamEvents`, updating the model with kills, launches, and captures. Periodically, `GetDetectedTargets` is called for key RED sensor units (EWRs, AWACS, CAP fighters) to build a fog-of-war-filtered picture. This raw state is then **abstracted** — individual aircraft become flight elements, which become packages; ground units aggregate into battalion-strength estimates by sector.

**Decision layer** (every 30–60 seconds): The abstracted state is formatted into a structured JSON observation and injected into the LLM prompt alongside strategic context (commander's intent, available reserves, recent outcomes). The LLM responds with structured tool calls representing operational orders. The recommended approach is vLLM or SGLang with `tool_choice="required"` and the appropriate parser (`qwen3_coder` for Qwen 3.5, `gemma4` for Gemma 4), which uses guided decoding to **guarantee valid JSON** conforming to the tool schema. Orders are **standing orders** — "CAP station Alpha with 2-ship F-14s, weapons free" persists until countermanded, not issued per-frame.

**Execution layer** (continuous, every tick): A deterministic Python state machine translates standing operational orders into specific DCS-gRPC/Olympus commands. When the LLM orders "Launch 4-ship strike package against Kutaisi from Vaziani," the executor spawns the group via Olympus, sets waypoints, assigns attack tasks via `ControllerService.SetTask`, and sets ROE via `SetOption`. Between LLM decisions, the executor handles tactical contingencies via rules: retreating damaged flights, reassigning CAP stations, triggering automatic defensive reactions.

Define **5–8 tool schemas** mapping to operational-level actions:

```python
tools = [
    {"name": "launch_air_package", "parameters": {"package_type": "CAP|STRIKE|SEAD|CAS|ESCORT",
     "aircraft_type": str, "count": int, "origin_base": str, "target_area": str, "roe": str}},
    {"name": "reassign_cap_station", "parameters": {"station_id": str, "new_location": dict}},
    {"name": "set_sector_priority", "parameters": {"sector": str, "priority": "high|medium|low"}},
    {"name": "withdraw_forces", "parameters": {"unit_group": str, "destination": str}},
    {"name": "commit_reserves", "parameters": {"reserve_id": str, "objective": str}},
    {"name": "adjust_roe", "parameters": {"scope": str, "roe_level": str}},
    {"name": "request_reinforcement", "parameters": {"unit_type": str, "quantity": int, "base": str}}
]
```

Keep schemas flat (avoid nesting), use `enum` constraints extensively, and include clear descriptions. For Qwen 3.5, enable thinking mode so the model reasons about threat assessments before committing to orders.

---

## Function calling works best with tool_choice="required" and XGrammar

Both vLLM and SGLang now provide robust function calling pipelines. The critical configuration for reliable game command generation:

**vLLM** serves an OpenAI-compatible API. Launch with:
```bash
vllm serve Qwen/Qwen3.5-35B-A3B \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --tensor-parallel-size 1 \
  --max-model-len 65536
```
Tools are passed via the standard `tools` parameter in chat completions. **Using `tool_choice="required"` activates guided decoding**, constraining the model's output to valid JSON matching the tool schemas — this eliminates the ~6.75% first-try success rate problem reported for complex schemas with unconstrained generation. XGrammar is the default structured output backend (fastest, C-based pushdown automaton); it falls back to Outlines for regex/choice constraints.

**SGLang** offers a unique `structural_tag` response format that's especially powerful for the commander use case — it allows the model to generate free-text reasoning followed by constrained tool call arguments:
```python
response_format = {
    "type": "structural_tag",
    "structures": [
        {"begin": "<function=launch_air_package>", "schema": package_schema, "end": "</function>"},
        {"begin": "<function=set_sector_priority>", "schema": priority_schema, "end": "</function>"}
    ],
    "triggers": ["<function="]
}
```
This guarantees the arguments within each function call tag conform to the JSON schema while allowing the model to reason freely before and between calls. SGLang's compressed FSM approach achieves up to **6.4x higher throughput** than competing systems on structured output tasks.

For multi-turn tool use (LLM issues command → harness executes → LLM receives result → issues next command), append tool results as `{"role": "tool", "content": json.dumps(result)}` messages. This enables reactive chains: the LLM orders a CAP launch, receives confirmation of successful spawn with unit IDs, then issues follow-on tasking referencing those IDs.

---

## Campaign state belongs in SQLite with event sourcing

DCS Liberation provides the best open-source reference for DCS campaign state modeling. Its domain hierarchy — `Game → Coalition → AirWing → Squadron → Flight` and `Theater → ControlPoint → TheaterGroundObject → TheaterGroup → TheaterUnit` — maps directly to the REDFOR commander's needs. Squadrons track **owned_aircraft**, **untasked_aircraft**, **pending_deliveries**, and pilot pools with availability/fatigue states. Control points model airbases, FOBs, FARPs, and carriers with runway health, parking capacity, and income value. The front line uses a graph-based `TransitNetwork` for pathfinding between control points.

Liberation's weakness is pickle serialization for persistence, which causes version incompatibility and prevents querying. For a real-time 30-second-cycle agent, the recommended pattern is **SQLite in WAL mode with event sourcing**:

- **Event log** (append-only): Every state change — unit destroyed, base captured, fuel consumed, order issued — is recorded as an immutable event with timestamp, entity ID, and JSON payload. This provides full audit trail and temporal replay.
- **Materialized state tables**: `units`, `squadrons`, `control_points`, `air_packages` tables derived from events. SQLite WAL mode supports concurrent reads (AI queries) during writes (telemetry ingest).
- **In-memory working set**: Hot state (current positions, active threats, available forces) in Python dataclasses for sub-millisecond access during observation formatting.

Resource tracking follows Command: Modern Operations patterns at reduced fidelity: per-base fuel storage and ammunition magazines, per-squadron aircraft inventory with status state machine (mission_ready → on_mission → returning → turnaround → mission_ready), and pilot availability with sortie-count-based fatigue. For the 30-second decision cycle, **delta encoding** is critical — present the LLM with "since last decision: 2 RED MiG-29s destroyed over Sector Bravo, 1 SA-11 TEL killed at grid 4532" rather than dumping full state each cycle. Hierarchical aggregation (units → flights → packages → force packages) keeps the observation within context window limits even for theater-scale scenarios.

---

## Putting it all together

The complete harness is roughly **2,000–3,000 lines of Python** organized into five modules: a gRPC perception client (async, consuming StreamUnits and StreamEvents), an Olympus REST command client, a state manager (SQLite + in-memory world model), an LLM interface (OpenAI-compatible client talking to local vLLM/SGLang), and a tactical executor (state machine translating operational orders to API calls). The decision loop runs as a single `asyncio` event loop: perception tasks update the world model continuously, a timer fires the LLM decision cycle every 30–60 seconds, and the executor drains the action queue between cycles.

For dual 3090s, **dedicate GPU 0 to DCS World and GPU 1 to the LLM** via `CUDA_VISIBLE_DEVICES=1` for vLLM. Run Qwen 3.5 35B-A3B at Q4 (20GB VRAM, 112 tok/s) or Gemma 4 26B-A4B at Q4 (18GB, 119 tok/s). For the Mac Studio, run MLX with Qwen 3.5 397B-A17B at 4-bit (214GB) for maximum reasoning quality, accepting ~35 tok/s generation speed — still under 15 seconds for a 500-token operational order, well within the 30-second decision budget. DCS World would run on a separate Windows machine connected over the network in this configuration, with DCS-gRPC bound to `0.0.0.0:50051` and Olympus backend set to `"address": "*"` for remote access.

## Conclusion

The core technical risk is not model capability or API availability — both are mature enough today. The risk is in **state abstraction quality**: converting raw DCS telemetry (potentially thousands of units with per-second position updates) into a concise, decision-relevant observation that fits within even a 256K context window without losing operationally significant information. The hierarchical aggregation pattern (individual units → tactical elements → operational formations) with salience-based filtering (emphasize active engagements, suppress quiet sectors) is the design challenge that will most determine whether the REDFOR commander makes competent decisions. Start with a small scenario (4v4 CAP engagement), validate the full perception→decision→execution loop, then scale state complexity incrementally. The MoE architectures of both Qwen 3.5 35B-A3B and Gemma 4 26B-A4B — with their low active parameter counts yielding sub-10-second inference — make iterative prompt engineering and schema tuning practical without waiting minutes per test cycle.