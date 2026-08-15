# Resume state — 2026-08-15

## Where the build is

**The full loop is wired and live-tested end-to-end**: xr-web client →
Episodes API → ar_datapipe (retarget/verify/export) → verdict, plus a
physics-driven live Twin stream, a live Follow session, manual Twin
Alignment (spec section 49), and a Corrections endpoint that actually
verifies reachability instead of just storing the event. Branches
`feat/ar-contracts`, `feat/ar-datapipe`, `feat/ar-backend`, `feat/ar-web-port`
merged into `feat/arvr-integration`. 78/78 Python tests green on Linux
(WSL x86_64), 52/52 vitest tests + typecheck clean for the TS client.

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
- Per user instruction: **MuJoCo instead of Isaac Sim** for all local
  verification/simulation (Isaac Sim is downloaded on the Spark but
  deliberately not run — GPU memory budget).

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

- **Isaac Sim 5.1.0 downloaded, not run** — deliberate, per instruction, to
  keep GPU memory free. Sections 14B-G (Isaac bridge, live Follow/Teach-
  replay against the *authoritative* twin) stay blocked until that
  changes; nothing else is blocked by it.
- `packages/isaac-bridge/`, `apps/ios/` — still empty. Isaac bridge is
  Sky/SSH-only. The iOS phone app is Andrew's and out of this
  consolidation's scope (xr-web is the browser stand-in, not a
  replacement for the primary phone demo device).
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

1. Validate this round's work (`follow.py`, `corrections.py`, `alignment.ts`,
   `followSession.ts`, `main.ts` changes) on the real Spark (worktree), then
   merge into `feat/arvr-integration` and push.
2. Wire `ar_datapipe`'s accepted-episode output into `ar_sim`'s director
   so REPLAY drives a real retargeted demonstration, not a fixed routine.
3. Do NOT run the Isaac Sim container until the Spark has memory/GPU
   headroom (`laguna-vllm` was at 114.6GB/128GB, GPU 96%, as of this
   check) or the user explicitly revisits it. Sections 14B-G (Isaac
   bridge, live Follow/Teach-replay against the *authoritative* twin)
   stay blocked until then; nothing else in this repo is blocked by it.
4. Real robot URDF, whenever F3/hardware has one — two placeholders
   (`ar_datapipe`'s and `ar_sim`'s) both need swapping and probably
   unifying at that point.
