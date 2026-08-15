# realsim — working agreements for Claude Code sessions

One phone video → 8 validated digital-cousin sim scenes + 1 photoreal eval twin,
targeting NVIDIA DGX Spark (GB10, sm_121, aarch64, CUDA 13).

**Read first, in order:** `STATE.md` (current build position + open bugs) →
`docs/PLAN.md` (canonical v2 architecture, milestones M6–M11).

## Commands

```bash
make loop        # THE verdict: ruff + 156 tests + full fixture pipeline. Green = working.
make run         # fixture pipeline only (r2s run-all fixtures/tiny_room --fixture -n 8 --seed 1337)
make run-cold    # same, cache disabled
uv run r2s gates                     # list all registered gates
uv run r2s gates --explain <ID>      # one gate's contract + remedy
uv run r2s status --run tiny_room-s1337
./spark.sh doctor|push|build|run|pull   # remote harness (needs `spark` in ~/.ssh/config)
```

## Invariants — do not violate, do not "temporarily" relax

1. **Agents propose, the harness disposes.** LLMs/models may propose (prompts, layouts,
   asset shapes, physics values); only deterministic code returns verdicts. Enforced
   structurally: `r2s-core` must NEVER gain an LLM-client or torch dependency.
2. **No bare floats for physical constants.** Everything is `Measured[T]` with
   provenance (MEASURED|CONFIRMED|INFERRED|ASSUMED). The validator hard-fails
   CONFIRMED/MEASURED without a source. Don't fight it; it's the point.
3. **Degradation is monotone.** An artifact is never cleaner than its inputs.
   Weak results ship LABELED, they don't crash — and they don't get laundered.
4. **`envgen` never imports `scan`.** Packages communicate through artifacts only.
5. **Gates are never relaxed to make a run pass.** Priors/placement constraints are
   negotiable; gate thresholds are not. Fix the code or the fixture, not the gate.
6. **Never ship N−1 scenes silently.** Generation failure raises loudly, naming the
   worst gate. `accepted + rejected == attempts` is asserted.
7. **Determinism via seed tree + caches.** All randomness through `ctx.rng(scope...)`.
   Model calls (LLM/diffusion) are disk-cached by content hash — the cache IS the
   reproducibility. Settled poses are BAKED into artifacts (physics isn't bit-stable).
8. **Never trust an exporter's flag.** Verify the file on disk (open the USD, load the
   mesh). Exit code 0 is not evidence.

## Architecture in one breath

Deconstruction (`scan/`): video → frames → COLMAP poses → depth-consensus scale
(Depth Pro, INFERRED, spread-gated) → 3DGUT splat → gravity → OWLv2+SAM2 object
specs + SpatialLM→RANSAC-refit room layout. Ends at SPECS, not edited pixels.
Recreation (`envgen/`): roomgen extrudes a clean empty USD room (albedo from
plane-inlier gaussian colors) → TRELLIS/procedural similar-asset pools → stratified
cousin composition (constructive sampling: eroded support polygons ∩ reach annulus)
→ MuJoCo settle-and-bake → diversity floor → tasks with witness proofs → validate.
The untouched splat = eval-only twin (NuRec on Spark; Gate 1 buys that answer first).

## Gotchas that already cost time (don't rediscover them)

- `torch.cuda.is_available()` lies on GB10 — only a real kernel launch proves CUDA
  (spark/probes/b3). Never `pip install torch` into the NGC container.
- trimesh v5: `mesh.export(file_type=...)`; `trimesh.proximity` needs `rtree`.
- Scale-solve residuals must be scored against the mesh SURFACE, not sampled points.
- Generate-stage rescale anchors: HEIGHT for furniture, horizontal max for graspables.
- Fixtures place at scanned bottom height (`aabb_m.lo[2]`), not z=0.
- MuJoCo settle: measure penetration AFTER first-contact resolution (open M6 bug —
  see STATE.md for the two ranked fixes).
- Unified memory on the Spark: cap containers `--memory=96g`, `MAX_JOBS=8`, or the
  whole box freezes including SSH.
- `.r2s/` is the local artifact store — gitignored, safe to delete, rebuilt by cache.

## Where things are

`packages/r2s-core` contracts+gates+store · `packages/scan` deconstruction ·
`packages/envgen` recreation · `fixtures/tiny_room` deterministic synthetic room
(regen: `uv run python tools/make_fixture.py`) · `spark/` preflight+probes ·
`docker/` 3 images split on ABI boundaries · `tests/` 156 passing.

## Current front (from PLAN v2 §5)

M6: fix cousins settle bug → M7: `scan/reconstruct/depth_anchor.py` →
M8: `envgen/roomgen/` (extrusion + ransac_only backend + gates) →
M9: SpatialLM ensemble backend + cluttered-wardrobe fixture →
M10: Spark bring-up (Gate 1 = NuRec sample render FIRST; `ssh-copy-id` prerequisite).
