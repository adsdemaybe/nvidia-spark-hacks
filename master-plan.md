# STRUCT — Master Project Plan

**Text → working robot: design it, build its board, replicate its world, train it, and hand humans the controller.**

**Status:** master plan, ready for coding. Feats 1–2 have detailed sub-plans
(`text-to-pcb-plan.md`, `text-to-cad-plan.md` in the project docs); this document is
the source of truth for everything shared, for feats 3–5, and for the 40-hour
execution schedule.

---

## 0. Decisions locked (2026-08-14)

| Decision | Choice | Consequence |
|---|---|---|
| Framing | Semi-production, multiagent coding, ~40h to demo | Vertical slices with production-shaped interfaces; nothing throwaway |
| Compute | **NVIDIA DGX Spark** (GB10 Grace Blackwell, 128 GB unified, aarch64 Linux) | Isaac Sim/Lab, gsplat training, and SmolVLA fine-tuning all run locally — NVIDIA ships official DGX Spark playbooks for Isaac. Everything must build on **aarch64**; x86-only wheels are a blocker to catch at hour 0 |
| Repo | **Monorepo**, shared infra | One compose stack, one job system, one artifact store, one provenance model |
| PCB ↔ CAD | Each exposes an **API + MCP server**; they negotiate fit in a feedback loop | Contract in §6 |
| Base policy model | **LeRobot SmolVLA** (ACT as fallback) | Fine-tunes fast on Spark; native LeRobot dataset format is the interchange for feats 3+4 |
| Scan capture | **Both**: iPhone LiDAR (Polycam/Scaniverse) for scaled mesh + phone video → COLMAP → gsplat for appearance | LiDAR mesh is the geometric truth; splat is the visual truth |
| AR/VR | **WebXR**, recommended device: **Meta Quest 3** running the WebXR app in-browser. **AR-first (decision: 2026-08-14):** passthrough AR (`immersive-ar`) is the primary mode for both feats 4 and 5 — easier to demo (judges see the real room, no disorientation, hand-off in seconds); fully-immersive VR (`immersive-vr`) is the secondary mode, used only when a task needs an environment that isn't physically present | Zero install, device-agnostic, hand-tracking via WebXR Hand Input API; Quest 3 color passthrough is what makes the AR-first call viable |
| RL sim | **Isaac Lab on the Spark** (primary, GPU-parallel PPO + USD + splat visuals), **MuJoCo** (fast checks, dataset replay verification) | Matches the project's Omniverse direction; MuJoCo stays the portable second opinion |
| Demo | **Sim-only** — no physical robot in 40h | Demo embodiment: SO-101 arm in sim, **LeRobot official URDF as the single source of truth** (matches SmolVLA's pretraining embodiment; USD/MJCF converted from it, never hand-edited) |
| Team | **5 humans, one per feat** + agent fleet | 5 tracks (§10); shared infra is a joint hour-0–4 job: F1 owner takes TS-side, F2 owner takes compose/core-py |
| Board fab | **Order after the hack** | Demo deliverable is the verified Gerber+BOM bundle; L8 still runs the JLC DFM profile so the post-hack order is one click, not a redesign |
| Room generality | **F3 is room-agnostic by requirement** | Any room — bedroom, venue, whatever — through the same pipeline with zero per-room code; the fixture scan and the venue scan are just two test cases of one path |
| F3/F4 task systems | **Separate**, connected by an internal fine-tune API | Each feat defines its own tasks; both emit LeRobot v3 datasets to the hub's `/finetune` API, which unions data sources for SmolVLA (§8) |
| Coding model ops | **Qwen3 27B (pivot, 2026-08-15) — Laguna S 2.1 NVFP4 is retired as the primary.** Laguna's NVFP4 checkpoint is 93 GB on disk and vLLM held 97–117 GB of the GB10's 121 GB unified pool, which left ~1–4 GB and made it impossible to run Isaac Sim, gsplat or a fine-tune at the same time; lowering `--gpu-memory-utilization` frees almost nothing because the weights are the floor. Measured behaviour before the pivot: excellent reviews (3 blockers with quantitative fixes on the rover), but ~27 minutes stuck in the designer stage under `--enforce-eager` at 16k context. The provider layer is model-agnostic and already proven across three backends, so the pivot is config. Originally: **Laguna S 2.1 NVFP4 powers the design loops themselves**: Laguna agents generate the CAD (build123d) and PCB (tscircuit HDL) code *and* invoke the deterministic systems — lint, compile, ERC, route, DRC, `evaluate()`, sim tiers — reading each report and revising in the feedback loop. This loop against deterministic gates is what makes the algorithm viable; the model is grounded by a **docs RAG (pgvector on the shared Postgres)**. Model-agnostic by construction: any agent slot falls back to Claude (Claude Code running the same prompts/commands) on failure | The invariant holds regardless of model: Laguna proposes and runs the tests, but only the deterministic harness says "pass". RAG corpus: tscircuit, build123d, Isaac Lab, LeRobot docs, embedded locally on the Spark |
| Feature list | **Finalized — all 5 feats ship** | Scope inside a feat can thin under the cut lines (§11), but no feat is dropped; post-hack hardening continues across all five even where 40h implementation is partial |

---

## 1. What Struct is

A user types what they want. Struct produces, end to end:

1. **text-to-pcb** — a manufacturable PCB (tscircuit HDL → routed, DRC×2-clean,
   physics-checked Gerbers) via an agent loop where deterministic tooling owns
   correctness.
2. **text-to-cad** — parametric CAD (build123d) validated by compilation, mass
   properties, simulation tiers, and a measurable criteria system.
3. **RL environment replication** — scan a real room, segment it into assets,
   rebuild a blank room + asset library in sim, then let a policy randomize
   placements and generate tasks, running RL until success and fine-tuning
   SmolVLA for the user's scenario.
4. **AR/VR training** — WebXR "games" (blend a smoothie, sort the desk), **AR
   passthrough by default** with virtual props anchored in the real room (VR as
   the secondary mode), whose human hand-tracking data flows through a
   retargeting pipeline into LeRobot-format training data.
5. **AR/VR digital twinning** — the reconstructed environment anchored back onto
   the real room in passthrough AR, with sim state overlaid live.

The demo story that ties them: *design a robot arm's controller PCB (1), design
the enclosure/chassis that provably fits it (2), scan the hackathon room and
train the arm to do a task in a randomized replica of it (3), collect human
demonstrations for that same task in AR (4), and watch the trained policy run
overlaid on the real room (5).*

### 1.1 The one invariant — inherited by every feat

> **Agents propose. The harness disposes.**

An agent never declares a PCB routable, a part manufacturable, a placement
physically plausible, a demonstration usable, or a policy trained. Deterministic
tools measure; agents interpret and revise. Every gate is a hard gate. Every
pipeline stage fails at the earliest, cheapest point. Every physical constant
carries provenance (`CONFIRMED | INFERRED | ASSUMED | MEASURED`). Every artifact
is hashed and immutable. This is already the spine of the PCB and CAD plans;
feats 3–5 adopt it wholesale (see each feat's "deterministic gates" list).

---

## 2. System map

```
                                ┌─────────────────────────────┐
   user intent ───────────────▶ │   STRUCT hub (FastAPI+MCP)  │◀──── WebXR client (Quest 3)
                                └──────┬──────────────────────┘
          ┌────────────┬───────────────┼──────────────┬──────────────┐
          ▼            ▼               ▼              ▼              ▼
   ┌────────────┐ ┌────────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐
   │ F1 pcb     │ │ F2 cad     │ │ F3 envrep │ │ F4 xrdata │ │ F5 twin   │
   │ tscircuit  │◀▶ build123d  │ │ scan→sim  │ │ hands→    │ │ splat→AR  │
   │ loop (TS)  │MCP│ loop (Py)│ │ →RL (Py)  │ │ LeRobot   │ │ anchor    │
   └─────┬──────┘ └─────┬──────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
         │              │              │             │             │
         ▼              ▼              ▼             ▼             ▼
   Circuit JSON      STEP/GLB      USD scene +   LeRobot        aligned
   + Gerbers         + Robot IR    SmolVLA ckpt  dataset v3     splat + poses
         └──────────────┴──────┬───────┴─────────────┴─────────────┘
                               ▼
              shared: Postgres · Redis/Dramatiq · MinIO (S3) · provenance ledger
```

Interchange formats are the only cross-feat contract: **Circuit JSON** (F1),
**Robot IR + STEP/GLB** (F2), **USD + scene.json** (F3/F5), **LeRobot dataset
v3** (F3/F4), **task.json** (F3/F4). Freeze these at hour 4 (§8).

---

## 3. Shared stack (decided)

| Layer | Choice | Notes |
|---|---|---|
| Languages | Python 3.11 (uv) for engines/pipelines; TypeScript (Node 22, pnpm workspaces) for tscircuit + WebXR + frontend | |
| Orchestration | **LangGraph** everywhere an agent loop exists; SQLite checkpointers per feat (Postgres checkpointer post-hack) | Both sub-plans already chose it |
| LLM | **Poolside Laguna S 2.1 (NVFP4 quant, `poolside/Laguna-S-2.1-NVFP4`) served locally on the Spark via vLLM** as the primary coding/agent model — NVFP4 is Blackwell-native, ~118B MoE / 8B active fits comfortably in 128 GB unified. Claude API as the escalation tier for the hardest design/reasoning agents; provider-agnostic wrapper (`initChatModel` on TS side, one small module on Py side) so per-agent provider is config, not code | Structured outputs only (Zod / Pydantic); parse failure = bounded retry. **Contention caveat:** Laguna inference, Isaac Lab RL, gsplat training, and SmolVLA fine-tuning all share the Spark's memory/compute — see risk table and §10 scheduling |
| Jobs | **Dramatiq on Redis** | Idempotent by input hash; heartbeat + reaper; no hand-rolled workers |
| Storage | Postgres 16 (designs/revisions/evaluations, append-only), MinIO (artifacts, S3 API), Redis 7 | one `compose.yaml` |
| API | FastAPI hub; every long op returns a job id; SSE progress | |
| MCP | Every feat ships an MCP server (FastMCP for Python feats, TS MCP SDK for pcb). The hub mounts them; PCB↔CAD talk over MCP (§6) | Also makes every feat drivable by the coding-agent fleet itself |
| Docs RAG | **pgvector** extension on the shared Postgres; local embeddings on the Spark; corpus: tscircuit, build123d, Isaac Lab, LeRobot, WebXR docs | Grounds Laguna's codegen; loaded during H0–4 infra setup, before the first agent runs |
| Fine-tune API | Hub endpoints: `POST /finetune/datasets` (register any LeRobot-v3 dataset) + `POST /finetune/runs` (SmolVLA fine-tune over a dataset union) | The F3↔F4 bridge — sim rollouts and human XR episodes meet only here |
| Sim | Isaac Sim 5 + Isaac Lab (Spark, via NVIDIA dgx-spark-playbooks); MuJoCo (pip, aarch64 fine) | Drake cut for 40h (CAD plan's tier 3 → post-hack) |
| Splatting | nerfstudio + gsplat (CUDA, build on aarch64), COLMAP (apt/spack) | Polycam export path needs no GPU |
| Segmentation | Grounded-SAM-2 (text-prompted masks) on capture frames | |
| CAD kernel | build123d/OpenCascade; GLB via trimesh; collision hulls via CoACD | |
| Robot/policy | LeRobot (SmolVLA fine-tune, dataset v3); Pinocchio for IK/retargeting | |
| Frontend | React + TS; Three.js + @react-three/xr (WebXR); `<model-viewer>` for GLB cards; Viser for engine-side robot inspection | |
| Telemetry | rerun-sdk for rollout/retarget traces; Parquet (pyarrow) for episode data | |

**Hour-0 preflight (blocker check, one script):** `uname -m` = aarch64; CUDA +
torch on Spark; Isaac Lab playbook smoke test; gsplat compile; MuJoCo import;
COLMAP binary; kicad-cli; Java ≥ 25 (Freerouting); ngspice; **vLLM serving
Laguna-S-2.1-NVFP4 with a structured-output smoke test (tool call + JSON schema
round-trip — agent loops die without this)**; Quest 3 reaches the dev server
over LAN (HTTPS cert for WebXR!). Any failure has a named owner and a
named fallback before work starts.

### 3.1 Monorepo layout

```
struct/
├── compose.yaml                  # postgres, redis, minio, api, workers
├── preflight.sh                  # the hour-0 check above
├── docs/                         # THIS FILE + feat sub-plans
├── packages/
│   ├── core-py/                  # provenance, Quantity, artifact client, job client, schemas
│   ├── ir/                       # Robot IR (Pydantic v2) — from the CAD plan §2
│   ├── cad-engine/               # build123d generators, criteria, evaluate()  (F2)
│   ├── pcb/                      # tscircuit loop, eslint-plugin-pcb, stage ladder (F1, TS)
│   ├── scan/                     # capture ingest → COLMAP → gsplat → seg → assets (F3a)
│   ├── envgen/                   # blank room, randomizer, task gen, USD/MJCF export (F3b)
│   ├── rl/                       # Isaac Lab tasks, PPO teacher, distill → SmolVLA (F3c)
│   ├── xr/                       # WebXR app: games, recorder, twin viewer (F4+F5, TS)
│   ├── datapipe/                 # hand traces → retarget → LeRobot dataset v3 (F4)
│   └── mcp-servers/              # one server per feat + hub manifest
├── apps/
│   ├── api/                      # FastAPI hub
│   └── web/                      # React dashboard: jobs, artifacts, viewers
└── fixtures/                     # golden boards, golden IRs, a pre-captured sample scan
```

`fixtures/` matters: **a pre-captured scan of a real room goes in the repo on
hour 1** so F3's pipeline develops against fixed data while someone captures the
venue room properly.

---

## 4. Feat 1 — text-to-pcb (summary; full plan in `text-to-pcb-plan.md`)

**Laguna agents** write the tscircuit HDL and drive the harness — they invoke
each stage, read its report, and revise; a deterministic stage ladder L0–L10
(lint → compile → ERC → place → route → DRC×2 → physics → SPICE → DFM →
artifacts → regression) owns every verdict. `eslint-plugin-pcb` v0.1 is already shipped and wired as L0.

**40h scope:** the loop as proven in the PoC + L0 lint + L2 ERC
(`@tscircuit/checks`) + KiCad second-opinion DRC (kicad-cli is scriptable and
headless) + the **MCP surface below**. SPICE (M3), Freerouting fallback (M4),
regression suite (M5) are post-hack unless time appears.

**Demo board:** the SO-101-class arm's controller (MCU + motor drivers + power
rail + connectors) — so the PCB in the demo is *the robot's own board*.

## 5. Feat 2 — text-to-cad (summary; full plan in `text-to-cad-plan.md`)

Robot IR (topology as data), geometry generator registry, criteria with
mandatory metrics, coverage analysis (BLIND/FRAGILE), provenance everywhere.
**Laguna agents** author the build123d/IR revisions and run the compile +
evaluation tiers in the LangGraph loop, iterating on compiler feedback and sim
reports — but `evaluate()` alone returns verdicts.

**40h scope = milestones M1–M5 compressed:** IR + registry, mass properties +
tier 0/1 (analytic + Pinocchio), criteria + `python -m engine.evaluate`, MuJoCo
tier for settle/fit checks, coverage analysis. Postgres/Redis come free from
shared infra. Drake, HIL, optimization beyond coordinate descent: post-hack.

**Demo part:** the enclosure/mount that houses Feat 1's board — designed through
the negotiation loop in §6, with `mount_fits` + envelope criteria as the gates.

## 6. The PCB ↔ CAD feedback loop (MCP contract)

Each side exposes MCP tools; a deterministic negotiator (not an LLM) drives the
loop and stops it.

**pcb server:**
- `pcb.design(spec) → {circuit_json, board_report}` where `board_report =
  {outline_mm, mounting_holes[], component_heightmap, connector_edges[],
  keepouts[], thermal_hotspots[]}`
- `pcb.replace_within(envelope) → new board_report` — re-place/re-route inside
  a CAD-imposed envelope (outline, max height, hole pattern)
- `pcb.check_fit(enclosure_report) → violations[]`

**cad server:**
- `cad.design_enclosure(board_report, intent) → {step, glb, enclosure_report}`
  where `enclosure_report = {cavity_mm, standoff_positions[], port_cutouts[],
  wall_thickness, max_component_height}`
- `cad.constrain_board(reason) → envelope` — what the enclosure can accept
- `cad.check_fit(board_report) → violations[]` (runs the `mount_fits` +
  clearance criteria deterministically)

**Loop:** intent → `pcb.design` → `cad.design_enclosure` → both `check_fit`s →
if either reports violations, the side with more freedom moves first
(`pcb.replace_within(cad.constrain_board())`), re-check → converge. Hard stop at
3 rounds; a non-converged pair is reported as such, never papered over. Both
`check_fit`s are deterministic geometry checks — agents never adjudicate fit.

**Status (2026-08-15): the geometric loop runs, both halves live.** `pcb-ai` serves
`/pcb/…` (including `replace_within`, which had been the missing side), `cad-generation`
serves `/cad/…` on aarch64 with build123d, and `tools/negotiate.ts` drives them. First
run converged on an 83.0×65.0×15.9 mm cavity with one real finding — the rover declares
no mounting holes, so nothing secures it in the shell.

### 6.1 From geometric fit to physical proof

Geometry agreeing is not the robot working. A board can fit its enclosure perfectly and
still fail to move the arm, because nothing yet checks that the current reaching the
motor produces the torque the joint needs against back-EMF and load.

**`electromechanical-cosim-plan.md` is the sub-plan for that**, and it extends this loop
from two participants to three: transient SPICE for the board's behaviour, a ZeroMQ
pub/sub wire as the virtual harness, and MuJoCo for the mechanics — with `motor/state`
(ω, load) fed *back* into the electrical solve so the coupling is real rather than
one-way. Same stop condition: three rounds, non-convergence reported.

The discriminator that keeps the loop from thrashing is deterministic, not a judgement
call: **if peak current is at the driver's limit the fault is electrical and goes to F1;
if current is well inside limits and the joint is still slow, it is mechanical and goes
to F2.**

---

## 7. Feat 3 — full RL environment replication (the new build)

The pipeline, with a deterministic gate after every stage:

```
capture ─▶ reconstruct ─▶ segment ─▶ assetize ─▶ blank room ─▶ randomize+tasks ─▶ RL ─▶ distill
  (a)         (b)           (c)        (d)          (e)             (f)           (g)     (h)
```

**(a) Capture.** Two inputs per room: Polycam/Scaniverse LiDAR scan (scaled
mesh, GLB/OBJ export) + 1–3 min phone video orbit. Gate: mesh is watertight-ish
and metrically scaled (known object check); video has ≥60% frame overlap.

**(b) Reconstruct.** COLMAP poses → nerfstudio `splatfacto` (gsplat) on the
Spark (~20–40 min). Align splat to LiDAR mesh with a similarity transform
(teaser-style correspondence or manual 3-point + ICP refine) so **the splat
inherits metric scale from LiDAR**. Gate: alignment RMS < 3 cm on floor plane.

**(c) Segment.** Grounded-SAM-2 with text prompts over capture frames
("table", "monitor", "keyboard", "bed", …; prompt list proposed by an agent from
a frame grid, *verified by mask coverage stats, not by the agent*). Lift 2D
masks to 3D by majority-vote assignment of gaussians across views. Gate: ≥90% of
gaussians assigned; each asset's gaussian cluster is spatially connected.

**(d) Assetize.** Per asset: crop LiDAR mesh by the 3D cluster's bbox → GLB
(visual: baked splat render or mesh texture) + CoACD collision hulls + inertial
props from bbox volume × ASSUMED density (provenance-labelled) →
`asset_bundle/{glb, collision/, physics.json, semantics.json}` → USD via Isaac's
asset converter + MJCF equivalent. Gate: each USD loads in Isaac Sim headless;
collision hull volume within [0.3×, 1.5×] of visual bbox volume.

**(e) Blank room.** Remove asset gaussians/mesh regions; reconstruct the shell:
floor plane + walls fitted from remaining geometry (RANSAC planes), splat
inpainting only if time allows — a clean planar shell textured from the splat is
enough for the demo. Gate: shell is closed, floor is z-up level.

**(f) Randomize + task gen — the "AI policy" is agent-proposes/harness-disposes.**
An LLM emits *placement priors* ("keyboard on table, in front of monitor,
reachable") and *task candidates* as structured JSON against `task.schema.json`
("move the keyboard next to the PC", success predicate: `dist(keyboard, pc) <
0.15m ∧ on(keyboard, table)`). A deterministic sampler turns priors into poses by
rejection sampling with trimesh collision + support checks (an object must rest
on a surface, nothing interpenetrates, robot base reach check passes). Gate: N
valid scene instantiations generated; every task's success predicate is
machine-checkable (predicates come from a fixed library: `on`, `near`, `inside`,
`grasped`, `pose_within` — the agent composes, never invents).

**(g) RL loop.** Isaac Lab on the Spark: SO-101 arm, GPU-parallel PPO on
**state-based observations** (fast teacher), domain randomization = the scene
sampler from (f) + physics randomization. Gate: success rate ≥ threshold on held-
out randomized scenes. MuJoCo mirror of the same task as the cheap cross-check
(catches Isaac-specific physics artifacts).

**(h) Distill → SmolVLA.** Roll out the teacher with camera rendering in the
splat-textured scene → episodes in **LeRobot dataset v3** (same schema feat 4
emits — this is deliberate: sim rollouts and human AR demos are unioned into one
training set) → fine-tune SmolVLA on the Spark. Gate: fine-tuned policy success
rate in sim ≥ teacher × 0.7 on eval scenes; report the number, never "it works".

**40h cut line inside F3:** (a)–(f) are the demo's heart and must land. If (g)
runs out of time, demo scripted rollouts in the randomized scenes; if (h) does,
show the LeRobot dataset + a fine-tune curve mid-training. Both partial states
are honest and demoable.

---

## 8. Feat 4 — AR/VR training-data games (WebXR)

**Recommendation (as asked): WebXR on Meta Quest 3.** Quest 3 gives hand
tracking + color passthrough through the browser's WebXR API with zero app-store
friction; the same app runs flat on desktop for development. Vision Pro's WebXR
lacks reliable hand-input parity and its native path (RealityKit/Swift) doesn't
fit 40h. Revisit natively post-hack if twinning fidelity (F5) demands RoomPlan.

**The app** (`packages/xr`, Three.js + @react-three/xr) is **AR-first**: the
default session is passthrough AR (`immersive-ar`) — virtual task objects (jar,
lid, button) are anchored onto the player's real table, so they interact with
virtual props in their real space, spectators can watch and coach, and the
headset hands off between people in seconds with no disorientation. VR
(`immersive-vr`, loading a scene.json from F3 or a built-in kitchen as the full
environment) is the secondary mode, used only when the task needs a space that
isn't physically present. Both modes record identically at 30–60 Hz: `{t, head_pose, left_hand:
26 joints, right_hand: 26 joints, object_poses[], events[]}` → streamed to the
hub as Parquet episodes. **Task systems are separate by decision (2026-08-14):**
F4 defines its own game tasks independently of F3's RL tasks. What connects them
is the hub's internal **fine-tune API** — `POST /finetune/datasets` accepts any
LeRobot-v3 dataset (F3 sim rollouts, F4 verified human episodes) and
`POST /finetune/runs` launches SmolVLA fine-tunes over a chosen union of
registered datasets. The predicate library is still shared code (both feats need
machine-checkable success), but task authorship is per-feat.

**Datapipe** (`packages/datapipe`), all deterministic:
1. hand joints → end-effector trajectory: wrist pose → 6-DoF target, pinch
   aperture (thumb–index distance) → gripper command
2. retarget to SO-101: Pinocchio differential IK, joint-limit + velocity clamped
3. **verification gate:** replay retargeted actions in MuJoCo; episode is
   accepted only if end-effector tracking error < 2 cm RMS and the task's
   success predicate still passes in replay. Rejected episodes are logged with
   the failing metric — *bad demos never enter the dataset silently*
4. accepted episodes → LeRobot dataset v3 (`observation.images.*` from replay
   render, `observation.state`, `action`), same schema as F3(h)

**Gate summary:** an episode is training data only after machine-checked replay
success. Human enthusiasm is not a quality signal.

## 9. Feat 5 — AR digital twinning (thinnest slice, by design)

Feat 5 is pure AR — no VR mode at all — and together with F4's passthrough games
it makes **AR the face of Struct's XR story**; VR is a supporting mode, not a
demo centerpiece. The honest 40h scope: **the reconstructed room from F3,
anchored onto the real room in Quest 3 passthrough, with live sim state
overlaid.**

1. Serve the splat (or its mesh proxy — splat rendering in WebXR is still
   costly; use the textured shell + asset GLBs if the splat renderer stutters)
   into the XR app.
2. Alignment v0: user places 2 anchor points (floor corner + table corner);
   similarity transform; drift accepted for demo distances. Alignment v1
   (stretch): WebXR plane detection on Quest 3 → auto floor+wall snap.
3. Twin sync: the hub streams object poses + robot joint state from the running
   Isaac scene over WebSocket; the XR app renders the virtual robot doing the
   trained task *in the real room*. This is the demo's closing shot.
4. Gate: reprojection error at the two anchors < 5 cm; stream latency < 150 ms.

Post-hack direction (recorded, not attempted now): continuous relocalization
against the splat, bidirectional twinning (real object moved → sim updates via
the segmentation pipeline), Vision Pro native path.

---

## 10. 40-hour execution plan (5 humans + agent fleet)

Five tracks, one per feat; each human directs agents within their track. Shared
infra is a joint hour-0–4 job before feat work forks: **F1's owner stands up the
TS workspace + MCP scaffolding; F2's owner stands up compose, `core-py`, hub API,
and the pgvector RAG (docs corpus loaded before Laguna's first agent runs).**

| Track | Owner | Scope |
|---|---|---|
| **F1 — pcb** | human 1 | TS-side infra (H0–4); then F1 40h-scope + pcb MCP server |
| **F2 — cad** | human 2 | Py-side infra + RAG (H0–4); then IR, cad-engine M1–M5 slice, cad MCP server, §6 negotiation loop with F1 |
| **F3 — envrep** | human 3 | pipeline (a)→(h) on the Spark, room-agnostic; owns the fixture scan from hour 1 and the venue scan later as a second test case |
| **F4 — xr data** | human 4 | XR game app + datapipe → `/finetune` API; owns Quest 3 + HTTPS/LAN setup |
| **F5 — twin** | human 5 | splat/mesh serving into XR, anchoring, live sim-state stream; shares the XR app codebase with F4 (coordinate early — same package) |

**Gates (wall clock):**
- **H+4 — interface freeze.** All schemas in `packages/core-py/schemas/` +
  `docs/contracts.md`: board_report, enclosure_report, envelope, Robot IR 1.0,
  asset_bundle, scene.json, task.json + predicate library, LeRobot v3 feature
  spec, XR episode format, MCP tool signatures. After H+4, changing a contract
  requires all affected track owners at the table.
- **H+16 — vertical slices in isolation.** A: a board through L0→L5 via MCP.
  B: `engine.evaluate` scoring an IR; enclosure from a hardcoded board_report.
  C: fixture scan through (a)–(d), assets loading in Isaac. D: XR app records a
  hand episode; datapipe replays one in MuJoCo.
- **H+28 — cross-feat integration.** PCB↔CAD loop converging on the demo board;
  C's scene.json loading in D's XR app; first RL training launched (it trains
  while everyone sleeps — schedule it *before* the night).
- **H+36 — demo freeze.** Cut lines applied (§11), demo script rehearsed once
  end-to-end, all artifacts pinned by hash.
- **H+40 — demo** (§12).

**Agent-fleet rules:** every work order references a contract file + a gate
command that must exit 0; agents work behind branches with the golden fixtures
as CI; no agent merges to a contract file (humans only, post-H+4).

## 11. Cut lines (apply in order when behind)

1. F1: drop KiCad second DRC (keep tscircuit DRC) → demo still truthful
2. F2: drop coverage analysis UI (keep the analysis as CLI output)
3. F3: drop (g) RL → scripted rollouts in randomized scenes (§7 cut line)
4. F5: drop live twin sync → pre-rendered aligned overlay
5. F3: drop splat appearance → LiDAR-mesh-textured scenes only
6. F4: drop MuJoCo replay-render observations → state-only dataset (mark it)
7. Never cut: the deterministic gates, provenance labels, or the H+4 contracts.
   A smaller honest demo beats a bigger fake one.

## 12. Demo script (7 minutes)

1. Type the robot spec → **F1** designs the controller board live (lint→route→
   DRC render timelapse from cached run).
2. **F2** designs the mount; show the §6 negotiation converging (violation →
   envelope → re-place → fit ✓), 3D of board seated in enclosure.
3. Show phone scanning the actual demo room (pre-done) → **F3** segmentation
   view → asset library → blank room → 4 randomized variants → RL success
   curve → SmolVLA fine-tune loss.
4. Hand a judge the **Quest 3 in passthrough AR** (**F4**): they do the task
   with virtual objects on the real demo table — the room stays visible, the
   crowd watches them play, and the headset passes to the next person in
   seconds. Show their episode passing replay verification into the dataset.
   (VR mode exists but stays out of the demo path.)
5. Closing shot (**F5**): passthrough on, the virtual SO-101 performs the
   trained task anchored on the real table.

## 13. Risks

| Risk | Sev | Mitigation |
|---|---|---|
| aarch64 wheel gaps (gsplat, CoACD, Grounded-SAM deps) | **high** | preflight at H+0; fallbacks: Polycam-mesh-only path, VHACD for CoACD, SAM2-base for Grounded-SAM |
| Isaac Lab install/perf on Spark eats a day | high | Use NVIDIA's dgx-spark-playbooks verbatim; MuJoCo is the full fallback sim (cut line 5 texture path still works) |
| COLMAP/splat too slow at the venue | med | Fixture scan pre-trained before the event; venue scan is a re-run, not a first run |
| Quest 3 WebXR needs HTTPS + same LAN | med | mkcert + local CA on the Spark at H+0; phone-hotspot LAN as backup |
| **Spark contention**: Laguna inference + Isaac Lab RL + gsplat + SmolVLA fine-tune all on one GB10 | **high** | Schedule, don't share: Laguna serves during build hours (agent fleet), heavy training owns the GPU overnight (H+28 RL launch); cap vLLM `gpu-memory-utilization`; Claude API absorbs agent load during training windows |
| Laguna structured-output/tool-call reliability below agent-loop needs | med | Preflight schema round-trip test; per-agent provider config falls back to Claude for designer/chief-class agents with zero code change |
| SmolVLA fine-tune quality in 1 night | med | ACT fallback (smaller, trains faster); cut line 3 |
| Splat segmentation is mushy | med | LiDAR-mesh bbox segmentation is the floor; splat grouping is the ceiling |
| PCB↔CAD loop oscillates | low | 3-round hard stop + report; both check_fits deterministic |
| Contract churn after H+4 | med | freeze rule in §10; contracts are files with owners |

## 14. Open questions — resolved 2026-08-14 (remaining items are H+0 tests)

All eight originals are answered and folded into §0's decisions table: 5 humans
one-per-feat; board fab ordered post-hack; F3 room-agnostic by requirement;
Laguna primary with docs-RAG + model-agnostic Claude fallback; F3/F4 task
systems separate, joined by the internal fine-tune API; LeRobot official SO-101
URDF; pgvector on shared Postgres for RAG; feature list finalized — all five
feats ship, hardening continues post-hack across all of them.

Still open, but they're **tests at H+0**, not decisions:

1. **Splat rendering in WebXR** (F5): try a gsplat web renderer for 30 min; if
   <45 fps on Quest 3, commit to the mesh-proxy path immediately.
2. **Laguna structured-output reliability**: the preflight schema round-trip
   decides per-agent Laguna vs Claude assignment empirically — both sub-plans'
   prediction-scoring machinery runs from hour 1 to keep that assignment honest.

---

*Sub-plans: `text-to-pcb-plan.md` (stage ladder, lint rules, tool audit) and
`text-to-cad-plan.md` (Robot IR, criteria/coverage, provenance, milestones) are
saved alongside this document in the project. Their principles are normative for
the whole of Struct.*
