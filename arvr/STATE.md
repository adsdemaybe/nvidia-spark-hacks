# Resume state — 2026-08-15

## Where the build is

**Two things now live in this repo, deliberately not merged into one:**

1. **The mode-based AR app** (`main.ts`, PLACE/TEACH/REPLAY/FOLLOW/TWIN/
   CORRECT) — everything Rounds 1-4 below describe. Fully working, fully
   tested, **completely untouched** by the pivot below.
2. **The Shadow Robot Spatial Demonstration Pipeline** (Round 5, new) — the
   project's actual current direction per a new master spec. A human
   demonstrates with a tracked hand, STRUCT renders a shadow hand, retargets
   onto a selected robot (the **real** SO-101 as of Round 7, swappable to a
   future CAD-generated robot later via the same `RobotProvider` interface
   with zero downstream rewrites), verifies in MuJoCo *or* now Isaac Sim
   (Round 8 — a second, independent, genuinely-different verifier, not a
   duplicate), exports accepted demos as `RobotEpisode` → LeRobot training
   data. Three interchangeable `HandFrame` sources: `mock`, `openxr`,
   `webcam` (Round 6).

The old app was deliberately left running rather than torn out — see Round
5's "why two apps" note. Combined test count: **74 passed / 12 skipped
Python, 233/233 vitest** as of Round 10, both suites green, lint clean.
Rounds 5-8 are on `feat/arvr-integration`; Rounds 9-10 (the headset path and
the sorting demo) are on `feat/openxr-hand-provider`.

### Round 10: red/blue ball sorting as the immersive Quest demo

Branch `feat/openxr-hand-provider`, on top of Round 9. The button task is a
good vertical slice and a bad demo: one press, nothing to carry, nothing a
watching human can score. This round adds the task the Quest demo is
actually for — six balls, three red and three blue, picked up by pinching
them and dropped into matching baskets, with the SO-101 shadow following
the same task-space intent and the whole thing recorded as a `HumanEpisode`
and uploaded for retarget/verify exactly as before.

**A second entry point (`sort-teleop.html` + `sortTeleopMain.ts`), not a
rewrite of `spatial-teach.html`.** Same reasoning as Round 5's "why two
apps": that page is the working, browser-verified button demo, and the
repo's habit is to leave a working thing running rather than break it on
the way to the next one. Both pages share every module that matters —
`hands.ts`, `xr.ts`, `xrCalibration.ts`, `xrHud.ts`, `shadowHand.ts`,
`shadowRobot.ts`, `liveRetargetSession.ts`, `humanEpisodeRecorder.ts`,
`humanEpisodeUpload.ts` — so the Quest build and the webcam build differ
only at the input layer, which is the property the spec asks for. Two
things did have to become parameters rather than constants:
`XrCalibration` now takes its anchor pair in the constructor (the sort
scene has no button to point at, so it calibrates against the robot base
and the red basket instead), and `PinchLatch.update` now accepts anything
carrying a `gripper` reading rather than a full WebXR `HandFrame`, so the
same hysteresis works on an already-converted struct_world frame without a
second copy of it.

**The layout is measured, not assumed.** `tools/so101_reach_envelope.py`
(new) parses the committed real SO-101 URDF with `ElementTree`, runs
forward kinematics over the five actuated joints, and does a position-only
damped-least-squares solve — position-only for the same reason `IkSolver`
is (Round 7's finding: a 5-DOF arm reaches position and an arbitrary
orientation jointly only on a lower-dimensional manifold). It exists
because Pinocchio is Linux-only and absent on this Windows machine, and
"no IK available" is not a reason to place a task's objects by guessing.
Nothing in the pipeline imports it; `IkSolver` still decides accept/reject
at verification time.

What it found, at table height z = 0.14 m (recorded in `sortLayout.ts`'s
own docstring so it is next to the numbers it justifies):

```text
x = 0.14 .. 0.38, |y| <= 0.24   every point converges under 5mm
x = 0.34, y = 0.34              1.4cm short
x = 0.42, y = 0.30              5.6cm short
x = 0.50, y = 0.00              2.1cm short
```

So the usable tabletop is about **0.48m wide by 0.24m deep**, not the
0.60 x 0.45 the task spec suggests. 0.60 wide puts the baskets at
y = ±0.30, already on the boundary; 0.45 deep needs x past 0.55, which the
arm cannot reach at any orientation. The spec's own instruction is to
validate real reach and move the objects rather than fake reachability, so
the scene is sized to the measurement and the deviation is written down
rather than discovered when IK starts failing mid-demo. Same reason the
baskets are 0.15m interior rather than the spec's 0.16m (a 0.16 basket
centered where these are puts its far corner at (0.36, 0.26), right on the
boundary — and the robot has to follow the hand *over* the basket, not
just to its center). Note the dense sweep the script prints covers
|y| ≤ 0.24, while `WORKSPACE_Y_M` is ±0.26; the extra 2cm rests on the
boundary probe at (0.34, 0.30), and `main()` as committed prints only the
dense grid, not those three boundary points — they were run ad hoc and
transcribed into the docstring.

**Where the rules live.** The entry point renders and records; it decides
nothing. `sortTask.ts` owns the predicate (what counts as sorted, plus a
deliberately tiny deterministic fall-and-rest so a released ball lands
somewhere reproducible), `grasp.ts` owns pickup (a 5cm distance test and a
rigid carry offset stored in the hand's frame, so turning your wrist swings
the ball around the pinch), `sortSession.ts` owns the per-frame wiring
pinch → grasp → carry → drop → score, `teleopTarget.ts` names the
task-space intent without doing any IK. The consequence that matters: the
spec's own acceptance scenario — grab `red_ball_0`, carry it, release over
`red_basket`, RED reads 1/3 — runs headlessly in vitest, along with all six
balls sorted to completion, and the layout constraints are asserted rather
than eyeballed (`scene layout sanity` in `sortSession.test.ts`: no two
balls closer than a diameter, no ball overlapping a basket wall, nothing
outside the measured workspace).

**Two real bugs, both found by running the thing rather than reading it:**

1. **A ball scored while being carried *through* a basket.** The
   containment test was a volume test on the ball's position with no regard
   for whether a hand was holding it, so carrying a ball across a basket on
   the way somewhere else scored it on the way past — and then scored it
   again when it finally landed, which is how `sort_complete` came to fire
   twice. A held ball is not in a container; reaching into a basket is not
   placing something in it. Fixed in `SortTask.settle` (`ball.held ? null :
   containingBasket(...)`), with a test per half: not scored while carried
   through, and the point taken back when a settled ball is lifted out again.
2. **The basket walls had their X and Y extents swapped**, so each basket
   rendered as two crossed sheets instead of an open box — visible in the
   very first browser render, invisible to every test that existed.
   struct_world X is depth and Y is width, three.js's `BoxGeometry` takes
   (width, height, depth); getting that pair the wrong way round builds a
   cross. In the same pass: the balls were spaced 4.5cm apart with a 6cm
   diameter, so they started life interpenetrating, and the test that should
   have caught it only asserted "more than one radius". Spacing is now 7cm
   and 9cm, and the layout tests assert the real constraint.

**THREE ADDITIVE CONTRACT CHANGES — flagged, not merged past review.** Each
is a widened `Literal` or a new optional field, each is marked
`ADDITIVE CONTRACT CHANGE (needs team sign-off)` in the Python source, and
`tests/test_spatial_contracts.py` proves for each that the original
vocabulary still validates and that unknown values are still rejected. Per
this repo's own rule that contracts are frozen before features, **these
need the team's sign-off**; they are written down here rather than treated
as an implementation detail:

1. `HumanEventType` gains `grasp_start`, `grasp_end`, `ball_enter_basket`,
   `wrong_basket`, `sort_complete`, `tracking_lost`; `HumanEpisodeEvent`
   gains optional `object_id` / `container_id`. Mapping "the red ball landed
   in the blue basket" onto the old `contact` would throw away the one fact
   the episode exists to record — a task predicate that cannot be
   reconstructed from the event stream is not training data. The TypeScript
   mirror in `humanEpisodeRecorder.ts` was widened to match.
2. `InteractionPhaseType` gains `lift`, `transport`, `place`. Pressing a
   button involves no carrying, which is why they were never needed before.
   (`InteractionPhase` also gained an optional `timestamp_ns` in the same
   diff — small, additive, same sign-off applies; the commit message's
   three-item summary does not mention it.)
3. `ObjectState` gains an optional `timestamp_ns`. Inside a `TwinState` the
   enclosing state carries the timestamp and this stays `None`; a
   `HumanEpisode`'s `object_states` are a time series, one sample per frame,
   and a policy cannot be learned from object poses that cannot be lined up
   with the hand frames that moved them. Sorting six balls is the first task
   that records object motion at all.

**Honest gaps:**

- **Not verified on hardware.** No headset was attached this session either.
  The flat/browser path was driven for real — that is what caught the
  crossed-sheet baskets — and 233/233 vitest, typecheck, production build
  and ruff are clean, but nobody has entered XR on a Quest, calibrated
  against the robot base and the red basket, and sorted a ball with a
  tracked hand. Everything Round 9's own hardware caveat says still applies
  unchanged.
- **The upload still carries a single goal point.** The shared `TaskSpec` is
  a goal position, so `finishDemo` sends the red basket's center as the
  goal — an honest projection of this task's real predicate (every ball in
  its matching basket), not the whole of it. The full predicate lives in the
  event stream, which is why the new event types record the objects and
  containers they concern. Teaching the backend the richer predicate is the
  next increment; faking it here was not.
- **The calibration anchor separation comment says 35cm; the computed value
  is 34.3cm** (robot base at struct origin, red basket at
  `[0.26, 0.175, 0.14]`). `anchorSeparationM` derives it, so nothing is
  wrong at runtime and the HUD shows 34cm — the prose rounds differently.

### Round 9: `HAND=openxr` becomes a path that can actually run on a headset

Branch `feat/openxr-hand-provider`, off `feat/arvr-integration`. Closes
Round 5's Next-step #2, which had stood open across three rounds: the
openxr code existed and was hardened, but had never been run on hardware
and — reading it carefully — could not have worked if it had been. Four
things stood between it and a demo, none of them typos:

1. **The hand landed nowhere near the robot.** WebXR's `local-floor` origin
   is the floor under wherever the headset booted, so a standing human's
   wrist sits ~1.3m from it. The SO-101 is a desktop arm with ~35cm of
   reach whose base is at struct_world `[0,0,0]`. Every frame asked the
   robot to reach three times further than it physically can; the only
   symptom would have been `ik_status` stuck at a failure, which reads as
   bad hand tracking rather than as a missing transform. CALIBRATE was an
   admitted no-op stand-in.
2. **FINISH was unreachable from inside.** All controls are DOM. An
   `immersive-vr` session composites only the WebGL layer, and
   `immersive-ar` shows DOM only when `dom-overlay` is granted — which
   `xr.ts` never requested. The demo could be started from a headset and
   not ended from one. `finishDemo` also called `session.end()`, so the
   only way to see a verdict was to be ejected from XR first.
3. **Passthrough was painted over.** `scene.background` is an opaque color,
   so a granted `immersive-ar` session would have shown a grey void where
   the room should be.
4. **The session started at the wrong moment** — inside START DEMO, so the
   human entered XR already recording, uncalibrated, with no way back out.

What landed:

- **`xrCalibration.ts`** — the real two-anchor solve, using `alignment.ts`'s
  existing tested `solveAlignment` rather than a second copy of the math.
  The human pinches where the robot's base belongs and where the button
  belongs; that fixes `T_struct_to_room` (which places the whole workspace
  in their room) and its inverse `T_room_to_struct` (which every tracked
  hand frame is mapped through). Deliberately **not** scale-corrected: the
  two anchors are 38cm apart because that is how far apart the robot's base
  and button really are, so a human who lays out a workspace the robot does
  not have gets a measured `anchorErrorM` and a rejection, not a silently
  stretched demo. The 5cm limit is a gate — `isCalibrated` is false until
  the number is good — not a note. 21 tests, including a metric-scale
  property (a 10cm hand move is 10cm in struct_world) and an
  anchors-round-trip-exactly property.
- **`invertAlignment` + `yawQuaternion`/`composeQuaternions`** in
  `alignment.ts`. Both transform directions are live at once, and a joint's
  orientation has to rotate by the same yaw its position does or the
  retargeter gets a pose whose halves disagree about which way is forward.
- **`xrHud.ts`** — an in-scene panel with poke-able buttons, so the flow is
  drivable from inside the headset whether or not `dom-overlay` is granted.
  Poking, not pinch-and-ray: the hand is already the input device being
  demonstrated with, and a fingertip position is the signal hand tracking
  reports most reliably. `PokeTracker` is edge-triggered with hysteresis
  (a fingertip resting on an edge would otherwise read as ~72 presses a
  second) and is plain geometry, so it is tested without a headset or a GPU.
  Buttons carry real depth for the same reason a bullet needs a thick
  target: a fingertip crossing a thin plate between two samples is never
  inside it.
- **One source of truth for the flow.** `controlSpecs()` describes the
  controls once; the DOM bar and the in-scene HUD both render from it. The
  headset and the desktop can no longer drift into offering different
  actions, which is exactly how FINISH went missing.
- **`spatialTeachLayout.ts`** — the fixture scene's struct_world constants,
  extracted because `xrCalibration` needs the same numbers the renderer
  uses. Two copies of a robot-base position would mean calibrating against
  one layout and retargeting against another.
- **`toWireHandFrame` takes an optional `roomToStruct`** rather than the
  openxr path getting its own conversion. Still one conversion function;
  `mock`/`webcam` pass nothing and are byte-identical to before. It also
  now has a real return type (`WireHandFrame`) instead of `object`.
- **Honest refusal**: `onHandFrame` drops openxr frames entirely while
  uncalibrated. Recording them would bank coordinates measured from a
  meaningless origin, and the pipeline would accept them.
- **The HUD says what is true**: which session kind was granted, whether
  hand tracking and dom-overlay were granted, the measured anchor error,
  and live wrist-distance-from-base so a human can see whether they are
  inside the arm's reach.

**Not verified on hardware.** No headset was attached this session. The
calibration math, poke semantics, pinch latch, and session-description logic
are unit-tested (165/165 vitest, typecheck and production build clean), and
the flat desktop path was re-driven in a real browser to confirm no
regression. Whether a Quest grants `immersive-ar`, `hand-tracking`, or
`dom-overlay` is answered by `probe.html` at runtime, not by this code.
Bring-up checklist is in `packages/xr-web/README.md`.

### Round 8: Isaac Sim verification (`IsaacSimulationProvider`)

Branch `feat/isaac-verifier`, off `feat/arvr-integration` (includes Rounds
6-7). Spec Milestone 3 (§70/§32-33) — assumed blocked in earlier planning
(no Spark access from this session), then confirmed *not* blocked: SSH to
the Spark (`ssh spark`) works, and `nvcr.io/nvidia/isaac-sim:5.1.0` is
already pulled there from Round 4's work. Live-tested end-to-end over SSH,
not just unit-tested against a fake server — a real 90-frame trajectory
from the mock episode went from this dev machine, over an SSH tunnel, into
a real Isaac Sim process on the Spark, through a real URDF import and real
PhysX joint control, and back as a real, structured `VerificationResult` —
not a stub.

**Real platform bug found and fixed, undocumented anywhere**: this image's
`ENTRYPOINT` (`/isaac-sim/runheadless.sh`) checks `uname -m`; on aarch64
(the Spark's GB10) it prints "Livestreaming is not supported on aarch64"
and `exec /bin/bash` — a bare interactive shell, silently ignoring
whatever `CMD` was passed to `docker run`, no error either way. This means
Round 4's own documented `run_twin_server.py` recipe (`bash -c '...'`, no
`--entrypoint`) only ever worked when run *interactively* (`-it`, a human
retyping commands into the dropped shell) — run non-interactively/scripted,
it silently no-ops. Fix: `--entrypoint /isaac-sim/python.sh` (or `bash`,
for a two-step recipe), bypassing `runheadless.sh` entirely. Documented in
`packages/isaac-bridge/README.md`, both recipes updated (the twin-server
fix is inferred from the same confirmed root cause, not independently
re-tested this round — out of scope, pre-existing Round 4 code).

**What's real:**

- `packages/isaac-bridge/run_verify_server.py` (new) — a persistent,
  batch request/response WebSocket server (not a push-stream like
  `run_twin_server.py`) that imports the real SO-101 URDF ONCE at startup
  (confirmed empirically: `isaacsim.core.prims.SingleArticulation`'s
  `dof_names` order — `['shoulder_pan','shoulder_lift','elbow_flex',
  'wrist_flex','wrist_roll','gripper']` — matches
  `ar_datapipe.retarget.IkSolver`'s Pinocchio order exactly, same robot,
  same URDF, no name-order surprises found), then answers many requests
  against that one persistent articulation: drives joints via
  `set_joint_positions`, steps real PhysX, reads back the real
  `gripper_frame_link` prim's world pose (not the parent link — a fixed-joint
  child, confirmed present as a distinct prim with
  `merge_fixed_joints=False`), computes real tracking error, evaluates
  joint limits (from `robot_ir.json`, already loaded from the same repo
  checkout) and the task predicate.
- `packages/spatial-providers/src/spatial_providers/isaac_simulation_provider.py`
  (new) — `IsaacSimulationProvider`, a thin WS client implementing
  `SimulationProvider`. Zero Isaac/Kit dependency (unlike MuJoCo's provider,
  needs no platform gating at all — `websockets` is pure Python). Config:
  `STRUCT_SIMULATION_PROVIDER=isaac` / `ISAAC_VERIFY_WS_URL`, matching the
  `STRUCT_ROBOT_PROVIDER` pattern exactly. Raises
  `IsaacVerifyServerUnavailable` (a clear error, not a hang) when the
  server isn't reachable — confirmed live against port 1 (nothing
  listening).

**Real bug found and fixed while building the request wire format**:
`mujoco_simulation_provider.py`'s pre-existing pattern of deriving
`joint_names` from `robot_ir.json`'s raw array order (fixed in Round 7 for
MuJoCo itself) needed the exact same fix applied here — reused
`ar_datapipe.robot_model.robot_model_from_bundle`'s generic derivation
rather than re-deriving it a third time.

**A genuine finding, not a bug — left as-is rather than tuned away**: the
live trajectory's `replay` check narrowly failed (tracking error ~1.0-1.2cm
against a 1cm tolerance, consistent across both 1 and 3 physics-settle-steps
per frame — ruled out as a settling-time artifact by testing both). Isaac's
real PD-drive dynamics (gravity + joint drive gains, never explicitly
tuned via `ImportConfig.set_default_drive_strength` — using Isaac's own
import defaults) introduce a small, real discrepancy between commanded and
achieved joint state that MuJoCo's purely kinematic replay
(`mj_forward`, no dynamics stepping) can't see by construction. This is
*exactly* what a second, independent, dynamically-authoritative verifier
is supposed to catch — loosening the tolerance to force a pass would defeat
the point. `task_predicate` still passed (Isaac's physics settled close
enough to the actual goal). Tuning drive stiffness is the natural next
lever if closer MuJoCo agreement is wanted; not done this round.

**What's honestly NOT checked**: collision (`collision_valid` stays `None`
— an explicit "not evaluated" per `VerificationChecks`' own contract, never
a false "passed"). Isaac's contact-reporting API
(`PhysxContactReportAPI` per-body + a `RigidContactView`/`ContactSensor`)
needs meaningfully more setup than MuJoCo's `data.ncon`; real, separate
scope. MuJoCo's own collision check (Round 7) already exists and works —
this isn't a regression, just not yet replicated against a second engine.

`STRUCT_SIMULATION_PROVIDER` (default `"mujoco"`, `"isaac"` the new option)
now selects between them at `ar_backend`'s actual
`/spatial/episodes/{id}/finish` route via a new
`get_configured_simulation_provider()`, mirroring
`get_configured_robot_provider()`'s exact pattern (spatial_providers
`__init__.py`). **The `isaac` path through the real backend route itself
hasn't been live-tested** (only the standalone client script was, over
SSH) — the wiring mirrors an already-proven pattern closely enough that
this is a reasonable next verification step, not a completed one.

### Round 7: Real SO-101 robot (kinematics, gripper, position-only IK)

Branch `feat/real-so101-robot`, off `feat/arvr-integration` (includes Round
6). Live testing of the webcam path surfaced that the fixture "SO-101" was
never the real robot — `fixtures/robot/test_arm.urdf` was a made-up
placeholder with no gripper. The user wanted the actual, professional
SO-101 (confirmed via AskUserQuestion over a full GR00T-style humanoid,
which would need a whole-body retargeting redesign — declined, out of
scope). SSH to the Spark (`ssh spark`) was also confirmed working, with
`nvcr.io/nvidia/isaac-sim:5.1.0` already pulled there — Isaac verification
(Round 8, next) was previously assumed blocked and isn't.

**What's real:** `fixtures/robot/so101_real/` vendors
`TheRobotStudio/SO-ARM100`'s real URDF + 13 STL meshes verbatim
(Apache-2.0, commit `7629d2a`, see that directory's `NOTICE.md`) —
`tools/make_real_so101_bundle.py` parses it directly via `ElementTree`
(not hand-transcribed — the real joint origins have non-trivial rotations,
retyping ~20 long floats by hand was exactly the kind of transcription
risk worth avoiding) and regenerates
`fixtures/spatial-training/robots/so101/` from it: real joint origins/axes/
limits, a real `gripper` joint (revolute, drives an actual moving jaw —
the placeholder had none), and `visual_meshes.json` (new, additive —
per-link real mesh list `shadowRobot.ts` loads via `STLLoader` at
runtime, best-effort; a marker-sphere skeleton renders immediately and
always, so the robot is visibly correct even before, or if, real mesh
loading fails).

**Real bugs found and fixed, not just "make tests green":**

1. **The real SO-101 is 5-DOF arm + 1-DOF gripper, not 6-DOF.** Full 6-DOF
   pose IK (arbitrary position + arbitrary orientation) is only reachable
   on a lower-dimensional manifold, not everywhere — confirmed directly: a
   position that converged from one exact FK-derived pose *failed* to
   converge again after rounding it to 4 decimal places (under half a
   centimeter of drift was enough to leave the reachable manifold
   entirely). Fixed with a genuine `position_only` mode in `IkSolver.solve`
   (truncates the pose error/Jacobian to the first 3 — position — rows;
   confirmed directly against Pinocchio that linear components come
   first, not assumed), wired through `ArmRetargeter`'s callers keyed off
   `RobotCapabilityProfile.arm_dof < 6` — generic, not SO-101-specific.
2. **MuJoCo's URDF compiler welds away a body connected only by a fixed
   joint** — the real gripper's dummy tool frame (`gripper_frame_link`,
   past the jaw) produces zero MuJoCo bodies, only Pinocchio preserves it
   as a distinct frame. `RobotModel` gained an optional
   `mujoco_ee_body`/`mujoco_ee_offset_m` fallback (`robot_model_from_bundle`,
   new, derives it generically from `robot_ir.json` rather than
   hardcoding "gripper_link" in three call sites) so MuJoCo tracking
   targets the *exact* same point Pinocchio's IK does, composed back onto
   a real body's world pose.
3. **A latent joint-order bug, only exposed by the real robot**:
   `mujoco_simulation_provider.py` derived `joint_names` from
   `robot_ir.json`'s own array order, but `RobotTrajectoryFrame.q` is
   populated in `ArmRetargeter`/Pinocchio's kinematic order — these only
   coincidentally matched for the old hand-authored placeholder (written
   base-to-tip on purpose). The real `robot_ir.json`, parsed straight from
   the vendored URDF, preserves the URDF's own tip-to-base file order
   instead, so MuJoCo silently replayed the wrong q value under the wrong
   joint name. Fixed by reading the order from the same `IkSolver` instance
   `ArmRetargeter` used, not a second, independently-derived list.
4. **Collision checking's "ncon > 0 = collision" rule assumed the
   placeholder's simple primitive shapes never touch.** The real SO-101's
   own vendored collision meshes (adjacent motor housings, mounting
   plates) touch by design at rest — confirmed directly, a real non-zero
   contact count in the neutral pose alone, and raw contact *count* isn't
   reliable either (the same resting pair registers as 4 or 9 contacts
   depending on mesh-triangle alignment at a given joint angle, confirmed
   by inspecting `dist` directly — same handful of penetration depths
   repeated, not new/deeper ones). Fixed by baselining max penetration
   *depth* against the neutral pose instead of a hardcoded zero or a raw
   count, with a small margin for mesh noise.
5. **`_derive_capability_profile` counted every non-fixed joint as
   `arm_dof`** — correct when the only non-fixed joints were arm joints,
   wrong the moment a real gripper joint existed too. Fixed to read
   `actuator.json`'s own arm/gripper split instead of re-deriving it from
   `robot_ir.joints`.

**Gripper now flows end-to-end for real**, not just visually: `ArmRetargeter`
writes the pinch-derived gripper command into `q`'s gripper index directly
(the gripper joint is a side-branch off the EE frame's own kinematic chain
— zero Jacobian column against it, so IK never touches that index; explicit
overwrite after each solve is required, not optional) — meaning the SAME
`q` vector drives the live shadow robot's jaw (`ShadowRobot.setJoints`,
no separate field/contract needed, `q` is just 6 elements now) *and*
replays a real, moving jaw in MuJoCo verification. `RobotShadowState` did
**not** need a new field — a plan-time assumption that turned out
unnecessary once this was traced through.

**Scene geometry recentered on real, IK-verified positions** (not
guessed): `ROBOT_BASE_STRUCT`/`ASSET_WORLD_POSITION`/`DEFAULT_GOAL_M`
(`spatialTeachMain.ts`), `BUTTON_WORLD_M`/`APPROACH_START_M`/`RETRACT_END_M`
(`tools/make_mock_hand_episode.py`), `DEFAULT_CONTROL_VOLUME`
(`webcamHand.ts`), and every test's own hardcoded goal — all were tuned
against the placeholder's ~1m reach; the real SO-101 is a small desktop
arm, maybe 30-40cm. Every new position is the literal FK output of a real,
within-joint-limits configuration (verified directly against
`ar_datapipe.retarget.IkSolver`), not eyeballed.

**Not yet done:** live browser verification with a real camera/session —
same limitation as Round 6, this agent has no way to open a browser here.
`npx vitest run`/`npm run typecheck`/`npm run build`/`uv run pytest`/
`ruff check` all pass (140/140 Python, 111/111 vitest), but nobody has
watched the real mesh assembly actually render, or the jaw actually open/
close live, in an actual browser tab yet.

### Round 6: Webcam hand tracking provider (browser-side MediaPipe)

Branch `feat/webcam-hand-provider`, off `feat/arvr-integration`, not yet
merged. The user wanted to test the pipeline with their laptop's own
camera instead of a Quest — a new spec
(`STRUCT_Webcam_MediaPipe_Hands_Claude.md`) asked for a `WebcamHandProvider`
peer to `MockHandProvider`/`OpenXRHandProvider`, producing the same
`HandFrame`, zero downstream changes.

**Architecture decision, confirmed with the user before building (diverges
from the spec's literal text):** the spec is written Python/OpenCV-flavored
(`cv2.VideoCapture`, `python -m ... webcam_test` CLIs, Windows DirectShow
notes). But every live hand-tracking path in this app is browser-side, and
the Python `HandProvider` ABC (`spatial-providers`) has **no live consumer
at all** — it's only used offline, replaying an already-recorded
`HumanEpisode` during `/spatial/episodes/{id}/finish`. The spec's own
Phase-4 gate ("mock and webcam share renderer") requires webcam frames to
reach the existing THREE.js `ShadowHand`, which lives in the browser.
Built it with MediaPipe's official JS/WASM Hand Landmarker
(`@mediapipe/tasks-vision`) + `getUserMedia` instead — same model, same 21
landmarks, reuses `ShadowHand`/`LiveRetargetSession`/`HumanEpisodeRecorder`
exactly as they already exist, zero new backend service.

**What's new:**

- `packages/xr-web/src/webcamHand.ts` — `WebcamHandProvider`, a peer to
  `MockHandProvider`. Pure conversion functions
  (`mediapipeResultToHandFrame`, `imageToControlSpace`,
  `worldLandmarksToStructJoints`, `palmOrientation`, `emaSmooth`,
  `PinchHysteresis`) are exported and unit-tested against synthetic
  MediaPipe-shaped results — no camera/WASM/DOM needed, same split as
  `resolveBoneSegments`/`reachStatus` elsewhere. Builds an intermediate
  struct_world `StructHandFrame` and converts it through `mockHand.ts`'s
  existing `toHandFrame` (the same device-frame boundary the mock provider
  already uses) rather than duplicating the WebXR conversion.
- `ar-contracts`'s `HandSourceDevice` gained the `"webcam"` literal
  (`Literal["openxr","phone","mock","webcam"]`) — additive, same pattern
  as the earlier `VerificationChecks.collision_valid` extension.
- `liveRetargetSession.ts`'s `toWireHandFrame` gained a `sourceDevice`
  parameter (default `"openxr"`, preserving old call sites) — **fixed a
  real pre-existing bug found while wiring this in**: it previously
  hardcoded `source_device: "openxr"` on every wire frame, silently
  mislabeling `mock` recordings' provenance too. `humanEpisodeRecorder.ts`
  and `spatialTeachMain.ts` now pass the real provider through.
- `spatialTeachMain.ts` gained a third HAND option, gated on
  `navigator.mediaDevices` existing (mirrors how `openxr` is gated on
  `detectCapabilities()`), a webcam preview panel in `spatial-teach.html`
  (spec's split-screen layout, CSS-mirrored for natural selfie-view), and
  the spec's required honesty readout (`HAND SOURCE: LAPTOP WEBCAM` /
  `MODE: SCREEN CONTROL` / `DEPTH: ESTIMATED`) plus live fps/handedness/
  pinch.

**Two real bugs found by the new tests, not by inspection** (same
discipline as Round 5): `palmOrientation`'s rotation-matrix-to-quaternion
conversion was fed a left-handed basis (wrong cross-product order),
producing a non-unit quaternion — caught by a magnitude assertion, fixed
with a proper Gram-Schmidt orthonormalization. A smoothing test asserted
the wrong array index — `hands.ts`'s `HandFrame` is WebXR-space, and
`adapter.ts`'s `structToWebxr` maps struct `[x,y,z]` → webxr `[-y,z,-x]`,
so a struct-x change shows up (negated) at webxr `position[2]`, not `[0]`;
the test was wrong, not the code.

**Judgment calls to flag:**

1. Browser-side MediaPipe, not the spec's literal Python/OpenCV — see
   architecture decision above. Confirmed with the user first.
2. MediaPipe has only one metacarpal landmark (thumb); the WebXR
   `*-finger-metacarpal` joints for index/middle/ring/pinky, and `palm`,
   are simply never emitted for webcam frames rather than fabricated. Real,
   visible consequence: those four fingers don't visually connect to the
   wrist in `ShadowHand` for webcam-sourced hands (only thumb does) — a
   disclosed gap, not a bug.
3. Only the `wrist` joint's orientation is genuinely derived (palm basis
   from three real landmarks, spec §18); finger-joint orientations stay
   identity, since `hands.ts`'s `JointPose` has no `orientation_valid` flag
   to extend for a purely cosmetic field, and `ShadowHand` never reads
   per-joint orientation anyway.
4. Default control-volume bounds are **not** the spec's literal example
   numbers — they're recentered on this fixture scene's actual reachable
   geometry (robot base at struct Y=-0.7, button at Y=0.0,Z=0.53; see
   `DEFAULT_CONTROL_VOLUME` in `webcamHand.ts`). The spec's own axis
   labels ("Y"=vertical, "Z"=depth) also don't match this app's real
   Z-up struct convention (Z is genuinely "up" elsewhere, e.g. the
   button's `z=0.53m` table height) — image up/down was deliberately
   mapped to struct Z, camera depth to struct Y, not copied literally.
5. Depth sign (`relative_mediapipe` strategy's near/far raw-z reference
   points), the world-landmark axis mapping, and the mirrored-preview
   handedness flip are internally consistent, tested design choices —
   but **not yet verified against a real camera** (this agent has no
   webcam access). Flagged in `webcamHand.ts`'s own docstring: "measure
   rather than assume," confirm live and flip signs if toward/away or
   left/right reads backwards.
6. MediaPipe's WASM fileset + `.task` model load from MediaPipe's own
   hosted CDN (jsdelivr / Google Cloud Storage), not self-hosted — first
   run needs internet. Avoided pulling a multi-MB binary through this
   agent's network-sandboxed shell; a disclosed tradeoff, not an oversight.
7. Calibrated-workspace (ArUco) and a real Quest re-test are explicitly
   out of scope this round, per the spec's own phase ordering (§46
   Phases 10-11) — this covers Phases 1-9 (the "vertical slice") only.

**Not yet done:** live browser verification with a real camera — this
agent has no webcam access, so `npx vitest run`/`npm run typecheck`/
`npm run build`/`uv run pytest` all pass, but nobody has actually opened
`spatial-teach.html`, selected HAND=webcam, and watched a real hand track.
That's the next step, and it needs the user's own machine.

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

1. ~~Open `spatial-teach.html` in a real browser and click through CALIBRATE
   → START DEMO → FINISH.~~ **Done** — user confirmed live: shadow hand
   tracks the mock demo, robot bends through IK, verdict renders. (Initial
   run surfaced a real bug — missing CORS middleware stalled the whole demo
   silently; see Round 5's CORS note above. Fixed and reconfirmed.)
2. `HAND=openxr` on real hardware (a Quest). Hardened this round (see
   below) — `preferredInputSource` fixes a real two-hands-interleaving bug,
   `session.end()` added on FINISH, `API_BASE` no longer hardcodes
   `127.0.0.1`. A Windows-side `netsh interface portproxy` rule was set up
   to bridge WSL's NAT isolation for LAN (headset) traffic reaching
   `ar_backend`; the matching firewall rule was never confirmed run. **Not
   live-tested against real hardware** — the user set this aside in favor
   of `HAND=webcam` (Round 6, below) rather than continuing the headset
   path. Still a real gap, not closed.
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
