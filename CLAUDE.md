# STRUCT — nvidia-spark-hacks

## What this is

Hackathon prototypes for **STRUCT**, targeting the NVIDIA DGX Spark (GB10 Grace
Blackwell, sm_121, aarch64, CUDA 13). Two workstreams live here:

- **realsim (F3)** — turns one phone video into N validated digital-cousin simulation
  scenes. Implemented, end-to-end, and driven by a single validation loop.
- **AR/XR spatial layer (`arvr/`)** — now a **hand-demonstration data collection**
  app: a human picks a mug off a table in front of a webcam, and that recording is
  exported as a LeRobot dataset for reinforcement learning. Read
  `arvr/DATA_COLLECTION.md` before touching that tree — it is the current, accurate
  description of what the app does and what the dataset contains.

  The mode-based AR app, the button task, and the ball-sorting demos were all removed;
  `STRUCT_2.md` and `arvr/ar-xr-plan.md` describe earlier directions and are history,
  not specifications. The **capture scene deliberately contains no robot**: the data is
  *for* a robot arm the CAD half has not generated yet, and a `HumanEpisode` is
  robot-independent by design so the same recordings compile onto that arm later.

The organizing idea across both: humans express physical intent spatially, STRUCT turns
that into robot-compatible data, and simulation state comes back into the room as a
digital twin.

Two planning docs for adjacent features also live at the root and have no code yet:
`text-to-cad-plan.md`, `text-to-pcb-plan.md`.

## Stack

- **realsim**: Python 3.11, uv workspace, MuJoCo, OpenUSD (`usd-core`), trimesh,
  COLMAP/gsplat backends, ruff + pytest. Runs on the dev machine against fixtures; GPU
  rungs are Spark-only.
- **AR/XR (planned)**: Swift/SwiftUI/ARKit/RealityKit on iOS; FastAPI + Pydantic +
  NumPy + PyArrow backend; Pinocchio for IK; LeRobot v3 for dataset export; optional
  WebXR client in TypeScript/Three.js.
- **Spark side**: Isaac Sim as the live simulation source, OpenUSD as the authoritative
  twin, vLLM serving Laguna S 2.1 NVFP4 on :8000.

## Architecture

**realsim** is a staged pipeline, every stage behind `run_stage` (content-addressed
cache + gates + graceful degradation):

```
capture → reconstruct → segment → assetize → generate → shell → cousins → tasks → validate
```

`packages/r2s-core` owns the machinery every stage shares — schemas, the CAS store,
gate registry/runner, provenance, run context. `packages/scan` owns the perception half
(capture through assetize/generate), `packages/envgen` owns the synthesis half (shell,
cousins, tasks, validate), and `packages/r2s-cli` is the `r2s` entry point. A stage
never validates itself: it emits artifacts, and a registered *gate* decides pass /
degraded / fail. The process exit code is the verdict — 0 pass, 10 degraded-but-shipping,
20+ broken.

**AR/XR** (per `STRUCT_2.md`) has one rule that everything else hangs off: no robotics
code may depend on a device-specific frame type. Every input device — phone, XR
controller, hand tracking, desktop mock — is converted by a Spatial Adapter into a
canonical `SpatialFrame`, and the same downstream pipeline runs regardless of source.
The client renders simulation state; it never simulates the robot itself.

## Folder structure

- `realsim/packages/` — the uv workspace members (`r2s-core`, `scan`, `envgen`, `r2s-cli`)
- `realsim/fixtures/tiny_room/` — the synthetic 36k-gaussian room the whole pipeline is
  developed against; deterministic (seed 20260814), regenerate with `tools/make_fixture.py`
- `realsim/spark/` — Spark-only preflight and GPU probes (`preflight.sh` VERIFIES)
- `realsim/docker/` — recon-gpu / tools-cpu / sim images for the Spark
- `setup/` — one-time Spark bring-up (`setup_spark.sh` INSTALLS, `serve_laguna.sh` serves)

## Commands

All realsim commands run from `realsim/` (it is a **uv** workspace — see the
`python-uv-projects` skill; never `pip install` into it).

```bash
make loop        # lint -> unit tests -> fixture pipeline. THE verdict on repo health.
make lint        # uv run ruff check packages tools tests
make test        # uv run pytest tests/ -q
make run         # uv run r2s run-all fixtures/tiny_room --fixture -n 8 --seed 1337
make run-cold    # same, cache off (--force on every stage)
make gates       # uv run r2s gates
make fixture     # regenerate fixtures/tiny_room
```

`arvr/` has the same loop contract (`make loop` = lint + tests) and its own uv workspace.
Its browser client (`arvr/packages/xr-web/`) has its own: `npm run typecheck`,
`npx vitest run`, `npm run build`.

- **`arxr/` is dead.** It was consolidated into `arvr/` (see `arvr/STATE.md`). A stale
  untracked `arxr/` directory may still sit in the working tree on this machine — ignore
  it, and never `git add -A` from the repo root, which sweeps its `node_modules` and
  `dist` into the index.

**`make` is not installed on the Windows dev machine.** The Makefiles are the source of
truth for what the loop *is*, but here you run the steps directly:

```bash
uv run --no-sync ruff check packages tools tests
uv run --no-sync pytest tests/ -q
```

- **Always pass `--no-sync` to `uv run` in `arvr/`.**
  Why: without it uv re-syncs on every invocation, and on this OneDrive-backed venv the
  uninstall step intermittently fails to delete `numpy.libs` ("Access is denied"),
  leaving two `numpy-*.dist-info` directories and a half-written package. The symptom is
  a later `ImportError: cannot import name '_methods' from partially initialized module
  'numpy._core'`, which looks like a code bug and is not. Repair with
  `uv pip install --reinstall numpy`.

`pytest` deselects `cuda` and `isaac` markers by default (and `isaac`/`device` in arvr),
so a green local run says nothing about the GPU rungs or anything needing a phone.
`make clean-run` and the Makefile's `rm -rf` also need a POSIX shell — use the Bash tool,
not PowerShell.

- **Pinocchio is Linux-only and is not installed here.** Anything downstream of IK —
  `ar_datapipe.retarget.IkSolver`, and therefore `POST /spatial/episodes/{id}/finish` —
  cannot run on this machine and returns 503. Use the human-layer export
  (`/export-human`) for anything that must work locally, and measure robot reach with
  `arvr/tools/so101_reach_envelope.py`, which does FK from the URDF without Pinocchio.

## Conventions

- **Never work on `main`.** Branch by capability, not by person: `feat/ar-teach`,
  `feat/ar-follow`, `feat/ar-isaac-bridge`. Capability branches merge into
  `feat/arvr-integration` only after their acceptance tests pass; that branch reaches
  `main` only after the integrated subsystem passes its own gates.
- **Canonical spatial convention**, everywhere, no exceptions: right-handed, **Z-up**,
  **meters**, quaternion `[x, y, z, w]`, timestamps in **nanoseconds**. Adapters convert
  at the boundary; nothing downstream re-interprets units.
- **Contracts are frozen before features.** `SpatialFrame`, `SpatialEpisode`,
  `TwinState`, `FollowState`, `CorrectionEvent`, `SceneManifest` are shared by the phone
  and the backend. Changing one is a reviewed, coordinated act, not an implementation
  detail.
- **Fixtures before integrations.** No feature waits on another feature or on Spark
  access. Develop against fixture data and a mock provider, then swap the provider
  (`MockTwinStateProvider` → `WebSocketTwinStateProvider`) with no client rewrite.
- **Gates decide, not judgment.** A demonstration is "verified" only when retarget, IK,
  joint/velocity limits, replay, and the task predicate all pass deterministically.
  Never mark a gate PASS from model reasoning, and always preserve the measurable reason
  a rejected episode was rejected.
- **USD is simulation truth; GLB is the mobile visualization.** The phone does not render
  full USD.
- Name AR/XR-owned containers, ports, logs and temp files with a `struct-ar-` prefix so
  they are distinguishable on shared infrastructure.

## Dangerous areas

- **The shared DGX Spark.** It is team infrastructure. Work only inside your own
  `$SPARK_HACK_ROOT/ar-vr/<name>/` directory. Do not modify sibling directories, delete
  containers you did not create, kill processes you did not start, or upgrade system
  packages — any of those can silently break a teammate mid-build. Give Spark-only
  branches their own `git worktree` rather than sharing one dirty checkout.
- **Shared branches.** No force-push, no `git reset --hard`, no `git clean -fdx` at the
  repo root. These are denied in `.claude/settings.json`.
- **`realsim/packages/envgen/src/envgen/validate/stage.py`** — the hull path recovery is
  the weakest code in the repo and has never executed end-to-end (blocked behind the
  cousins bug). Expect first-run failures in `_witness_goal` / `_materialize_meshes`.
- **`realsim/uv.lock`** — a real workspace lock. Change dependencies through `uv`, and
  say why before adding one.

## Known edge cases

- **Network is sandboxed.** `git fetch`/`push` and any other egress fail with
  "Could not resolve host" unless the domain is in `sandbox.network.allowedDomains`
  (`.claude/settings.json` allows GitHub, PyPI, and astral.sh). Anything else needs the
  sandbox disabled for that one command.
- **`gh` CLI is not installed** on this machine. Use plain `git` plus the GitHub MCP
  tools.
- **The cousins stage is currently red** — `GenerationFailure: worst gate: settle`, with
  penetration pinned at a constant 6.9 mm across retries (threshold 2 mm). The working
  diagnosis is geometric, not dynamic: objects spawn at `surf_h + 1e-3` on the scanned
  table's convex-hull surface, which carries the fixture's ±3 mm noise plus hull-facet
  bumps. See `realsim/STATE.md` for the two candidate fixes, in order.
- **Determinism is per-machine.** Settled poses are baked into artifacts, so two runs on
  the *same* machine must match byte-for-byte; across machines expect to need a
  tolerance.
- **`realsim/STATE.md` is the live resume document** for realsim. Read it before touching
  that subtree — it is more current than this file for pipeline state.
- **Vite proxy keys match as bare prefixes, not path segments.** `arvr/packages/xr-web`
  proxies `/spatial` to the backend, which also swallowed every request for
  `/spatial-training/...` — the fixture meshes and assets. Keys are now anchored
  (`^/spatial(/|$)`); keep them that way when adding routes.
  Why: the failure is silent and misleading — assets 404 while sitting plainly on disk,
  and the client falls back to a placeholder rather than erroring.
- **A 404 is not a thrown `fetch`.** It resolves with a parseable error body that sails
  through `.json()` and only explodes later, far from the cause. Check `response.ok`
  and the parsed shape before using it — `ShadowRobot.loadRealMeshes` crashed this way
  with "cannot convert undefined to object" deep in the render path.
- **A 404 from `ar_backend` usually means a stale uvicorn, not a missing route.**
  Without `--reload`, uvicorn serves whatever it imported at startup forever, so a route
  added since then is absent and FastAPI answers `404 {"detail":"Not Found"}` — while
  older routes on the same prefix still answer 200, which makes it look like a routing
  or proxy bug. Check what the *live* process actually serves before reading any code:
  `curl -s http://127.0.0.1:8000/openapi.json | grep -o "/spatial/episodes[^\"]*"`.
  Why: the code on disk is correct, so every code-first hypothesis is a dead end. This
  cost a full debugging session; `/export-human` 404'd while `create` and `artifact` 200'd.
- **On Windows a second uvicorn can bind an already-used port instead of failing.**
  Windows' default `SO_REUSEADDR` semantics let both sockets bind, and which one gets a
  given connection is undefined — so a successful "Uvicorn running on ..." line does NOT
  prove the old process is gone, and killing the new one silently hands traffic back to
  the stale one. Confirm with `netstat -ano | grep :8000` and check the PID.
- **`app.routes` does not contain flat routes in this FastAPI version** — it holds
  `_IncludedRouter` wrappers, so `{r.path for r in app.routes}` finds nothing and any
  route-existence check written that way passes vacuously. Read `/openapi.json` instead;
  it is also what a client can actually see.
- **A camera pointed at the user sees them mirrored, and the flip must reach positions,
  not just labels.** `resolveHandSide` flips the handedness *label* for a mirrored
  preview; `placeInControlVolume` and `worldLandmarksToStructJoints` must take the same
  `mirrored` flag and always agree with each other.
  Why: with image-x unflipped the camera→struct map is a **reflection** (negative
  determinant), which renders a right hand as a left hand *and* inverts left/right
  control. A reflection is correct on every axis taken one at a time, so no per-axis test
  catches it — assert the sign of a scalar triple product (`webcamHand.test.ts`'s
  chirality tests). Flipping one of the two functions without the other puts the fingers
  on the wrong side of the wrist.
- **MediaPipe world landmarks are camera-relative, so they rotate with the camera exactly
  as the wrist anchor does.** The image→struct mapping has two consumers
  (`placeInControlVolume` for the anchor, `worldLandmarksToStructJoints` for the finger
  geometry); both now derive from `cameraBasis(tiltDeg, mirrored)`. Keep it that way —
  a rotation cannot mirror, so deriving from one basis makes inside-out hands
  unrepresentable rather than merely tested-against.
- **A mirrored CSS preview does NOT mirror what MediaPipe sees.** `transform: scaleX(-1)`
  on the `<video>` element changes presentation only; the landmarker reads the raw frame,
  so its handedness label needs no correction (`LANDMARKER_INPUT_MIRRORED = false`).
  Geometry is a separate question — a camera pointed at you really does see you mirrored,
  so positions DO flip. Never drive both from one flag.
  Why: while both were flipped the result looked self-consistent (a "left" label on the
  left of the screen, in a world mirrored end to end) and every frame self-reported a
  plausible handedness, so no unit test could catch it. Only real two-handed data showed
  it: the right hand was on the +X side of the left in 0.0% of 1215 paired instants.
- **Calibrate constants against a recorded episode, not against population averages.**
  Query `arvr/datasets/human_demos.sqlite` directly. Doing this once found four wrong
  constants at the same time — handedness, palm length (9.4cm vs an assumed 10), grip
  thresholds (91% of frames saturated, so closure was effectively a boolean), and a mug
  clamped 5cm inside the table.
- **Hand POSITION is metric (the palm is the ruler); the control volume only clamps.**
  `metresPerImageUnit` divides the real palm length by the apparent palm span — both in
  normalized image units, so focal length cancels and no calibration is needed.
  Why: interpolating image position across the control volume makes the frame width equal
  the box width, while MediaPipe world landmarks arrive in true metres. Each hand then
  renders full size with the GAP between two hands squashed by the box-to-FOV ratio —
  hands 40cm apart landed ~18cm apart. Never size a control volume to set scale; size it
  to the reachable workspace, and keep depth on its own fixed travel so widening the box
  does not change depth sensitivity.
- **Camera placement is a continuous tilt angle, not a mode.** A laptop lid cannot reach
  90°; fully open it looks down ~30-50°, so presets for only 0° and 90° put vertical
  motion on the wrong axis at every real lid angle.
  Why: the demonstrator sees vertical control inverted AND the hand model tipped over,
  which look like two bugs and are one. If someone reports either, check that the tilt
  setting matches the physical camera before touching the mapping.
- **Fixture GLBs are authored Z-up (struct_world); three.js renders Y-up.** Placing one
  with `placeAtStruct` alone leaves it lying on its side — it also needs
  `orientToStruct`. And check whether an asset's local origin is its centre or its base
  before positioning it; the generated mug is base-origin, and assuming centre leaves it
  hovering half its height above the table.

## Keeping this file current

When a session gets corrected, discovers a non-obvious detail, or finds this file wrong,
update the relevant section — see the `capturing-lessons` skill. Directory-specific rules
belong in that directory's `CLAUDE.md`, not here.
