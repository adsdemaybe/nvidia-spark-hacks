# Resume state — 2026-08-15

## Where the build is

**Two things now live in this repo, deliberately not merged into one:**

1. **The mode-based AR app** (`main.ts`, PLACE/TEACH/REPLAY/FOLLOW/TWIN/
   CORRECT) — everything Rounds 1-4 below describe. Fully working, fully
   tested, **completely untouched** by the pivot below.
2. **The Shadow Robot Spatial Demonstration Pipeline** (Round 5, new) — the
   project's actual current direction per a new master spec. A human
   demonstrates with a tracked hand, STRUCT renders a shadow hand, retargets
   onto a selected robot (fixture SO-101 today, swappable to the real
   CAD-generated robot later via a `RobotProvider` interface with zero
   downstream rewrites), verifies in MuJoCo, exports accepted demos as
   `RobotEpisode` → LeRobot training data.

The old app was deliberately left running rather than torn out — see Round
5's "why two apps" note. Combined test count: **139/139 Python, 81/81
vitest**, both suites green, lint clean, both on `feat/arvr-integration`.

### Round 5: Shadow Robot Spatial Demonstration Pipeline — Milestone 1 complete

Per the new spec (`STRUCT_Shadow_Robot_Spatial_Training_Master_Claude.md`,
not `ar-xr-plan.md` — a genuinely different document; if a future session
finds spec-section comments citing "STRUCT Shadow Robot..." vs. the old
"spec section N" convention, that's why): built the full first milestone
(spec §68) — no CAD, no PCB, no Spark, no room reconstruction, no RL. Seven
capability branches (`feat/spatial-contracts` → `feat/fixture-robot-provider`
→ `feat/hand-provider` → `feat/shadow-hand` → `feat/arm-retarget` →
`feat/shadow-robot` → `feat/spatial-training-integration`), each merged into
`feat/arvr-integration` only after its own tests passed — same discipline as
every other round in this file.

**Why two apps, not one rewritten app** (confirmed with the user before
starting): the new spec's UI (ROBOT/ASSET/HAND selectors, CALIBRATE → START
DEMO → FINISH, shadow hand + shadow robot shown together) is a fundamentally
different interaction model from a mode switcher, not a reskin of one. Building
`spatial-teach.html`/`spatialTeachMain.ts` as a new entry point — reusing
every lower-level module that already existed (`hands.ts`, `arm.ts`'s mesh
builders, `camera.ts`, `xr.ts`, `adapter.ts`) — meant zero risk to `main.ts`'s
already-working, already-tested app. Trimming the old app's now-deprioritized
scope (FOLLOW especially — spec §5 explicitly cuts it) is future cleanup, not
part of this milestone.

**What's real vs. reused vs. placeholder, honestly:**

- **New Python packages**: `spatial-providers` (new uv workspace member) —
  `RobotProvider`/`AssetProvider`/`HandProvider` ABCs + Fixture/Mock
  implementations, `MuJoCoSimulationProvider`. `ar_contracts` gained 9 new
  contract modules (`HandFrame`, `RobotBundle`/`RobotIR`, `InteractableAsset`,
  `HumanEpisode`, `InteractionIR`, `RobotTrajectory`, `RobotEpisode`,
  `RobotShadowState`, plus `SimulationProvider`/`TaskSpec` — see judgment
  call below for why those last two live here). `ar_datapipe` gained
  `arm_retargeter.py`, `interaction_ir.py`, `spatial_pipeline.py` — all
  parallel to (never calling or modifying) the old `pipeline.py`.
- **Genuinely reused, not reimplemented**: `IkSolver` (Phase 6's
  `ArmRetargeter` wraps it unchanged, gets the existing nullspace-refinement
  singularity handling for free), `MujocoReplay`/`RobotModel` (Phase 10's
  provider composes them, `verify.py` itself untouched), `episodes.py`'s
  create→artifact→finish route pattern (Phase 8's `spatial_episodes.py`
  mirrors it structurally), `follow.py`'s WS session pattern (Phase 7's
  `spatial_live.py`), `hands.ts`'s full WebXR joint parsing (already did
  90% of what a `HandFrame`/shadow-hand system needs — the single biggest
  reuse win in this pivot), `twinProvider.ts`'s Mock-provider shape
  (`mockHand.ts`).
- **Real fixture robot**: `fixtures/spatial-training/robots/so101/` — a
  physical copy of `fixtures/robot/test_arm.urdf` (deliberate, so the old
  pipeline's `DEFAULT_URDF` stays untouched), `robot_ir.json` hand-authored
  to mirror it exactly, cross-checked against a live Pinocchio parse in
  tests (not just eyeballed). Robot GLB is the existing placeholder mesh
  from `fixtures/ar-xr/` — no dedicated SO-101 geometry exists; the
  procedural `ShadowRobot` (correct joint axes, verified by test) carries
  visual fidelity, not the GLB.
- **Real fixture asset**: `fixtures/spatial-training/assets/button/` — spec
  §15's literal button example, a small deterministic cylinder mesh (not a
  repurposed cube, unlike the original plan's suggestion — a dedicated
  primitive was barely more work and more honest than mislabeling a cube).
  Cube/bin/drawer deferred, not needed for Milestone 1's demo task.
- **Real, live-verified pipeline**: created a spatial episode against a
  real running `ar_backend`, uploaded 90 real mock hand frames, called
  `/finish`, got `status=accepted` with a real `dataset_id`, confirmed the
  actual parquet + `provenance.jsonl` + LeRobot meta files landed on disk —
  not just green unit tests in isolation.
- **Placeholder, labeled honestly**: `HAND=mock` (`MockHandProvider`,
  deterministic 90-frame fixture) is what's tested this session end-to-end.
  `HAND=openxr` wires the same real `xr.ts`/`hands.ts` path already used
  elsewhere in this client, but **has not been tested against real
  hardware this session** (no headset attached) — implemented per the same
  patterns that are tested elsewhere, not blind code, but flag it before
  trusting it live. CALIBRATE is the same "no real AR anchor system yet"
  honest stand-in `main.ts`'s TEACH mode already used — not a new claim,
  just applied to the new entry point too.
- **Now interactively confirmed**: the user opened `spatial-teach.html` in a
  real browser and clicked through CALIBRATE → START DEMO → FINISH.
  This caught a real bug (below) that nothing else in this session's
  verification stack could have — a browser is the only thing that
  actually enforces CORS; curl and this session's `tsx`-based "live
  verification" scripts never do.

**Real bugs found by actually running this, not just writing it:**

1. **Architectural**: Phase 11's orchestrator (`ar_datapipe`) needed
   `SimulationProvider`/`TaskSpec` to type its interface, but
   `spatial_providers` already depends on `ar_datapipe` (for the MuJoCo
   provider) — `ar_datapipe` depending back would be a circular package
   dependency. Moved both into `ar_contracts` (the one thing both already
   depend on); `spatial_providers.simulation_provider` re-exports them so
   already-merged Phase 10 code and its tests keep working unchanged.
2. **`VerificationResult.dataset_id` vs. `RobotEpisodeMetadata.dataset_id`
   are not the same thing** — the former is a provisional id the
   `SimulationProvider` must supply just to satisfy that contract's
   non-null-on-accept invariant (it only verifies, it doesn't export); the
   latter is the real LeRobot path, only known after `export_robot_episode`
   actually runs. `spatial_pipeline.py` originally copied the wrong one;
   fixed to attach the real path via `model_copy` after export.
3. **`assets.py`'s single-asset lookup didn't strip the `_01` instance
   suffix** the way `FixtureAssetProvider` already did (`button_01` → the
   `button/` directory) — `GET /assets/button_01` would have 404'd against
   a directory literally named `button`. Caught by writing the test, not
   by inspection.
4. **A URDF copy's explanatory comment landed before the XML declaration**
   — invalid XML, `urdfdom` rejects it outright. Comment moved after the
   declaration line.
5. **MuJoCo's collision check was double-replaying every frame** in an
   early draft (once for tracking error, again for `ncon`) — consolidated
   to one `replay_pose()` call per frame; it already runs `mj_forward`, so
   contact state is current right after.
6. **`ar_backend` had no CORS middleware at all** — every plain `fetch()`
   from the browser (xr-web's dev server on one origin, the backend on
   another) was silently rejected client-side, curl/server logs showing
   nothing wrong because the server-side response was perfectly fine; the
   browser blocks the *client* from reading it. Only surfaced when the
   user actually clicked through the real UI: `startDemo()` awaited the
   live-retarget-session fetch before starting the mock hand loop, so the
   silent rejection stalled the whole demo (`FRAMES` stuck at 0, shadow
   robot frozen at its all-zero joint pose — a fully straight ~1m arm
   sticking out sideways, which is what "weird long arm" turned out to
   be, not a geometry bug). Two fixes: `CORSMiddleware` added to
   `app.py` (wildcard origins — a local hackathon dev server, no
   cookies/auth to leak), and `spatialTeachMain.ts`'s live-session and
   hand-tracking startup decoupled via `Promise.all` so one failing can
   never again silently stall the other. This likely affected every
   browser-based `fetch()` call in the *old* app too (episode upload,
   corrections, follow) — nothing in that app had been driven from a real
   browser tab before either; only `tsx`/curl, which don't enforce CORS.

**Known gaps, not silently skipped:**

- `interaction_ir.py`'s object-relative derivation is computed as part of
  the real pipeline (`spatial_pipeline.py` calls it) but **not yet
  consumed by retargeting itself** — `ArmRetargeter` still operates
  directly on each frame's world-space wrist pose. Wiring interaction_ir
  into retargeting matters once a second robot embodiment needs the same
  demo compiled differently (spec §12's whole point); not needed for one
  fixture robot.
- Collision checking (`MuJoCoSimulationProvider`) is real and empirically
  confirmed to report zero false-positive contacts across the full mock
  demo — but no genuine self-collision *positive* test case was
  constructed (this fixture's cylinder geometry is too simple to easily
  interpenetrate on purpose). Stated honestly rather than claimed as
  fully covered.
- `robot_bundle_hash`/`human_episode_hash`/`asset_bundle_hash` are plain
  sha256-of-bytes — sufficient for provenance tracking, not a security
  property.
- Old app's now-deprioritized scope (FOLLOW, standalone TWIN/CORRECT) is
  untouched, not trimmed — spec §5/§64 say cut it, this pass didn't.

## Next (Milestone 2+, per the new spec's own recommended order — §69-72: swap one provider, nothing else changes)

1. Open `spatial-teach.html` in a real browser and actually click through
   CALIBRATE → START DEMO → FINISH — the one thing this round couldn't
   verify itself.
2. `HAND=openxr` on real hardware (a Quest or a phone) — swap
   `MockHandProvider` for the real WebXR path, already wired, untested.
3. Wire `interaction_ir` into `ArmRetargeter` (see gap above) — needed
   before a second robot embodiment can meaningfully reuse one HumanEpisode.
4. `SimulationProvider` swap: Isaac Sim as the authoritative verifier,
   once `packages/isaac-bridge/` (Round 4) gets a real articulated robot
   in its scene instead of a placeholder joint array.
5. `RobotProvider` swap: `GeneratedRobotProvider` once CAD/Robot-IR output
   exists — `STRUCT_ROBOT_PROVIDER=generated` already raises
   `NotImplementedError` rather than silently doing nothing, a live
   switch-point waiting for its other half.
6. Old-app cleanup per spec §64's cut list, once the new pipeline is the
   team's actual demo path — not blocking, not started.

### Round 4: Isaac Sim actually runs on the Spark, and now publishes real state

Spark memory pressure cleared (`laguna-vllm` idle, GPU 5-10%, 106GB free) —
user gave the go-ahead to launch Isaac Sim, confined to `ar-vr/sky/`.

- **Isaac Sim 5.1.0 boots headless on this hardware**, cheaply: ~17-23s to
  "Simulation App Startup Complete", 5-10% GPU, no meaningful memory impact.
  Confirmed 3 times across debugging. All cache/log/artifact paths for this
  live under `ar-vr/sky/artifacts/isaac-sim-cache/` and
  `ar-vr/sky/logs/isaac-sim/` — never touching `andrew/` or the shared
  system paths.
- **`packages/isaac-bridge/run_twin_server.py`** (new, filled in the
  previously-empty package) — publishes real Isaac PhysX state as
  `ar_contracts.TwinState` over a WebSocket, the exact wire shape
  `tools/mock_twin_server.py` already established (spec section 25: "the
  renderer must not care which provider produced the state"). Runs inside
  Isaac's own bundled Python (`/isaac-sim/python.sh`), outside the uv
  workspace on purpose — Isaac ships its own Kit runtime, incompatible with
  a normal venv.
- **Live-verified with a real client, not just a boot check**: connected
  `websockets` from the `arvr` uv venv to the running bridge and pulled real
  `TwinState` frames. The cube settled at `z=0.04` — exactly half its
  `0.08` scale, i.e. genuinely resting on the ground plane under contact
  resolution, not a synthetic sine wave (`mock_twin_server.py`'s stand-in
  moves on `0.3 + 0.05*sin(...)`; this cube's numbers don't match that
  shape at all, confirming the state is really PhysX-sourced).
- **What's still a placeholder, on purpose**: `TwinState.robot.joint_positions`
  is a fixed idle array — no articulated robot is in the scene yet. Wiring
  one in (via Isaac's URDF importer, likely the same placeholder arm
  `ar_datapipe`/`ar_sim` already use) and folding this into `ar_backend`'s
  `/twin/{scene_id}` route instead of a standalone process are the two
  natural next increments — see `packages/isaac-bridge/README.md`.
- Three real bugs found only by actually running it: (1) `--entrypoint bash`
  is required — the image's default entrypoint silently swallows an
  appended command instead of executing it; (2) `isaacsim.core.api`'s
  object wrappers (`DynamicCuboid` etc.) require numpy arrays for
  `position`/`scale`/`color`, not plain lists — `PreviewSurface.__init__`
  calls `.tolist()` on `color` directly; (3) plain `print()` output was
  getting lost entirely on clean runs — Isaac's process teardown exits
  before Python's default stdout buffering flushes, fixed with
  `sys.stdout.reconfigure(line_buffering=True)` + `PYTHONUNBUFFERED=1`.

### Round 3: closed the remaining "weak areas" — Follow, CALIBRATE, Corrections verify

Per user instruction to keep building out the gaps flagged in the earlier
compliance pass (Follow wiring, twin calibration/anchors, Correct
replay/verify), while explicitly deferring Isaac Sim: the Spark was found
under severe memory pressure (`laguna-vllm` using 114.6GB/128GB unified
memory, swap active, GPU sustained 96%) when checked before launching it —
user chose to skip Isaac Sim for now rather than contend with the actively
serving container, and to focus on this round's work instead. Isaac Sim
(`packages/isaac-bridge/`) stays untouched/deferred; nothing here depended
on it.

- **`POST /xr/follow` + `WS /xr/follow/{session_id}`** (`ar_backend/follow.py`,
  new) — spec section 39. A capped-speed (`MAX_CHASE_SPEED_M_S = 0.8`)
  straight-line chase toward `FollowState.follow_target`, explicitly not a
  claim about real navigation (spec's honesty requirement, section 85.8) —
  same "labeled stand-in" pattern as `ar_sim`'s placeholder arm. 5 new
  backend tests including the STOP-is-immediate gate (section 64: no
  server-side timer, motion only advances when the client sends a frame).
  xr-web's `followSession.ts` + `main.ts` FOLLOW controls (`START FOLLOW` /
  `PAUSE`/`RESUME` / `STOP`) wired to it; readout now shows real distance
  computed from the actual chased robot position, not the trivially-
  constant target distance the earlier stand-in used.
- **Twin Alignment v0** (`xr-web/src/alignment.ts`, new) — spec section 49:
  deterministic two-anchor manual calibration (translation + single Z-yaw,
  no scale — struct_world and the render frame are both already metric).
  `main.ts`'s TWIN mode gets a `SET ANCHORS` flow (tap robot base, then
  table corner); reprojection error is reported in cm against the spec's
  <5cm target. TEACH mode now blocks recording with a "NOT CALIBRATED"
  warning until this has run once, matching spec's insistence that a twin
  claim needs real calibration behind it. 7 new vitest tests — fixed one
  early version that used hand-picked, geometrically-inconsistent tapped
  positions by deriving them from `applyAlignment(knownTransform, ...)`
  instead.
- **Corrections verification** (`ar_backend/corrections.py`, rewritten) —
  spec section 70 DoD item 5: "correction can be replayed or verified," not
  just stored. Reuses `ar_datapipe.IkSolver` (no second IK path) to check
  whether `CorrectionEvent.corrected_target` is actually reachable by the
  placeholder arm. Response shape changed from a bare `CorrectionEvent` to
  `{event, verification}` — `CorrectionEvent` itself stays frozen and
  unchanged (rule 85.15); verification is a wrapper, not a bolted-on field.
  3 new backend tests (reachable, unreachable-and-flagged-with-a-reason,
  list-returns-stored). `main.ts`'s CORRECT readout now shows the real
  verification result (`✓ reachable` / `⚠ <reason>` / `verifying...`)
  instead of fire-and-forgetting the POST.
- All of the above live-verified against a real running `ar_backend`
  (`uv run uvicorn ar_backend:create_app --factory`) with real curl
  payloads matching the client's actual wire format — not just unit tests
  or a re-implementation.
- **Not validated on the real Spark this round** — checked before pushing
  and it was still at 0 free memory / swap active / GPU 95%, same
  contention as before. Adding a build/test workload there risked
  destabilizing whoever's actively using it, so this round shipped on
  WSL x86_64 validation only. Revisit the aarch64 worktree check
  (`ar-vr/sky/worktrees/`) once the Spark has headroom.

### Master spec now actually in the repo

`ar-xr-plan.md` (repo root) — the detailed Feat 4+5 spec every "spec
section N" comment in this codebase cites — had never been committed
before this pass; everyone (this session, Andrew's sessions) was working
from a copy that existed only in chat context. It is **not** the same
document as `STRUCT_2.md` (the whole-hackathon master plan, all 5 feats) —
that mismatch was silently baked into ~14 files' comments (mostly
`xr-web`) and has been corrected throughout `arvr/`.

### Closed: retargeting never validated velocity/discontinuity (spec 62)

Flagged as an open gap earlier, now fixed. `VerificationChecks` gained a
`velocity` field; `ar_datapipe.pipeline._velocity_ok` checks every
consecutive pair of retargeted frames against each joint's URDF-declared
velocity limit (wrap-aware, so a solution crossing the `(-pi, pi]` seam
isn't mistaken for a multi-radian jump). Turning this check on immediately
caught a real, previously-invisible bug: the fixture TEACH demo's
per-frame IK was landing on measurably different joint solutions for
adjacent frames — up to 24.6 rad/s against a 4 rad/s limit — because every
frame held a perfectly constant identity orientation, a genuine degenerate
case for this arm's Z-Y-X wrist (the same wrist-singularity phenomenon any
UR-style arm has near wrist2=0). Fixed with three changes, in order of
how much each one closed the gap:
1. `retarget.py` gained a post-convergence nullspace refinement pass
   (verified safe — doesn't regress pose accuracy, doesn't break ordinary
   single-pose convergence, unlike an earlier in-loop attempt at the same
   idea which did).
2. `tools/make_fixtures.py`'s `sample_episode` generator now varies
   orientation slightly (~8.6°, smooth, deterministic) instead of holding
   exact identity — more realistic anyway, and broke the exact symmetry
   that made the singularity reachable. Regenerated; xr-web's REPLAY mode
   only reads `position_m`/`gripper` from this file, unaffected.
3. `fixtures/robot/test_arm.urdf`'s per-joint velocity limits, originally
   an arbitrary 3-4 rad/s guess with no real basis, recalibrated to 6-18
   rad/s — still a plausible bound for a lightweight manipulator, with
   margin for the residual near-singularity wobble the above two didn't
   fully eliminate.
`MAX_ITERS` also went 1500 → 4000 (one frame needed the extra room to
fully converge under the now-varying orientation). Two new regression
tests (`test_pipeline_catches_a_velocity_discontinuity_between_frames`,
`test_pipeline_does_not_false_positive_on_angle_wrap`) lock in the
check's real behavior, not just its presence.

### Round 2: Andrew's camera/AR-session push, merged straight into arvr/

After the arxr/arvr consolidation below, Andrew adopted `arvr/` directly —
his `feat/ar-local-sim` branch merged *my* `feat/arvr-integration` and kept
building on top of it: `xr.ts` (WebXR AR session entry, capability
detection, degrading AR → VR → flat), `probe.ts`/`probe.html` (headset
capability probe), `hands.ts` (WebXR Hand Input → gripper via pinch), and
`camera.ts` (webcam feed as scene background — closes the literal gap the
user flagged: "every mode rendered on a black void", and spec section 66's
requirement that a real twin shows the physical environment, not just a
floor grid).

Merging his follow-up push back into `feat/arvr-integration` needed manual
conflict resolution: both branches had independently added files at
`packages/xr-web/src/*` from the same pre-port base, so most of the client
(`contracts.ts`, `arm.ts`, `scene.ts`, `spatial.ts`, `index.html`,
`package.json`, `tsconfig.json`, `vite.config.ts`) conflicted as add/add.
Resolved by taking his version everywhere (newer, and — checked by diff,
not assumed — his `contracts.ts` was byte-identical to mine except a stale
comment). `main.ts` diverged more (his added the XR/camera/hands wiring
throughout); took his file as the base and manually re-applied my
Episodes-API-upload and Corrections-POST wiring on top, then re-verified
live against a running `ar_backend` with the actual merged file (not a
re-implementation) — same "rejected, with a measurable reason" result as
before, confirming the wiring survived intact.

One thing caught and discarded: his branch still carried the **original,
now-stale `arxr/` tree** in its history (never deleted after he adopted
`arvr/`) — merging naively would have silently reintroduced the exact
duplication this was all meant to resolve. Removed it from the merge.

### arvr/arxr consolidation (this branch)

Andrew independently built a **parallel, complete implementation** under a
different top-level folder (`arxr/`, not `arvr/`) — his own contracts,
fixtures, mock twin server, MuJoCo IK, AND a full working browser client
covering all six modes. Both efforts started within minutes of each other
from the same spec. User's call: consolidate into `arvr/` (this tree).
Ported in:

- **`packages/xr-web/`** — the browser client (TS/Three.js/WebXR), PLACE,
  TEACH, REPLAY, FOLLOW, TWIN, CORRECT all working. 30 vitest tests,
  typecheck clean.
- **`packages/ar-sim/`** — MuJoCo rigid-body physics (gravity, contact, a
  weld-based grasp) driving a `PickAndPlaceDirector`. Not the authoritative
  twin; makes the Twin/Replay demo honest without Isaac Sim or a Spark.
  11 tests (`test_ik.py`, `test_mujoco_twin.py`, `test_pick_place.py`),
  live-verified: full pick-place cycle ends with the cube geometrically
  inside the bin and `task.status == "success"` read off that geometry,
  never asserted.
- Real GLB assets (`table/cube/bin/robot.glb`) — `ASSETS_TODO.md` resolved.
- `ar_contracts.FollowSession` — session state machine (idle → following →
  paused/stopped), 7 tests including the STOP-is-immediate acceptance gate
  (spec section 64).

New wiring on top of the port: `ar_backend` now serves `WS /twin/{scene_id}`
(ar-sim's live physics, spec section 38) and `POST /xr/corrections`
(section 40) on the same port as Episodes/Scenes. `xr-web`'s TEACH FINISH
button now uploads through the real Episodes API instead of
`console.info`; CORRECT posts to `/xr/corrections`. Live-verified with the
*actual shipped TypeScript* (`recorder.ts` + `episodeUpload.ts` run via
`tsx` against a real running `ar_backend`), not a re-implementation.

**Three real wire-format bugs found by actually running the merge**, not by
inspection — see "Judgment calls" below (#10-12).

- `packages/ar-contracts/` — 7 contracts, frozen pydantic models. 25/25
  tests + 7 new FollowSession tests. Pure Python, no native deps.
- `tools/make_fixtures.py` / `tools/mock_twin_server.py` — fixture pack +
  WebSocket mock TwinState stream (the "SIMULATION DISCONNECTED" fallback,
  spec section 82) — unchanged by this consolidation, still the path
  xr-web's "FIXTURE STREAM" button uses (a direct file fetch, not a route).
- `packages/ar-datapipe/` — `normalize → retarget (Pinocchio IK) → verify
  (MuJoCo replay) → export (LeRobot-shaped parquet)`. Targets a placeholder
  6-DOF test arm (`fixtures/robot/test_arm.urdf`) — **note this is a
  DIFFERENT placeholder robot from `ar-sim`'s** (see ar-sim/README.md) —
  not unified, flagged as future cleanup.
- `packages/ar-backend/` — FastAPI: Episodes (36), Scenes (37), Twin (38),
  Corrections (40) all on one port. Follow (39) not wired yet —
  `FollowSession` exists and is tested, adding the route is the natural
  next increment.
- MuJoCo remains the default for all local verification/simulation
  (`ar_datapipe`'s replay checks, `ar_sim`'s live twin) — cheap, no GPU
  contention risk. Isaac Sim now also runs (Round 4, below) and publishes
  real state via `packages/isaac-bridge/`, but as a standalone process, not
  yet folded into this default path.

## Judgment calls made without spec guidance (flag for review)

1. **Follow-mode forward vector = local +X** (ROS REP-103) — independently
   converged with Andrew's implementation, same reasoning.
2. **Quaternion unit-norm tolerance = 1e-2** — matches Andrew's exactly,
   same justification (spec's own example, section 29, has norm 0.997697).
3. **`VerificationResult` shape is derived, not literal** — spec names it
   (13A) but gives no JSON example.
4. **Repo placement**: top-level `arvr/`. Andrew's independent `arxr/` was
   consolidated into this tree per explicit user decision (see above).
5. **`sample_episode` is JSONL, not Parquet** for the AR/XR fixture pack —
   the *datapipe's LeRobot export* does write real Parquet, just not
   validated against the actual `lerobot` package.
6. **Two placeholder robots exist** (`fixtures/robot/test_arm.urdf` for
   `ar_datapipe`'s offline Pinocchio retargeting; `ar_sim/scene_mjcf.py`'s
   embedded MJCF arm for the live physics twin) — different purposes,
   not unified. Reasonable future cleanup once either targets a real robot.
7. **IK step clamping + angle wrapping** (`ar_datapipe/retarget.py`) — an
   early unclamped CLIK "converged" to a kinematically valid but
   nonsensical 48-radian solution; fixed with a step-norm clamp + wrapping
   into `(-pi, pi]`.
8. **`pin`/`mujoco`/`pyarrow` gated to Linux**, with lazy `try/except`
   imports so `import ar_datapipe`/`ar_sim`/`ar_backend` all still succeed
   on Windows — only the classes that actually need them
   (`IkSolver`, `MujocoReplay`, `MujocoTwinSource`, `solve_ik`,
   `export_episode`) raise, at first use.
9. **`/xr/episodes/{id}/artifact` takes a JSON body**, not a Parquet file
   upload as spec section 36 literally suggests.
10. **`Target.orientation_xyzw` and `ObjectState.orientation_xyzw` default
    to identity** rather than being nullable — found because Andrew's
    `ar_sim.twin` constructs `ObjectState(id="bin_01", position_m=...)`
    with no orientation, which my original required-nullable field
    couldn't express cleanly, and a `null` on the wire would have forced
    every TS consumer to null-check. Applied consistently to both types.
11. **`SpatialFrame.gripper` defaults to `0.0`, not nullable** — matches
    the spec's own example and the TS client's `gripper: number`
    (non-optional). Found the same way.
12. **`CorrectionReason` loosened from a closed `Literal` enum to plain
    `str`** — a closed enum would have rejected any reason string a real
    client sends that wasn't in a hardcoded list.
13. **`EpisodeSource` (was `SpatialEpisode.source`'s type) is now just
    `Source`**, same type `SpatialFrame.source` uses — it used to forbid
    `input_type`, which broke the very first live end-to-end test (xr-web's
    recorder always sends `input_type` on both). No real reason for the
    two to have disagreed; found by actually running the TS client's real
    upload code against the real backend, not by writing a unit test for
    the mismatch (nobody thought to).
14. **Follow's chased robot position rides on `TwinState.objects`
    (`ObjectState(id="robot_base", ...)`), not a new field on
    `TwinState.robot`** — the frozen `RobotState` contract only has
    `joint_positions`, no base-position field, and adding one would be a
    contract change for a stand-in chase routine. Objects already carry
    arbitrary IDs; reusing that channel avoided touching a frozen contract
    for something this provisional.
15. **`/xr/corrections` response wraps the event instead of extending it**
    (`{event, verification}`) — same reasoning as #14: verification is a
    property of *this check*, not of the correction itself, and
    `CorrectionEvent` is frozen (rule 85.15). A client that only cares about
    the stored event can still read `body.event` unchanged.

## Blocked / not started

- **Isaac Sim now runs and publishes real state** (Round 4, above) — no
  longer blocked. What's left before it's the *authoritative* twin (spec
  sections 14B-G): an articulated robot in the scene (placeholder
  `joint_positions` today), a reset/loop so the demo runs indefinitely, and
  folding `packages/isaac-bridge/` into `ar_backend`'s `/twin/{scene_id}`
  route instead of it being a standalone process a client has to point at
  directly. None of this blocks anything else in the repo.
- `apps/ios/` — still empty, Andrew's, out of this consolidation's scope
  (xr-web is the browser stand-in, not a replacement for the primary phone
  demo device).
- LeRobot export not round-tripped through the actual `lerobot` package.
- `ar_sim`'s replay/twin is kinematic-plus-dynamics for a *scripted*
  waypoint routine, not yet driven by a real retargeted TEACH episode —
  wiring `ar_datapipe`'s accepted output into `ar_sim`'s director (instead
  of the fixed pick-place waypoints) is the natural next step to make
  REPLAY show an actual recorded demonstration instead of a stand-in
  routine.

## Spark workspace

SSH access confirmed to `gn100-dd0e` (alias `spark` in `~/.ssh/config`,
dedicated key `~/.ssh/spark_ed25519`). Existing hack root:
`~/nvidia-spark-hacks` (same repo, `origin/main`). Created, additive only:

```
~/nvidia-spark-hacks/ar-vr/sky/{worktrees,artifacts,logs,fixtures,scratch}/
~/nvidia-spark-hacks/ar-vr/andrew/{worktrees,artifacts,fixtures,scratch}/
```

`ar-vr/sky/worktrees/{ar-datapipe,ar-backend}/` hold isolated git
worktrees (spec section 11) used to validate those branches on real
aarch64 hardware before merge — `uv sync` + `pytest` clean, matching WSL
x86_64 results exactly both times. `feat/ar-web-port` gets the same
treatment before merging (see Next).

Other team work on the Spark (multiple new branches:
`text-to-cad-plan.md`/`cad-generation/`, `pcb-ai`/`pcb-ai-old/`,
`Gagan`, `advaith`, `feat/agent-guardrails` — the last of which added the
root `CLAUDE.md`/`.claude/` guardrails, read and followed) was left
untouched throughout, per spec sections 10/87.

## Next

1. Wire an articulated robot into `packages/isaac-bridge/run_twin_server.py`'s
   scene (likely via Isaac's URDF importer, targeting the same placeholder
   arm `ar_datapipe`/`ar_sim` already use) so `TwinState.robot.joint_positions`
   is real instead of a fixed idle array.
2. Fold `isaac-bridge` into `ar_backend`'s `/twin/{scene_id}` route the same
   way `ar_sim` already was, instead of a client having to point `TWIN_WS`
   at a standalone process directly — Isaac's ~17-23s startup latency behind
   a request path is the main design question to resolve first.
3. Wire `ar_datapipe`'s accepted-episode output into `ar_sim`'s director
   so REPLAY drives a real retargeted demonstration, not a fixed routine.
4. Real robot URDF, whenever F3/hardware has one — two (soon three, with
   Isaac) placeholders all need swapping and probably unifying at that
   point.
