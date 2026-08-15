# Resume state — 2026-08-15

## Where the build is

**Phase 0 (Contracts), Phase 1 (Fixtures), a working Phase 5-6 slice
(Retarget + Verify + Export), and the Episodes/Scenes API surface are
done.** Branches: `feat/ar-contracts` + `feat/ar-datapipe` merged into
`feat/arvr-integration`; `feat/ar-backend` new, pending merge — 41/41 tests
green on Linux, 25 passed + 2 skipped on Windows (as designed).

- `packages/ar-contracts/` — all 7 contracts as frozen pydantic models.
  Coordinate convention documented in `docs/CONTRACTS.md`. 25/25 tests pass
  everywhere (pure Python, no native deps).
- `tools/make_fixtures.py` / `tools/mock_twin_server.py` — fixture pack +
  WebSocket mock TwinState stream, smoke-tested end-to-end.
- `packages/ar-datapipe/` — `normalize → retarget (Pinocchio IK) → verify
  (MuJoCo replay) → export (LeRobot-shaped parquet)`, entry point
  `run_episode()`. Targets a placeholder 6-DOF test arm
  (`fixtures/robot/test_arm.urdf`, NOT the real robot — no real URDF exists
  yet). One URDF drives both Pinocchio and MuJoCo so they can't silently
  disagree about the kinematic chain. 9 tests, all green on Linux;
  end-to-end run on the actual fixture TEACH episode
  (`fixtures/ar-xr/sample_episode.*`) accepts with tracking error ~0.1mm.
  Full rationale + platform caveats in `packages/ar-datapipe/README.md`.
- `packages/ar-backend/` — **new.** FastAPI Episodes API (spec section 36:
  create → upload artifact → finish → status) + Scenes API (section 37),
  wrapping `ar_datapipe.run_episode`. `/finish` runs the pipeline
  synchronously (fast enough at demo scale, no task queue needed). Verified
  two ways: `TestClient` unit tests (7 of them) AND a real running
  `uvicorn` process hit with `curl` end-to-end (create → upload 105 frames
  → finish → accepted, tracking error ~0.1mm) — same on both Windows and
  Linux for the parts that don't need Pinocchio/MuJoCo (`/scenes/*`).
- Per user instruction: **MuJoCo is used for local verification instead of
  Isaac Sim** (not installed/run on the Spark yet, see below) — deliberately
  kinematic-only cross-checking, not scene-level physics, so it needs no
  GPU and runs in CI/WSL.
- `ruff check` clean, `uv sync` clean on both Windows and Linux.

## Judgment calls made without spec guidance (flag for review)

1. **Follow-mode forward vector = local +X** (ROS REP-103 convention) —
   see `packages/ar-contracts/.../follow.py`.
2. **Quaternion unit-norm tolerance = 1e-2** — the spec's own `SpatialFrame`
   example (section 29) has norm 0.997697, rounded for readability.
3. **`VerificationResult` shape is derived, not literal** — spec names it
   (13A) but gives no JSON example.
4. **Repo placement**: top-level `arvr/`, sibling to `realsim/`. Confirmed
   with Sky before scaffolding.
5. **`sample_episode` is JSONL, not Parquet** for the AR/XR fixture pack —
   but the *datapipe's LeRobot export* does write real Parquet
   (`packages/ar-datapipe/src/ar_datapipe/export.py`), just not validated
   against the actual `lerobot` package (heavy torch/gymnasium dep avoided).
6. **Placeholder test-arm URDF** (`fixtures/robot/test_arm.urdf`) — made up,
   6 revolute joints, ~0.95m reach, sized to cover the fixture episode's
   workspace. Delete once F3/hardware produces a real robot URDF.
7. **IK step clamping + angle wrapping** (`retarget.py`) — an early
   unclamped CLIK implementation "converged" to a kinematically valid but
   nonsensical 48-radian joint solution (spiraled through several full
   turns before settling); fixed with a per-iteration step-norm clamp and
   wrapping the final answer into `(-pi, pi]`.
8. **`pin`/`mujoco`/`pyarrow` gated to Linux** in `ar-datapipe`'s
   `pyproject.toml` (`sys_platform == 'linux'` markers), AND each of their
   module-level imports is wrapped in `try/except ImportError` with the
   real error deferred to first use (`IkSolver.__init__`,
   `MujocoReplay.__init__`, `export_episode`) — otherwise `import
   ar_datapipe` (and transitively `import ar_backend`, since it wraps the
   pipeline) would hard-fail on Windows even for code paths that don't
   need them, like the Scenes API. Caught this by actually trying to boot
   `ar_backend` on Windows, not by inspection.
9. **`/xr/episodes/{id}/artifact` takes a JSON body**, not a Parquet file
   upload as literally suggested by spec section 36 — consistent with the
   JSONL-vs-Parquet call already made for the fixture pack.

## Blocked / not started

- **Isaac Sim 5.1.0 finished downloading** (`nvcr.io/nvidia/isaac-sim:5.1.0`,
  30GB) after one retry (a network EOF interrupted the first attempt
  mid-layer). **Not run** — pull only, per instruction, to keep GPU memory
  free while other team members' containers (`laguna-vllm`, etc.) are
  active. Sections 14B-G (Isaac bridge, live Follow/Teach-replay
  integration) stay blocked until there's a deliberate decision to actually
  run it — this does not block anything else; Phase 2-9 all develop against
  fixtures/mocks/MuJoCo per the spec's own sequencing.
- `packages/isaac-bridge/`, `packages/xr-web/`, `apps/ios/` — directories
  created, empty. Isaac bridge is Sky/SSH-only (blocked above). xr-web and
  the iOS app are Andrew's, need not touch the Spark at all.
- `fixtures/ar-xr/{table,cube,bin,robot}.glb` — not fabricated, see
  `fixtures/ar-xr/ASSETS_TODO.md`.
- LeRobot export has not been round-tripped through the actual `lerobot`
  package (see caveat above / `export.py` docstring).
- Dynamic (PD-controlled) replay — `verify.py` is kinematic-only by design
  for now; real dynamics need a real robot's mass/gain values.
- Twin/Follow/Correction streaming endpoints (spec sections 38-40) — not
  wired into `ar_backend` yet. Twin has a standalone equivalent already
  (`tools/mock_twin_server.py`); folding everything into one FastAPI app is
  a reasonable next step once a client needs both REST and streams from the
  same port.

## Spark workspace

SSH access confirmed to `gn100-dd0e` (alias `spark` in `~/.ssh/config`,
dedicated key `~/.ssh/spark_ed25519`). Existing hack root:
`~/nvidia-spark-hacks` on the Spark (same repo, `origin/main`). Created,
additive only, nothing else touched:

```
~/nvidia-spark-hacks/ar-vr/sky/{worktrees,artifacts,logs,fixtures,scratch}/
~/nvidia-spark-hacks/ar-vr/andrew/{worktrees,artifacts,fixtures,scratch}/
```

`ar-vr/sky/worktrees/ar-datapipe/` holds an isolated git worktree (spec
section 11) used to validate `feat/ar-datapipe` on real aarch64 hardware —
`uv sync` + `pytest` both clean there, matching WSL x86_64 results exactly.

Other team work on the Spark (`text-to-cad-plan.md`, `text-to-pcb-plan.md`,
`pcb-ai-old/`, running `laguna-vllm` container, uncommitted
`STRUCT_2.md`/`master-plan.md` changes in the shared checkout) was left
untouched throughout, per spec sections 10/87.

## Next

1. Get `feat/ar-backend` reviewed and merged into `feat/arvr-integration`.
2. Andrew starts Phase 2 (Place) against `fixtures/ar-xr/scene.json` + the
   mock twin server — needs real GLB assets first (`ASSETS_TODO.md`).
3. Do NOT run the Isaac Sim container until there's a deliberate decision
   to spend the GPU memory budget on it.
4. Real robot URDF, whenever F3/hardware has one — swap
   `ar_datapipe.robot_model.DEFAULT_URDF` and re-tune IK/joint-limit
   assumptions; the pipeline shape shouldn't need to change.
5. Twin/Follow/Correction endpoints in `ar_backend`, once a client
   actually needs them behind the same port as the Episodes/Scenes API.
