# Resume state — 2026-08-15

## Where the build is

**The full loop is wired and live-tested end-to-end**: xr-web client →
Episodes API → ar_datapipe (retarget/verify/export) → verdict, plus a
physics-driven live Twin stream and a Corrections endpoint. Branches
`feat/ar-contracts`, `feat/ar-datapipe`, `feat/ar-backend` merged into
`feat/arvr-integration`; `feat/ar-web-port` new, pending merge — 69/69
Python tests green on Linux (WSL x86_64 verified, Spark aarch64 pending
this branch's turn), 32 passed + 5 skipped on Windows (as designed), 30/30
vitest tests + typecheck clean for the TS client.

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
- Follow session/WS endpoints not wired into `ar_backend` (section 39) —
  `FollowSession` is ready, this is a small increment.

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

1. Validate `feat/ar-web-port` on the real Spark (worktree), then merge
   into `feat/arvr-integration`.
2. Wire `ar_datapipe`'s accepted-episode output into `ar_sim`'s director
   so REPLAY drives a real retargeted demonstration, not a fixed routine.
3. Follow session/WS endpoint in `ar_backend` (section 39).
4. Do NOT run the Isaac Sim container without a deliberate decision to
   spend the GPU budget.
5. Real robot URDF, whenever F3/hardware has one — two placeholders
   (`ar_datapipe`'s and `ar_sim`'s) both need swapping and probably
   unifying at that point.
