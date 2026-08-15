# Resume state — 2026-08-15

## Where the build is

**Phase 0 (Contract Freeze) and Phase 1 (Fixtures) are done.** Branch:
`feat/ar-contracts`, not yet merged into `feat/arvr-integration` (that
integration branch doesn't exist yet — create it once this branch's
acceptance gate is reviewed, per spec section 7).

- `packages/ar-contracts/` — all 7 contracts implemented as frozen pydantic
  models: `SpatialFrame`, `SpatialEpisode`, `TwinState`, `FollowState`,
  `CorrectionEvent`, `SceneManifest`, `VerificationResult`. Coordinate
  convention documented in `docs/CONTRACTS.md`.
- 25/25 tests pass (`uv run pytest tests/ -q`): schema validation against
  the spec's literal JSON examples, unit-quaternion rejection, NaN/Inf
  rejection, negative-timestamp rejection, episode event ordering,
  UUID validation, immutability, unknown-schema-version rejection,
  extra-field rejection, VerificationResult's accept/reject invariants,
  and the follow-target calculation (section 22).
- `tools/make_fixtures.py` generates the fixture pack deterministically
  (seed 1337) — `fixtures/ar-xr/{scene.json, fake_twin_state.jsonl,
  sample_follow.jsonl, sample_correction.json, sample_episode.{json,jsonl}}`.
- `tools/mock_twin_server.py` streams schema-valid `TwinState` over
  WebSocket (`ws://host:8765/twin/<scene_id>`); smoke-tested end-to-end
  with a real client connection, not just unit-tested.
- `ruff check` clean, `uv sync` clean (pydantic 2.13, no heavy deps).

## Judgment calls made without spec guidance (flag for review)

1. **Follow-mode forward vector = local +X** (ROS REP-103 convention). The
   spec's follow-target formula (section 22) needs a "forward" definition
   it never gives. See `follow.py` docstring.
2. **Quaternion unit-norm tolerance = 1e-2**, not something tighter. The
   spec's own `SpatialFrame` example (section 29) has norm 0.997697 —
   rounded for readability — so a tight tolerance would reject the spec's
   own example payload.
3. **`VerificationResult` shape is derived, not literal.** Spec section 13A
   names it but gives no JSON example. Built from the `GET /xr/episodes`
   response shape (section 36) + the demo acceptance checklist (section 75).
4. **Repo placement**: new top-level `arvr/` folder, sibling to `realsim/`,
   not the bare root-level `packages/`/`apps/` paths the spec's prose
   literally shows. Confirmed with Sky (the user) before scaffolding.
5. **`sample_episode` is JSONL, not Parquet** (spec section 35 wants
   Parquet for high-rate pose). Fixture generator has no pyarrow dependency
   yet; a real Phase 4 recording pipeline should write Parquet, reusing
   `realsim`'s r2s-core Parquet path as a reference rather than reinventing.

## Blocked / not started

- **Isaac Sim is not installed on the Spark** (`gn100-dd0e` / GB10, checked
  2026-08-15: no `isaac*` paths, no `nvcc`, no `uv`). Sections 14B-G
  (Isaac bridge, live Follow/Teach-replay integration) cannot start until
  Isaac Sim + CUDA toolchain are provisioned. This does not block Phase 2-9
  (Place/Teach/Follow/Twin/Correct all develop against fixtures + the mock
  twin server per spec's own sequencing).
- `packages/isaac-bridge/`, `packages/xr-web/`, `apps/ios/` — directories
  created, empty. Isaac bridge is Sky/SSH-only (blocked above). xr-web and
  the iOS app are Andrew's, need not touch the Spark at all.
- `fixtures/ar-xr/{table,cube,bin,robot}.glb` — not fabricated, see
  `fixtures/ar-xr/ASSETS_TODO.md`.
- Datapipe (normalize/retarget/verify/export, spec section 13C-F) — not
  started. Needs a robot URDF (from F3/environment or a placeholder) and
  Pinocchio before IK retargeting can be real; premature to stub without one.

## Spark workspace (bring-up done)

SSH access confirmed to `gn100-dd0e` (alias `spark` in `~/.ssh/config`,
dedicated key `~/.ssh/spark_ed25519`). Existing hack root:
`~/nvidia-spark-hacks` on the Spark (same repo, `origin/main`). Created,
additive only, nothing else touched:

```
~/nvidia-spark-hacks/ar-vr/sky/{worktrees,artifacts,logs,fixtures,scratch}/
~/nvidia-spark-hacks/ar-vr/andrew/{worktrees,artifacts,fixtures,scratch}/
```

Other team work on the Spark (`text-to-cad-plan.md`, `text-to-pcb-plan.md`,
running `laguna-vllm` container, uncommitted `STRUCT_2.md`/`master-plan.md`
changes in the shared checkout) was left untouched, per spec sections 10/87.

## Next

1. Get this branch reviewed, then create `feat/arvr-integration` and merge.
2. Andrew starts Phase 2 (Place) against `fixtures/ar-xr/scene.json` + the
   mock twin server — needs real GLB assets first (`ASSETS_TODO.md`).
3. Sky: provision Isaac Sim on the Spark (separate from this branch's
   scope) before attempting Phase 11 (real Isaac integration).
4. Datapipe/retarget (Phase 5) once a robot URDF exists.
