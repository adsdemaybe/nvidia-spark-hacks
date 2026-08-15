# Resume state — 2026-08-14

## Where the build is

Full pipeline implemented end-to-end (no stubs left except GPU backends):
capture → reconstruct → segment → assetize → generate → shell → cousins →
tasks → validate, all behind `run_stage` (CAS cache, gates, degradation).
CLI: `uv run r2s run-all fixtures/tiny_room --fixture -n 8 --seed 1337`.
Validation loop: `make loop` (ruff + pytest + fixture pipeline; exit code is
the verdict).

- Unit tests: **156/156 pass** (`uv run pytest tests/ -q`).
- Fixture: `fixtures/tiny_room/` — 36,300-gaussian synthetic room, deterministic
  (seed 20260814), regenerate with `uv run python tools/make_fixture.py`.
- Pipeline stages PASSING on fixture: capture, reconstruct (after two loop-caught
  fixes below), segment, assetize, generate, shell.

## The bug I was mid-fix on (cousins stage)

`GenerationFailure: stratum c1 ... worst gate: settle` — rejections show
**pen=6.9mm constant** across retries (threshold 2mm), dT small, no explosion.

Diagnosis (unverified): objects spawn at `surf_h + 1e-3` on the SCANNED table's
**convex-hull** collision surface, whose top face carries the fixture's ±3mm
gaussian noise plus hull-facet bumps — initial penetration ~7mm is geometric,
not dynamic. Two candidate fixes, in order:
1. Spawn epsilon 5mm (compose.py `sample_placement`, z offset) — cheap, try first.
2. Measure `max_penetration_m` only after ~10 settle steps (mjcf.py `settle`),
   so first-contact resolution doesn't count as violation. Justifiable: the
   gate's intent is "no persistent interpenetration", not "no initial contact
   depth".
Also consider: settle threshold `settle_max_translation_m=0.05` is fine;
rotation 10.6° rejections were the mug rolling — stable-pose sampling may pick
the mug's side-lying pose; check `stable_pose_probs` weighting.

Earlier loop-caught fixes (done, keep):
- sim(3) scoring now measures distance to mesh SURFACE via
  `trimesh.proximity` (needs `rtree`, added to deps) — RMSE 33mm → 6.6mm.
- generate-stage rescale anchor bug: support furniture now anchors on HEIGHT
  (was horizontal max → 1.22m-tall tables → everything fell to the floor).
- fixtures place at scanned bottom height (`aabb_m.lo[2]`), not z=0.
- trimesh 5 API: `mesh.export(file_type=...)`, not `export_mesh(mesh, ...)`.

## After cousins goes green

1. tasks + validate stages have never executed (blocked behind cousins) —
   expect first-run bugs in `_witness_goal` / `_materialize_meshes` (the hull
   path recovery in validate/stage.py is the weakest code in the repo).
2. `make loop` green end-to-end, then `make run-cold` (cache-off) + determinism
   check (two runs, diff scene JSONs; settled poses are baked so artifacts
   should match byte-for-byte given the MuJoCo version is fixed... verify, may
   need tolerance on settled poses across machines — the *same* machine must
   be exact).
3. `git init` + first commit + remote (spark.sh push needs a remote).
4. User-directed additions (recorded in plan): gsplat repo as reference for the
   gsplat backend rung; **NVIDIA-Omniverse/usd-convert-gsplat** as the official
   PLY→USD converter — wire into `scan/reconstruct/backends.py` gsplat rung
   and drop the hand-rolled-NuRec-writer risk note.
5. TRELLIS → omni.kit.asset_converter → LLM-PhysX chain: adapter exists
   (`scan/generate/trellis_usd.py`), Spark-only, unwired into GenerateConfig
   source list (add "trellis" to docstring choices; select_source already
   routes it).

## Spark bring-up (unchanged, waiting on SSH)

`spark/preflight.sh` groups A–D; Gate 1 = Isaac renders NVIDIA's own NuRec
sample (`NUREC_SAMPLE=... ./spark/preflight.sh --group sim`) — run BEFORE any
reconstruction work. `./spark.sh push|build|run|pull|doctor` once a `spark`
host exists in ~/.ssh/config. Images: docker/{recon-gpu,tools-cpu,sim}.Dockerfile.

## Task list mapping

Tasks #7/#8 (stubs, tests) are done in effect — #8 by the test subagent,
#7 superseded by full implementations. #12 is the open one (cousins bug above).
