# Resume state — 2026-08-15

## Where the build is

**Phase 0 (Contracts), Phase 1 (Fixtures), and a working Phase 5-6 slice
(Retarget + Verify + Export) are done.** Branches: `feat/ar-contracts`
(merged into `feat/arvr-integration`), `feat/ar-datapipe` (new, pending
merge — 34/34 tests green on Linux).

- `packages/ar-contracts/` — all 7 contracts as frozen pydantic models.
  Coordinate convention documented in `docs/CONTRACTS.md`. 25/25 tests pass
  everywhere (pure Python, no native deps).
- `tools/make_fixtures.py` / `tools/mock_twin_server.py` — fixture pack +
  WebSocket mock TwinState stream, smoke-tested end-to-end.
- `packages/ar-datapipe/` — **new.** `normalize → retarget (Pinocchio IK) →
  verify (MuJoCo replay) → export (LeRobot-shaped parquet)`, entry point
  `run_episode()`. Targets a placeholder 6-DOF test arm
  (`fixtures/robot/test_arm.urdf`, NOT the real robot — no real URDF exists
  yet). One URDF drives both Pinocchio and MuJoCo so they can't silently
  disagree about the kinematic chain. 9 new tests, all green on Linux;
  end-to-end run on the actual fixture TEACH episode
  (`fixtures/ar-xr/sample_episode.*`) accepts with tracking error ~0.1mm.
  Full rationale + platform caveats in `packages/ar-datapipe/README.md`.
- Per user instruction: **MuJoCo is used for local verification instead of
  Isaac Sim** (not installed on the Spark yet, see below) — this is
  deliberately kinematic-only cross-checking, not scene-level physics, so
  it needs no GPU and runs in CI/WSL.
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
   `pyproject.toml` (`sys_platform == 'linux'` markers) — Pinocchio's
   Windows wheel coverage (via `cmeel`) is incomplete for some transitive
   deps as of `pin` 3.4-4.1, and building them needs a full MSVC+CMake
   toolchain. `uv sync` still succeeds on Windows for the rest of the
   workspace; `ar_datapipe` just isn't importable there (tests skip via
   `pytest.importorskip`, don't error).

## Blocked / not started

- **Isaac Sim download started but interrupted once** (network EOF
  mid-pull on a ~10GB layer), restarted — reusing cached layers, see
  "Spark workspace" below for current status. Per instruction: pull only,
  do not run it yet (GPU memory budget). Sections 14B-G (Isaac bridge, live
  Follow/Teach-replay integration) stay blocked until it's actually usable
  — this does not block anything else; Phase 2-9 all develop against
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

## Spark workspace

SSH access confirmed to `gn100-dd0e` (alias `spark` in `~/.ssh/config`,
dedicated key `~/.ssh/spark_ed25519`). Existing hack root:
`~/nvidia-spark-hacks` on the Spark (same repo, `origin/main`). Created,
additive only, nothing else touched:

```
~/nvidia-spark-hacks/ar-vr/sky/{worktrees,artifacts,logs,fixtures,scratch}/
~/nvidia-spark-hacks/ar-vr/andrew/{worktrees,artifacts,fixtures,scratch}/
```

**Isaac Sim 5.1.0** (`nvcr.io/nvidia/isaac-sim:5.1.0`, the exact image
`realsim/docker/sim.Dockerfile` already targets — reused rather than
reinvented) is being `docker pull`ed on the Spark in the background,
logging to `ar-vr/sky/logs/isaac_sim_pull.log`. Pull only — not run, per
instruction, to keep GPU memory free while other team members' containers
(`laguna-vllm`, etc.) are active. Check status with:

```bash
ssh spark 'tail -20 ~/nvidia-spark-hacks/ar-vr/sky/logs/isaac_sim_pull.log'
ssh spark 'docker images | grep isaac-sim'
```

Other team work on the Spark (`text-to-cad-plan.md`, `text-to-pcb-plan.md`,
`pcb-ai-old/`, running `laguna-vllm` container, uncommitted
`STRUCT_2.md`/`master-plan.md` changes in the shared checkout) was left
untouched throughout, per spec sections 10/87.

## Next

1. Get `feat/ar-datapipe` reviewed and merged into `feat/arvr-integration`.
2. Andrew starts Phase 2 (Place) against `fixtures/ar-xr/scene.json` + the
   mock twin server — needs real GLB assets first (`ASSETS_TODO.md`).
3. Confirm the Isaac Sim pull finished; do NOT run it until there's a
   deliberate decision to spend the GPU memory budget on it.
4. Real robot URDF, whenever F3/hardware has one — swap
   `ar_datapipe.robot_model.DEFAULT_URDF` and re-tune IK/joint-limit
   assumptions; the pipeline shape shouldn't need to change.
