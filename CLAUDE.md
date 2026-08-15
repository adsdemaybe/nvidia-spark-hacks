# STRUCT — nvidia-spark-hacks

## What this is

Hackathon prototypes for **STRUCT**, targeting the NVIDIA DGX Spark (GB10 Grace
Blackwell, sm_121, aarch64, CUDA 13). Two workstreams live here:

- **realsim (F3)** — turns one phone video into N validated digital-cousin simulation
  scenes. Implemented, end-to-end, and driven by a single validation loop.
- **AR/XR spatial layer (F4 + F5)** — the human-facing interface: place a robot in a
  real room, teach it a task by moving a phone, replay the verified demonstration, walk
  and have it follow, correct its trajectory in space, and see live Isaac Sim state
  overlaid on the physical room. Planned in `STRUCT_2.md`; not yet implemented.

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

`arxr/` has the same loop contract (`make loop` = lint + tests) and its own uv workspace.

**`make` is not installed on the Windows dev machine.** The Makefiles are the source of
truth for what the loop *is*, but here you run the steps directly:

```bash
uv run ruff check packages tools tests
uv run pytest tests/ -q
```

`pytest` deselects `cuda` and `isaac` markers by default (and `isaac`/`device` in arxr),
so a green local run says nothing about the GPU rungs or anything needing a phone.
`make clean-run` and the Makefile's `rm -rf` also need a POSIX shell — use the Bash tool,
not PowerShell.

## Conventions

- **Never work on `main`.** Branch by capability, not by person: `feat/ar-teach`,
  `feat/ar-follow`, `feat/ar-isaac-bridge`. Capability branches merge into
  `ar/vr` (the AR/VR integration branch, renamed from `feat/arvr-integration` once
  the individual capability branches it had absorbed were cleaned up) only after
  their acceptance tests pass; that branch reaches `main` only after the
  integrated subsystem passes its own gates.
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

## Keeping this file current

When a session gets corrected, discovers a non-obvious detail, or finds this file wrong,
update the relevant section — see the `capturing-lessons` skill. Directory-specific rules
belong in that directory's `CLAUDE.md`, not here.
