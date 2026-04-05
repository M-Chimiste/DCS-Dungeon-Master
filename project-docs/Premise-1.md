# Building an AI opponent on DCS Olympus

**DCS Olympus exposes a REST API from a C++ DLL running inside DCS World, making it fully programmable by an external AI agent — but no one has done it yet.** The tool already supports coalition-filtered fog of war, a spawning budget system, and real-time unit command — the exact primitives an LLM-based commander agent needs. Combined with DCS-gRPC for high-performance streaming sensor data and DCS Liberation's open-source campaign AI as an architectural reference, all the building blocks exist today to create a dynamic campaign AI opponent. This report maps every integration point, constraint mechanism, and design decision required to make it happen.

---

## How DCS Olympus talks to the simulation

DCS Olympus is a two-process architecture. **Component A** is a native C++ DLL loaded by DCS World as a mod/service from `Saved Games\DCS\Mods\Services\Olympus\`. This DLL embeds a lightweight HTTP server (likely cpp-httplib) that exposes a **REST API on port 3001** (configurable; some versions use 4512). It sits inside the DCS process, bridging C++ code with DCS's embedded LuaJIT VM via `net.dostring_in()` calls into the server scripting environment. The DLL requires two `autoexec.cfg` permissions: `net.allow_unsafe_api = { "userhooks" }` and `net.allow_dostring_in = { "server" }`.

**Component B** is a Node.js web server on port 3000 that serves the TypeScript/Leaflet.js map interface as static assets. Critically, B never communicates with A — the browser client downloads the UI from B, then talks directly to A's REST API for all game state and commands. A third optional component provides WebSocket-based SRS radio integration on port 4000.

The REST API endpoints are not formally documented (no OpenAPI spec exists), but the frontend TypeScript code — which constitutes **73.2%** of the codebase — reveals the full surface. The API accepts JSON over HTTP, uses SHA256-hashed password authentication for four roles (Game Master, Blue Commander, Red Commander, Admin), and supports SSO via custom `X-Authorized`/`X-Group` headers. Setting `backend.address` to `"*"` in `olympus.json` opens the API to external clients, which is exactly what an AI agent would need.

**Readable data** includes real-time unit positions, types, coalitions, categories (aircraft/helo/ground/naval), health, altitude, speed, heading, human-vs-AI status, bullseye positions, airfield ownership, connected players, mission editor drawings, and coalition-filtered detection data. **Writable commands** span spawning any DCS unit type with configurable loadout/livery/skill, single and multi-waypoint movement, orbit patterns, combat tasks (attack, bomb, lase, follow, refuel, land), ROE and alarm state, radar on/off, formations, effects (smoke/flares/explosions/napalm), grouping/ungrouping, and unit deletion.

The overall language breakdown is TypeScript (73.2%), Lua (19.5%), Python (3.7% — build scripts), and C++ (1.8% — the backend DLL). The Electron-based Olympus Manager handles installation and updates.

---

## No built-in AI commander, but the API is agent-ready

Olympus is explicitly designed as a human-operated game master tool. The wiki states: *"Using Olympus requires a human to play it 'live' like a game master; it is not a tool for pre-game mission creation or planning."* There are **no built-in AI commander, automated decision-making, or bot features**. No existing plugins or community scripts automate REDFOR or BLUFOR decisions through Olympus.

However, the REST API is inherently agent-ready. DCSServerBot — a Python-based server management tool — already integrates with Olympus programmatically via its REST API, proving external automation works. The `controllers` array in `olympus.json` hints at an extensibility mechanism for external controllers. The authentication system's Red Commander role provides exactly the access scope an AI agent needs: full control of red coalition units, filtered to red coalition sensor data only.

The DCS modding ecosystem does include sophisticated AI behavior frameworks. **MOOSE** (Mission Object Oriented Scripting Environment) provides rule-based AI dispatchers for CAP, CAS, SEAD, and other mission types via Lua. **Skynet-IADS** simulates realistic integrated air defense networks with radar management and HARM evasion. **SkyEye** is an AI-powered GCI bot using OpenAI Whisper for speech recognition and Piper TTS for natural voice. But none of these operate at the operational/campaign commander level that an LLM agent would target.

---

## Architecture for an external LLM-based commander agent

Building an AI agent that commands REDFOR through DCS Olympus requires four layers: perception, cognition, decision, and action. The most practical architecture uses **DCS-gRPC as the primary data ingestion layer** and **DCS Olympus's REST API as the command interface**, with an LLM (Qwen3 or Gemma) providing operational reasoning.

**Perception layer.** DCS-gRPC (a Rust DLL exposing gRPC on port 50051) provides `StreamUnits` for real-time position streaming and `UnitService.GetDetectedTargets()` for per-unit sensor data — filtered by detection type (VISUAL, OPTIC, RADAR, IRST, RWR, DLINK). An agent should poll each friendly unit's detections and fuse them into a Common Operating Picture. DCS-gRPC uses Protocol Buffers with native bidirectional streaming, handling **600 calls/second** into the mission scripting environment. Python connects via standard `grpcio` stubs generated from the proto files in `protos/dcs/`.

```python
# Minimal DCS-gRPC Python client
import grpc
channel = grpc.insecure_channel('127.0.0.1:50051')
# Use generated stubs from protos/dcs/ directory
# coalition_client.StreamUnits() for real-time positions
# unit_client.GetDetectedTargets() for sensor-filtered contacts
```

**Cognition layer.** The LLM receives a structured text representation of the fused battlespace picture — friendly force disposition, detected enemy contacts with confidence levels, resource status, airfield ownership, and frontline positions. DCS Liberation's architecture provides the best reference: it models the theater as a **graph of control points connected by edges**, with squadrons assigned to airbases, and plans coordinated **packages** (SEAD + Strike + Escort + CAP) rather than individual flights. An LLM agent should adopt this package-based planning paradigm.

**Decision layer.** The agent outputs structured JSON commands: which units to spawn (within budget), what packages to form, target prioritization, waypoint assignments, and ROE settings. A function-calling LLM like Qwen3 maps naturally to this — define tools for `spawn_group()`, `set_waypoints()`, `assign_task()`, `set_roe()`, and let the model call them. A decision cycle of **30–60 seconds** is realistic for operational-level command; tactical AI (individual unit maneuvering) should be delegated to DCS's native AI or MOOSE dispatchers.

**Action layer.** Commands are issued via HTTP POST to Olympus's REST API at `http://localhost:3001/olympus/`, authenticated with the Red Commander password. Alternatively, DCS-gRPC's `CoalitionService.AddGroup()` and `ControllerService` can handle spawning and tasking directly. Using both tools in combination is viable — Olympus for high-level spawning with its rich unit database, DCS-gRPC for fine-grained task control and event streaming.

---

## Fog of war is solvable with existing APIs

DCS Olympus already implements coalition-restricted views. When logged in as Red Commander, the operator sees *"only what their respective systems can see"* — units won't reveal type or model until a friendly sensor identifies them. This UI-level enforcement maps directly to DCS's underlying detection model.

DCS's Lua API exposes **six sensor types** through `Controller.getDetectedTargets()`: VISUAL (eyeball), OPTIC (targeting pods), RADAR (air/ground/naval), IRST (infrared), RWR (radar warning receiver — bearing only), and DLINK (shared via data link). Each detection returns whether the target is currently `visible`, whether its `type` is identified, whether `distance` is known, and stale track data (`lastPos`, `lastVel`) when the contact fades.

To ensure the AI doesn't cheat, the agent should **never query omniscient world state**. Instead:

- Query `getDetectedTargets()` on every friendly REDFOR unit via DCS-gRPC's `UnitService`
- Fuse detections into a coalition-level picture with confidence scores
- EWR radar provides long-range detection without type ID; fighter radar adds type identification at medium range; IRST is passive but range-poor; VISUAL gives positive ID at short range
- Degrade stale tracks over time: `estimatedPos = lastPos + lastVel × timeDelta`
- Multiple sensors on the same target increase track confidence
- If Olympus's Red Commander API already filters data by coalition, an agent connecting as Red Commander automatically receives only REDFOR-visible data — **no additional filtering code needed**

The remaining risk is that DCS-gRPC's `StreamUnits` without coalition filtering returns all units. The agent must discipline itself to ignore raw streams and only use `getDetectedTargets()`, or a middleware layer should enforce this. Building a **"sensor fusion service"** that wraps DCS-gRPC and only exposes detection-based data to the LLM is the cleanest architecture.

---

## Resource constraints require a custom logistics layer

Olympus provides a **basic spawning budget and cost system** for its PvP/RTS mode. Each unit in the database has a `cost` property and an `era` tag. The Game Master configures per-coalition budgets and era restrictions, and commanders can only spawn units they can afford from permitted eras. This is functional for balanced gameplay but is not a full logistics simulation.

**What Olympus does not model**: supply chains, ammunition expenditure tracking, fuel consumption, runway repair timelines, attrition replacement rates, force generation cycles, or infrastructure dependencies. DCS Liberation provides a richer reference: it models **factories** ($10M/turn income + ground unit production), **ammunition depots** (limiting frontline deployments), **runway damage and repair** ($100M, 4 turns), **squadron sizes** with permanent attrition, and a **graph-based control point network** where ground convoys move between nodes and can be interdicted.

To create realistic resource constraints for an AI opponent, you would need to build a **campaign state layer** on top of Olympus:

- **Force pool**: Define a finite ORBAT (Order of Battle) the AI can draw from, tracked in an external database
- **Attrition**: Hook DCS-gRPC's `StreamEvents` to detect unit deaths and permanently remove them from the force pool
- **Sortie generation**: Limit how many aircraft can launch per time period based on airbase capacity and maintenance modeling
- **Ammunition/fuel**: Track expenditure per sortie and require "resupply" delays
- **Reinforcement pipeline**: New units arrive on a schedule (e.g., a squadron of Su-27s available after 48 in-game hours) rather than instantly
- **Economic model**: Control points generate income; losing territory reduces the AI's budget

This campaign state layer would sit between the LLM decision engine and the Olympus/gRPC command interface, validating every spawn request against available resources before forwarding it.

---

## The landscape of AI agents in military simulation

No existing project combines LLMs with DCS World for operational command. The closest efforts span several categories:

**Dynamic campaign engines.** DCS Liberation is the gold standard — a Python-based turn-based campaign generator with rule-based AI that plans coordinated air packages, manages economics, and models IADS networks. Its AI uses heuristics for target prioritization and force allocation, not machine learning. **DCC (Digital Crew Chief)** attempted similar functionality but development has stalled.

**Tactical AI research.** DARPA's **Air Combat Evolution (ACE)** program trained deep RL agents for F-16 dogfighting; Heron Systems' agent beat a human pilot 5-0 in the 2020 AlphaDogfight Trials, and AI agents have since flown the X-62A VISTA in live combat against manned F-16s. Academic papers have applied PPO and SAC reinforcement learning to DCS World for within-visual-range air combat. But these focus on single-aircraft maneuvering, not operational command.

**LLM wargaming.** Several 2024–2026 papers explore LLMs as wargame players: **WarAgent** simulates historical conflicts with country-level LLM agents; **Snow Globe** automates qualitative wargames with multi-agent LLM systems; the **U.S. Army War College** uses LLMs as adjudicators in their "Pacific Strategy" game. These demonstrate LLMs can reason about military operations but operate in abstract, text-based environments rather than high-fidelity simulators.

**Voice/GCI bots.** **SkyEye** (Go, Whisper, Piper TTS) provides AI-powered ground-controlled intercept over SRS radio, reading Tacview telemetry and issuing AWACS-style calls. **OverlordBot** (C#, Azure) does similar work. These prove AI can operate in the DCS ecosystem in real-time but handle only the narrow GCI role.

**Infrastructure tools.** **DCS-gRPC** (Rust, gRPC, 103+ stars) is the most capable external API for DCS, with streaming unit data, event streams, per-unit detection queries, and coalition-level commands. **MOOSE** (Lua, 335+ stars) provides sophisticated in-mission AI dispatchers. **pydcs** (Python) generates mission files programmatically. **DCSServerBot** (Python) manages servers and integrates with Olympus. Together, these form a complete toolchain for an AI agent — but no one has assembled them into a commander-level system.

---

## Concrete integration roadmap

A Python-based AI commander agent connecting to DCS Olympus would follow this technical path:

**Phase 1 — API discovery.** Clone the DCSOlympus repo (`release-candidate` branch) and grep C++ source for HTTP handler registrations (`svr.Get()`, `svr.Post()`) and TypeScript source for `fetch()` calls. Map every REST endpoint, its HTTP method, request body schema, and response format. Alternatively, run Olympus and use browser DevTools Network tab to capture all API traffic — this is the fastest path to a complete endpoint catalog.

**Phase 2 — Data pipeline.** Install DCS-gRPC alongside Olympus (they coexist as separate DCS mods). Generate Python protobuf stubs from `protos/dcs/`. Build a `BattlespaceFusion` service that subscribes to `StreamEvents`, periodically queries `GetDetectedTargets()` on all REDFOR units, and maintains a fused COP with track confidence scores and staleness.

**Phase 3 — Decision engine.** Wrap an open-source LLM (Qwen3-30B-A3B or Gemma-3-27B) with function-calling tools mapped to Olympus REST commands. Feed it a structured battlespace summary every 30–60 seconds. Define tools: `spawn_package(type, composition, target)`, `assign_cap(group_id, patrol_area)`, `vector_intercept(group_id, contact_id)`, `set_roe(group_id, roe)`. Use Liberation-style package planning as the prompt engineering framework.

**Phase 4 — Resource constraints.** Build a `CampaignState` module tracking force pools, budgets, attrition, and reinforcement schedules. Validate all LLM-issued spawn commands against this state before forwarding to Olympus. Hook DCS-gRPC events (S_EVENT_DEAD, S_EVENT_CRASH) to update attrition tracking.

**Phase 5 — Fog of war enforcement.** Insert a `SensorFilter` middleware between the raw DCS data and the LLM context window. This layer only passes detection-based contacts (never omniscient unit lists) and degrades stale tracks. If using Olympus's Red Commander role, the REST API already filters coalition data — verify this provides sufficient restriction or supplement with DCS-gRPC detection queries.

The full stack is: **DCS World** → **Olympus DLL + DCS-gRPC DLL** (in-process) → **REST API + gRPC** (network) → **Python sensor fusion + campaign state** (middleware) → **LLM decision engine** (Qwen3/Gemma) → **Python command issuer** → **REST API / gRPC** → **DCS World**.

---

## Conclusion

DCS Olympus provides roughly **80% of the infrastructure** needed for an LLM-based dynamic campaign AI opponent. Its REST API offers full unit spawning and command, its coalition roles enforce fog of war at the API level, and its budget system provides basic resource constraints. The missing 20% — formal API documentation, a logistics/attrition layer, and the AI decision engine itself — must be built externally.

The most important architectural insight is that **DCS-gRPC and Olympus are complementary, not competing**. gRPC provides the high-frequency streaming perception pipeline (unit tracks, sensor detections, events) while Olympus provides the high-level command vocabulary (spawn with loadout, set formation, assign tasks). An agent should read through gRPC and write through Olympus.

The fact that no one has yet built an LLM commander for DCS is not a technical limitation — it's an opportunity gap. Every integration point exists. The DCS scripting engine exposes per-unit, per-sensor detection data sufficient for honest fog of war. Liberation's open-source campaign AI demonstrates that package-based operational planning works. The remaining engineering challenge is prompt design: teaching an LLM to reason about force ratios, SEAD sequencing, timing coordination, and adaptive re-planning when contacts change — which is exactly the kind of structured reasoning modern function-calling models like Qwen3 are built for.