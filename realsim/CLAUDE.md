# realsim — implementation plan

## Goal

Take one phone video of a room and produce 8 physically-valid, task-annotated
simulation scenes (digital "cousins", not copies) plus 1 photoreal evaluation
twin. Target hardware is an NVIDIA DGX Spark (GB10, sm_121, aarch64, CUDA 13);
development happens on a Mac against a synthetic fixture.

`docs/PLAN.md` holds the full architecture and is the source of truth. This file
is the build plan derived from it.

## Status

Nothing is implemented. This is a from-scratch build in an empty tree.

Note when reading `docs/PLAN.md` §5: its milestone table is written as a delta
on a previous implementation that no longer exists. Items phrased as "fix the
open bug" or "port from the shell stage" refer to code that must be written, not
found. The build below covers both the base pipeline and the newer additions.

## Architecture

Two packages that never import each other, communicating only through artifacts
on disk:

**Deconstruction** (`scan/`) — video to specifications, never edited pixels:
frames → camera poses → metric scale → gaussian splat → gravity alignment →
object specs → room-structure spec.

**Recreation** (`envgen/`) — specifications to worlds, never reads pixels:
extrude a clean empty room → generate substitute assets → compose scenes →
settle physics → verify diversity → attach tasks → validate.

Stage contract, uniformly:

```python
stage(inputs: dict[str, Artifact], cfg, ctx: RunContext) -> (Artifact, GateReport)
```

Pure function, no global state, no mutation of inputs. This is what makes the
pipeline resumable, cacheable, and splittable across machines.

## Design rules

These are load-bearing; violating them causes bugs that pass tests.

1. **Models propose, deterministic code disposes.** Learned components may
   suggest prompts, layouts, asset shapes and physical values. Only
   deterministic code returns a verdict. Enforce structurally — the core package
   must never gain a model-client or torch dependency — and assert it in a test.
2. **No bare floats for physical constants.** Every measurement carries its
   provenance (MEASURED / CONFIRMED / INFERRED / ASSUMED) and fails validation
   if it claims more than it can support.
3. **Degradation is monotone and must reach the exit code.** An artifact is
   never cleaner than its inputs. Weak results ship labeled rather than
   crashing — but a fully-stubbed run that exits 0 makes the mechanism useless.
4. **Never relax a gate to make a run pass.** Sampling priors are negotiable;
   thresholds are not. Fix the code or the fixture.
5. **A gate must be able to fail.** The trap is a gate whose acceptance band is
   its own producer's clamp, or one reading a value nothing compares against —
   both report success forever. Write the failing input before the passing one.
6. **Fail loudly on partial output.** Never emit N−1 scenes silently; assert
   `accepted + rejected == attempts`.
7. **Determinism through a seed tree and content-addressed caches.** All
   randomness flows through one seeded generator per scope. Model calls are
   cached by content hash. Physics results are baked into artifacts because
   simulation is not bit-stable.
8. **Verify outputs, don't trust writers.** Reopen files and count what is in
   them. An exit code of 0 is not evidence, and neither is a written image —
   check the pixels.

## Build order

Each phase ends with the full pipeline runnable and green.

**Phase 1 — Contracts.** Schemas, provenance types, content-addressed store,
gate registry and runner, exit codes, run context with the seed tree. Every
downstream stage stubbed. Deliverable: the whole pipeline runs end-to-end on a
fixture producing stub artifacts with a complete gate report. This freezes the
contract; everything after is swapping backends.

**Phase 2 — Fixture and capture.** A deterministic synthetic room with known
ground truth (dimensions, colors, object placements), so fits are checked
against numbers rather than eyeballed. Frame extraction, sharpness filtering,
camera intrinsics.

**Phase 3 — Geometry.** Camera poses, metric scale with a consensus check,
gaussian splat reconstruction, gravity alignment. Scale is the highest-risk
item: without it, every mass, reach and grasp width downstream is fiction.

**Phase 4 — Understanding.** Object detection and segmentation lifted to 3D;
room layout as planes, spans and a closed footprint with per-wall provenance.

**Phase 5 — Room construction.** Extrude the layout into a clean empty room —
floor slab, walls clipped to the footprint, per-surface color from evidence —
emitted as a standard USD scene plus a physics-engine mirror. Acceptance gates:
evidence coverage per wall, footprint closure, agreement with the scan.

**Phase 6 — Assets and composition.** Substitute asset generation through a
shared assetization path (collision decomposition, inertia, filtering), then
stratified scene composition by constructive sampling, physics settling, and a
diversity floor.

**Phase 7 — Tasks and validation.** A closed predicate library with witness
proofs, then cold-load re-verification and rendering.

**Phase 8 — Target hardware.** Bring-up on the Spark, cheapest probes first, and
the highest-risk unknown bought before anything depends on it.

## Verification

One command runs the whole pipeline on the fixture and **its exit code is the
verdict** (0 pass, 10 degraded but shipping, 20+ broken). Build that command in
Phase 1 and keep it green. Alongside it: unit tests per gate with both passing
and failing inputs, and a byte-identical reproduction check from a fixed seed.

Look at the output. Render what gets built and inspect it — a stage emitting
geometry nobody ever renders is how invisible-output bugs survive.

## Environment

Available locally: `ffmpeg`, `usd-core`, `mujoco`, `trimesh` 5, `shapely`,
`cv2`, `coacd`, `plyfile`, `scipy`, and USD command-line tools including a
headless renderer.

Not available locally: `torch`, `colmap` / `pycolmap`, and the GPU-only
generation and rendering stacks. **A real video therefore cannot run end-to-end
on this machine.** Develop fixture-first and keep every heavy stage behind a
stub backend so the pipeline works before any model is installed.

## Known pitfalls

- CUDA availability checks can report success on a build with no kernels for
  this GPU; only a real kernel launch returning a finite result proves it.
- `trimesh` v5: use `mesh.export(file_type=...)`; proximity queries need
  `rtree`; stable-pose computation needs `networkx` and degrades silently
  without it.
- Score scale-solve residuals against mesh surfaces, not sampled points.
- When rescaling, anchor support furniture on height and graspable objects on
  their largest horizontal extent.
- Measure interpenetration over the settled window, not as a maximum across the
  whole trajectory — the latter reports solver transients. Raising the spawn
  offset makes this worse, not better.
- Light intensities are not free parameters; a wrong value renders every surface
  pure white and still exits 0.
- On unified-memory hardware, cap container memory and build parallelism or an
  out-of-memory event takes down the whole machine including SSH.
- A preflight that counts a skipped check as a pass will exit 0 having tested
  nothing.
