# STRUCT F3 — V1: one video → 8 trainable cousin scenes

## Context

STRUCT feat 3 ("RL environment replication") needs a working first version. The
goal of F3 is not a digital twin of a scanned room — it is a *distribution* of
rooms. One phone video goes in; **eight physically valid, task-annotated
simulation scenes** come out, seven of them generated variants rather than
copies.

Why cousins and not twins: ACDC (Stanford) measured **90% vs 25% zero-shot
sim-to-real success** for policies trained on digital cousins vs exact twins. A
twin overfits to one room; a cousin distribution generalizes. That result is the
reason F3 exists, and V1 must prove the variation axis works.

**Scope, decided with the user:**

| Question | Answer |
|---|---|
| Scope | F3 only, end to end |
| Stops at | **8 validated cousin scenes** — no RL (§7g), no SmolVLA (§7h) |
| Location | New prototype at `/Users/advaithvecham/experiments_2/realsim/` |
| Existing `nurec_reconstruction` | **Not a dependency** — user reports it doesn't work well. Code fresh; use verified upstreams as reference |
| Substitute assets | **Generative 3D per swap** |
| Hardware | Fixture-first on the Mac; DGX Spark later over SSH |

## Prior art: what to reference, what to ignore

Exploration found four prototypes on this machine. Per the user's instruction,
none becomes a dependency.

- `~/nurec_reconstruction` — implements video→COLMAP→3DGRUT→USDZ and a Lambda
  GPU harness. **Reference only.** Worth reading `reconstruction/three_dgrut.py`
  for the correct `apps/colmap_3dgut_mcmc.yaml` invocation and
  `scripts/lambda_remote.sh` for the ssh/rsync pattern. Do not import it.
- `~/Documents/Studying Stuff/current_projects/simbiote` — GB10 hackathon repo;
  mine `GB10_MEMORY_BUDGET.md`, `SSD_LAYOUT.md`, `scripts/gb10/` for Spark facts.
- `experiments_2/gsplat_mujoco/splatsim` — hand-rolled MPS splat rasterizer
  written earlier this session. **Keep it — I was wrong to call it dead weight.**
  It is pure PyTorch with *no custom CUDA kernels*, which makes it the one splat
  trainer in this plan that **cannot fail for toolchain reasons on sm_121**. It
  serves double duty: the Mac dev path, and the bottom rung of the reconstruction
  fallback ladder. `ingest.py` and `sfm.py` port directly as stage functions;
  `rasterizer.py` becomes `backends/splat/torch_ref.py`.
- Verified upstream to actually build on: **`nv-tlabs/3dgrut`** (NVIDIA official,
  the reference NuRec path). Its README **documents our exact target build**:
  `docker buildx build --platform linux/arm64 --build-arg CUDA_VERSION=13.0.2`.
- **Reference repos (user-directed, 2026-08-14):**
  - `github.com/nerfstudio-project/gsplat` — the fallback splat trainer
    (JIT-compiles for any arch, no pinned CUDA); mine its rasterization API and
    training loop for `scan/reconstruct/backends.py`'s gsplat rung.
  - `github.com/NVIDIA-Omniverse/usd-convert-gsplat` — **official PLY → gsplat
    USD converter.** This de-risks the weakest fallback rung: gsplat → PLY →
    NuRec-style USD no longer needs a hand-rolled writer. Wire it as the
    export path in the gsplat backend and as the L2 fallback when 3DGRUT's
    exporter is entangled with its CUDA extensions.

## Two constraints that shape everything

**1. No Spark yet.** So V1 is **fixture-first**: one pre-reconstructed scene
committed as a fixture, and every stage after reconstruction developed and
tested on the Mac, CPU-only.

**2. GB10 is sm_121** — the experimental corner of every toolchain. Verified:
needs CUDA 13+; most ML wheels are cu12/x86 (`ImportError: libcudart.so.12`);
NVIDIA's guidance is the `nvcr.io/nvidia/pytorch` container over pip torch;
CUDA extensions need `TORCH_CUDA_ARCH_LIST` (use `"12.0"` first — `sm_120` PTX
JITs cleanly to sm_121; `"12.1"` may not be accepted by PyTorch's arch parser).

Two Isaac paths exist, not one: `nvcr.io/nvidia/isaac-sim:5.1.0` **is multi-arch
and runs on aarch64**, and NVIDIA's DGX Spark playbook additionally builds from
source (gcc-11, `LD_PRELOAD=libgomp.so.1`, ~50GB). Live streaming is unsupported
on aarch64 — headless offline render only, which suits us. cuRobo/SkillGen also
unsupported.

**Three traps specific to this machine:**
- **`torch.cuda.is_available()` lies here.** It returns True on a torch build
  with no sm_121 cubin; you find out at the first kernel launch with
  `no kernel image is available for execution on the device`. Every probe must do
  a **real matmul and check the result is finite.**
- **Unified memory OOM freezes the whole box**, not the container — you lose SSH.
  Cap every container (`--memory=96g --memory-swap=96g`), set `MAX_JOBS=8` on
  source builds (20 Arm cores running nvcc will exhaust 128GB), run `earlyoom`.
- **`docker save` every image that works, immediately.** On this platform a
  working image is a research artifact you may not be able to reproduce — tags
  move, wheels vanish. Pin by digest after first success.

**Design consequence — the most important decision here:**

> V1 is **simulator-dual**. Scenes emit **both** USD (Isaac Sim — the real
> target) and **MJCF** (MuJoCo — the validator). Physics gates run in **MuJoCo
> on CPU**, so the entire gate and cousin system is testable on the Mac today.
> Isaac becomes a rendering/RL target, not a blocking dependency.

This matches STRUCT §0's existing "MuJoCo stays the portable second opinion."

---

## Architecture

`uv` workspace, three packages, so the later monorepo migration is a `git mv`.
`envgen` must never `import scan` — they communicate through artifacts only.

```
experiments_2/realsim/
  pyproject.toml                    # uv workspace; members = packages/*
  packages/
    r2s-core/     src/r2s/core/     # schemas, provenance, store, gate runner. NO heavy deps.
    scan/         src/scan/         # capture → reconstruct → segment → assetize
    envgen/       src/envgen/       # shell → cousins → tasks → validate
  fixtures/tiny_room/               # committed: 24 frames, sparse model, 5k-gaussian PLY
  fixtures/assets/                  # committed: pre-generated substitute meshes
  tests/
```

### Stage contract

Every stage is a pure function over artifacts:

```python
stage(inputs: dict[str, Artifact], cfg, ctx: RunContext) -> (Artifact, GateReport)
```

No stage reads global state or mutates its input. This is what makes the
Mac↔Spark split free and resumability trivial.

### Core invariants (from STRUCT §1.1)

1. **Agents propose, the harness disposes.** An LLM may emit segmentation
   prompts, asset descriptions, placement priors, and task instructions. It never
   emits a verdict. Enforced structurally: **`r2s-core` has no LLM client
   dependency at all**, and gate modules cannot import a proposer.
2. **Provenance is mandatory.** No bare floats for physical constants — every one
   is a `Measured[T]` with `ValueProvenance ∈ {MEASURED, CONFIRMED, INFERRED,
   ASSUMED}`, defaulting to `ASSUMED`, with a validator that hard-fails
   `CONFIRMED`/`MEASURED` without a source. Assets carry
   `AssetProvenance ∈ {SCANNED, GENERATED, PROCEDURAL}`.
3. **Degradation is data, not an exception.** Every artifact carries
   `degradation: FULL | DEGRADED | STUB` = `max()` of its inputs'. Monotone
   downstream, cannot be laundered. This is how weak segmentation still ships 8
   labeled scenes instead of crashing.
4. **Stub-first.** Every heavy stage ships a stub backend on day one.
   `r2s run-all` must work end-to-end on the Mac before COLMAP is ever run.

### Artifacts

`capture.json` → `reconstruction.json` → `segmentation.json` → `asset_bundle/` →
`shell.json` → `scene.json` ×8 → `task.json` ×8, each with a `GateReport`.

Content-addressed store at `.r2s/cas/`, keyed on
`hash(input refs + config + stage_version + code version + seed)`. **Cache inputs,
never verify output hashes** — 3DGS training, CoACD, and PhysX are not bitwise
deterministic. Store interface is S3-shaped (`fsspec`), so MinIO later is a URL
change, and `r2s remote` sync is `rsync --ignore-existing` on immutable blobs.

### CLI

```bash
scan capture VIDEO [--lidar MESH]    # ffmpeg + sharpness filter + EXIF intrinsics
scan reconstruct --run ID [--backend 3dgrut|gsplat|stub]
scan segment --run ID [--prompts "mug . chair . laptop"] [--propose]
scan assetize --run ID [--hulls coacd|vhacd|convex]
envgen shell --run ID
envgen cousins --run ID -n 8 --seed 1337
envgen tasks --run ID
envgen validate --run ID [--sim mujoco|isaac]
r2s run-all VIDEO -n 8 | r2s doctor | r2s status | r2s gates explain ID | r2s remote ...
```

`--propose` is the **only** flag that permits an LLM call anywhere. CI never sets it.

---

## The novel part: cousin generation

### Four variation axes, in value order

**identity** (swap the asset) > **layout** (move surfaces, change counts) >
**task** (new target/predicate) > **placement** (pose jitter).

Placement alone is what §7(f) currently specifies, and it is the weakest axis —
8 scenes differing by 20cm of mug translation are one scene with noise.

### The 8-scene stratification

| ID | Identity | Layout | Task | Role |
|---|---|---|---|---|
| C0 | scanned | scanned | A | **TWIN** — pure splat render, eval anchor, F5 overlay |
| C1–C3 | generated variants | ≈ scanned | A | axis 1 |
| C4–C5 | generated | surfaces moved, ±1 object | A | axis 2 |
| C6 | resampled | resampled | **B** | axis 3, **held out** |
| C7 | **asset-disjoint from C0–C6** | new | **C** | full shift, **held out** |

Train on C0–C5, hold out C6/C7. Assert C7's asset-ID disjointness mechanically —
a leak is invisible in aggregate metrics and silently invalidates the eval.

**Per-episode randomization is not optional.** 8 static scenes is a tiny
distribution. Every scene exposes `randomize(episode_seed)` re-jittering poses
(±2cm, ±10°), friction (μ ∈ [0.4,1.0]), mass (±15%), and lighting. Most of the
generalization comes from *within*-scene randomization; shipping 8 frozen USDs
would undercut the premise.

### Asset generation (user's choice: generative 3D per swap)

Flagging the cost once, then building it as specified: generative 3D produces a
mesh in ~30s and then you re-pay the entire assetization bill — convex
decomposition, mass, friction, scale prior, canonical orientation — for every
asset, which retrieval libraries give away for free. This is the highest-risk
option and it is a deliberate choice.

The mitigation that makes it workable: **generated meshes go through the exact
same `scan/assetize` stage as scanned ones.** Assetize is representation-agnostic
— mesh in, hulls + inertia + USD out — so the generative path adds a producer,
not a parallel pipeline.

```
LLM proposes varied descriptors per category   ("a low wooden stool", "a metal bar stool")
        ↓                                       ← the ONLY LLM step
text/image → 3D  (Hunyuan3D 3.x or TRELLIS 2)
        ↓
      mesh.glb
        ↓
  scan/assetize  ← shared with SCANNED assets: CoACD hulls, trimesh inertia, USD + physics APIs
        ↓
   gated asset  → rejected assets fall back to PROCEDURAL primitives
```

- Backends behind one `AssetSource` protocol: `Hunyuan3DSource`, `TrellisSource`,
  `ProceduralSource` (parametric box/cylinder/mug primitives), `FixtureSource`
  (pre-generated, committed — this is what V1 develops against on the Mac).
- **TRELLIS → USD chain (user decision, 2026-08-14):** TRELLIS generates the
  mesh → **Omniverse asset converter** (`omni.kit.asset_converter`, run inside
  the `sim` container's Kit Python) converts GLB → USD → **an LLM proposes the
  PhysX properties** (mass, friction, material) for the converted prim.
  Invariant preserved: the LLM *proposes* — every proposed value lands as a
  `Measured[...]` with `Proposer(kind="llm")`, is clamped to the category's
  plausible band, and the same deterministic gates (`ASSET.MASS_RANGE`,
  inertia validity, settle) adjudicate it exactly as they do the density-prior
  path. Deterministic priors remain the fallback when no LLM is available.
- Generation is a **Spark/GPU stage**. On the Mac it reads `fixtures/assets/`.
- **Index-time filters, not gate-time.** Reject any asset the SO-101 cannot grasp
  (width, payload) *before* it enters the candidate pool — otherwise you generate
  5 candidates, reject all 5, and it looks like a generation bug.
- Uniform scaling only, clamped to `[0.5, 2.0]`. Anisotropic scaling breaks wall
  thickness, grasp geometry, and makes the inertia tensor a lie. Anchor on height
  for support providers, largest horizontal extent for manipulables.
- **Cache every generation on disk** keyed by `sha256(prompt + model + seed)`.
  LLM and diffusion calls are not reproducible even at temperature 0; without the
  cache, "same seed → same 8 scenes" is simply false.

### Composition: turn gates into generators

The highest-leverage design decision. A gate that only rejects gives a low
acceptance rate and a long debugging night; the same predicate used to constrain
the sampling domain gives near-100% acceptance.

- **Reachability becomes a polygon.** Offline, FK 200k random joint configs into
  a 2cm voxel occupancy grid (`so101_workspace.npz`). Slice it at the support
  height, convert to a `shapely` polygon, and **intersect it into the placement
  region before sampling**. Placements are reachable by construction.
- **LLM placement priors compile to regions**, not post-hoc constraints. `near` →
  annulus; `in_front_of` → 90° sector; `against_wall` → inward-buffered wall
  strip. `region = support_face ∩ (⋂ priors) ∩ reachable`. An empty intersection
  is a *named prior conflict*, not 64 mysterious failed samples.
- **Erode every support polygon by 2cm** (`poly.buffer(-0.02)`) — this single
  line prevents the sampler placing a mug 3mm from the table edge and burning
  retries on tip-overs the geometry should have prevented.
- Sample XY by area-weighted triangulation, not bbox rejection (thin L-shaped
  desktops loop near-forever). Orientation from `trimesh.poses.compute_stable_poses`
  weighted by stability probability — not "AABB min at face height," which
  happily rests a mug on its rim.
- Place in topological order of the `on`/`inside` DAG: supports before supportees.

**Robot base placement is a scene variable**, sampled and validated — the SO-101
has ~0.35m reach, so base pose determines whether *any* task is reachable.
Require ≥0.06m² of reachable area on the primary surface or resample the base.
Omitting this is the most likely way to get 8 scenes that all fail reachability.

### Tasks

Fixed predicate library — LLM composes, never extends: `on`, `near`, `inside`,
`grasped`, `pose_within`. AST with AND/OR/NOT, **max depth 2, max 3 leaves**.
Each predicate has a scalar (generation-time, trimesh) and a **batched torch**
(runtime) implementation — a Python loop over `num_envs` would dominate step time.

Entities bind by **USD prim path** (`/World/obj_003`), never by semantic label —
two mugs and the binding is silently ambiguous. Carry a separate `failure`
formula (e.g. `on(mug, FLOOR)`) for early termination.

`inside` is the fragile one — implement it, but gate it out of LLM composition in
V1 unless a container with a validated interior volume is present.

---

## The gates

A cousin ships only if all pass. **Run cheap → expensive, short-circuit, always
record which gate failed.** Putting the settle test first is worth ~2 orders of
magnitude in wall-clock and is the most common way to lose a day.

Order: `affordance` (µs) → `diversity` (µs) → `static physics` (ms) →
`reachability` (voxel lookup, then IK) → `task validity` (ms) →
**`observability`** (render) → `settle` (physics, seconds).

| # | Gate | Check |
|---|---|---|
| 1 | **Physical validity** | max penetration ≤1mm; every body supported within 2mm; settle 240 steps @ dt=1/120 → Δpos <5mm, Δrot <2°, terminal \|v\| <1e-3 |
| 2 | **Reachability** | grasp, pre-grasp, place, pre-place all IK-solvable, in joint limits, **collision-free** (a pose reachable through the tabletop is not reachable) |
| 3 | **Task validity** | success predicate **false** on the *post-settle* state; satisfiable **with a recorded witness** (sampled goal + collision-free + reachable + 3-waypoint path clear); goal ≥5cm from start |
| 4 | **Affordance preservation** | the substitute satisfies the same requirement set the scanned asset did for every predicate it participates in (`support_face`, `graspable_width`, `interior_volume`, …) |
| 5 | **Diversity floor** | `D = 0.40·(1−Jaccard(asset_ids)) + 0.10·(1−Jaccard(categories)) + 0.35·layout_chamfer + 0.15·task_distance ≥ 0.30` |
| 6 | **Observability** ⚠️ | target occupies ≥200px and ≥30% of its unoccluded silhouette in ≥1 camera at t=0 |

**Gate 6 was missing from the original five and closes a real hole:** a scene
where the mug is fully occluded by the monitor from every camera is physically
valid, reachable, task-valid, affordance-preserving, and diverse — and completely
untrainable for a vision policy. All five original gates pass; the scene is worthless.

Two silent-bug traps in gate 5:
- **Exclude the shell from the layout Chamfer.** Floor and walls are identical
  across all 8; include them and `D` saturates near 0, everything passes, and you
  ship 8 near-duplicates with a green dashboard.
- **Don't take top-1 substitutes.** Sample from top-K (K=5) across the 8 scenes,
  or the highest-value axis silently collapses into the weakest.

**Threshold 0.30 is a guess until calibrated.** Ship
`calibrate_diversity.py`: generate 50 candidates, plot the pairwise `D`
histogram, pick the valley. Budget 30 minutes.

### Retries and failure

Three nested loops with explicit budgets: placement resample (64) → scene
resample (8) → candidate resample (5) → **fail the run loudly**.

**Never ship 7 scenes silently.** On exhaustion, raise naming the gate with the
highest rejection count and dumping the top-5 rejection reasons with metrics.
Append-only `rejections.jsonl` plus an accounting assertion
`accepted + rejected == attempts` — no attempt may vanish.

**One relaxation rule: priors are negotiable, gates are not.** After scene-loop
exhaustion, drop the softest unsatisfied prior (logged by name). Never relax a
gate threshold, including "just to see if it works."

For every physical failure, dump a top-down render with the offending pair in
red. Debugging "gate 1 failed" without a picture costs more hours than writing
the renderer.

---

## Risks, ranked

| # | Risk | Mitigation |
|---|---|---|
| 0 | ⚠️ **Isaac Sim headless RTX cannot render NuRec on aarch64 GB10.** The one risk whose fallback costs *capability*, not time — there is no second implementation of Isaac Sim and you cannot patch a closed binary. NVIDIA's own docs carve out livestreaming on aarch64, which is evidence the aarch64 render path has known gaps | **Buy the answer in the first 3 hours of SSH access** (M4 Gate 1). If it fails: USD-only validation via pure `pxr` (schema, transforms, collision, physics) + visual checks through 3DGRUT's own playground renderer. Geometric gates survive; photoreal validation does not |
| 1 | **SO-101 is 5-DOF** (5 arm joints + gripper). Full SE(3) IK targets fail to converge nearly everywhere — you'll conclude scenes are unreachable when they're fine | Pose IK as position (3) + approach direction (2), roll free: project rotational error onto the plane orthogonal to the approach axis. **Verify against a hand-checked pose before building on it.** Single most likely way to lose a day |
| 2 | **Generative 3D re-pays full assetization** per asset (user's explicit choice) | Shared `assetize` stage; index-time graspability/mass filters; `ProceduralSource` fallback; disk-cached generations |
| 3 | **sm_121 / CUDA 13 / aarch64** — the experimental corner of every toolchain | `nvcr.io/nvidia/pytorch` base container; `TORCH_CUDA_ARCH_LIST=12.1`; gsplat fallback for 3DGRUT; MuJoCo gates so the Spark isn't blocking |
| 4 | **Metric scale.** SfM is scale-free; without it every mass, inertia, grasp width, and reach check is fiction | `RECON.SCALE_METRIC` is a HARD gate. Treat the iPhone LiDAR mesh as **required capture equipment**, not optional. `--assume-scale` escape stamps ASSUMED and forces DEGRADED downstream |
| 5 | **Grounded-SAM-2 as a repo is an aarch64 install trap** — its `_C` CUDA extension is a known build failure on sm_121 | Use `transformers` GroundingDINO + SAM2 (pure PyTorch) as **primary**, not fallback |
| 6 | **Stripping objects leaves holes and baked shadows** — the splat has lighting cooked in | Don't pretend to inpaint. Record `Hole` records honestly; bias substitute placement to the removed object's pose so the hole is occluded; gate it (`SCENE.HOLE_OCCLUSION`) |
| 7 | **Nondeterminism** — LLM/diffusion calls and PhysX are not reproducible | Disk-cache all model calls; **bake settled poses into the committed USD** so the *artifact* is deterministic even though the *process* isn't |
| 8 | **Kaolin wheels** are pinned to torch+CUDA combos unlikely to exist for CUDA 13/sm_121 | Write `r2s.metrics` (chamfer/IoU, ~50 lines of torch) **first, on the Mac**. Kaolin then becomes a speed optimization, never a blocker — and the preflight cross-checks Kaolin's output against the reference (a partially-built Kaolin imports fine and returns wrong numbers) |
| 9 | `python-fcl` (trimesh collision backend) is a common ARM wheel gap | Verify import at hour 0; fallback to trimesh broadphase + manual SAT |
| 10 | **`pycolmap` has no aarch64 Linux wheels** | Source build, or apt `colmap` + CLI driver, or permanently run SfM on the Mac (it's CPU-bound anyway and already works) |

**Verified low-risk** (aarch64 wheels confirmed to exist): `coacd`, `pin`
(Pinocchio), `trimesh`. Don't spend planning energy on these.

### Container strategy

Three images split on **ABI boundaries, not pipeline stages** — there are exactly
two irreconcilable Python universes (NGC PyTorch, and Isaac's bundled Kit Python
which must never be pip-mutated):

| Image | Base | Runs on |
|---|---|---|
| `recon-gpu` | `nvcr.io/nvidia/pytorch:<pinned>` | Spark only — torch, COLMAP, 3DGRUT, gsplat, SAM2, generative 3D |
| `tools-cpu` | `python:3.12-slim` (multi-arch) | **Mac and Spark** — trimesh, coacd, pinocchio, usd-core, mujoco, all gate logic |
| `sim` | `nvcr.io/nvidia/isaac-sim:5.1.0` + thin overlay | Spark only |

`tools-cpu` is the important one: **the same image on both machines**, holding
most of the pipeline. Interface between images is a single bind-mounted run
directory — no RPC, no compose networking. Each stage is a pure function of the
previous directory, which is exactly what makes blind authoring work.

Two cross-container traps: Isaac Sim 5.1 containers run **non-root** by default
(fix UID/GID sharing on the work mount, or it breaks at 2am), and every stage
must write small `thumbs/*.png` so results are reviewable without pulling
gigabytes over SSH.

---

## Milestones

Each leaves `r2s run-all` green.

| M | Where | Work |
|---|---|---|
| **M0** | Mac | `r2s-core` complete: schemas, `Measured`, CAS store, gate registry/runner, exit codes. All stages stubbed against `fixtures/tiny_room`. `r2s run-all -n 8` emits 8 STUB scenes + 8 STUB tasks with a full gate report. **This freezes the contract; everything after is swapping backends.** |
| **M1** | Mac | Real capture + SfM. Port `splatsim/ingest.py` + `sfm.py`. LiDAR ingest, sim(3) metric scale solve. |
| **M2** | Mac | Real segmentation (`transformers` GroundingDINO+SAM2 on MPS) + assetize (CoACD, trimesh inertia, USD authoring). All CPU/MPS. |
| **M3** | Mac | Shell, composition, tasks, all 6 gates, MuJoCo settle. **8 real cousin scenes — V1's deliverable is reachable here without the Spark.** |
| **M4** | Spark | **Bring-up, in inverted pipeline order** (see below). |
| **M5** | Spark | 3DGRUT splat training + generative 3D backends; full e2e run; `docker save` + digest pin. |

### M4 bring-up order — inverted on purpose

Cheap probes gate expensive builds, and **the failure with no fallback is bought
first**:

| Gate | Time | What | Why here |
|---|---|---|---|
| **0** | 10m | `preflight.sh --group host`: arch, `compute_cap` (expect 12.1), CUDA ≥13, docker+`--gpus`, ≥250GB disk, NGC login | baseline, no builds |
| **1** | 1–3h | **Isaac Sim headless renders NVIDIA's *own* NuRec USDZ sample → assert pixel variance > threshold** | ⚠️ **Do this before writing a line of reconstruction code.** It is the only dependency with no fallback that preserves the requirement, it is a closed binary you cannot patch, and nobody has published a NuRec-headless-on-GB10 result. Using NVIDIA's asset isolates "Isaac renders NuRec on aarch64" from "our export is valid." A booted Kit that renders black passes an exit-code check — assert on pixels. |
| **2** | 45m | NGC PyTorch: real matmul (not `is_available()`), then **compile a 10-line CUDA extension** with the candidate arch list | 3-minute proxy for a 4-hour build. Record the winning `TORCH_CUDA_ARCH_LIST` into `env.json`; everything downstream consumes it. **Do not start Gate 5 until this is green.** |
| **3** | 15m | `tools-cpu` image runs the full CPU pipeline on the T0 fixture on the Spark | confirms nothing broke crossing to aarch64 Linux |
| **4** | 1–2h | COLMAP build (**no aarch64 pycolmap wheels** — source or apt) | if it fights >2h, run SfM on the Mac permanently and move on |
| **5** | 2–4h | 3DGRUT arm64 build + 200-iter smoke train + USDZ export | biggest build; `MAX_JOBS=8`, compile in the last layer, buildx cache |
| **6** | 30m | **Our** USDZ renders in Isaac | the true integration gate, cheap *because* 1 and 5 passed independently. Fails here ⇒ bug is provably ours |

`preflight.sh` is the highest-value file in the project — write it on the Mac,
blind, before SSH exists. `r2s doctor` re-runs it as JSON and diffs against the
last known-good, so when something that worked yesterday breaks today (a
background DGX OS driver update), you know in 10 seconds.

Sequencing note: **build the LLM proposer last.** Validate the whole pipeline
against hand-written priors first. If the LLM never lands you still ship 8
validated cousins — you just wrote the priors yourself. Build it first and a
schema bug blocks everything downstream.

## Verification

```bash
# M0 — contract frozen, runs on the Mac with zero heavy deps
uv run r2s run-all fixtures/tiny_room/orbit.mp4 -n 8 --backend stub
uv run pytest tests/ -v          # schema round-trip, per-gate pass+fail fixtures,
                                 # degradation monotonicity, seed reproducibility

# M3 — the actual V1 deliverable
uv run r2s run-all data/room.mp4 --lidar data/room.glb -n 8 --seed 1337
uv run r2s gates report --run latest        # 8 scenes, all 6 gates, per-scene table
uv run r2s status --run latest              # stage cache table
ls .r2s/runs/latest/scenes/                 # 8 × {scene.usda, scene.xml, task.json}

# determinism — must be byte-identical
uv run r2s run-all data/room.mp4 -n 8 --seed 1337 --out runA
uv run r2s run-all data/room.mp4 -n 8 --seed 1337 --out runB
diff -r runA/scenes runB/scenes

# M5 — on the Spark
ssh spark 'cd realsim && uv run r2s doctor'
ssh spark 'cd realsim && uv run envgen validate --run latest --sim isaac'
```

**Definition of done for V1:** `r2s run-all` on a real phone video emits exactly
8 scenes, each with a `task.json` and a passing 6-gate report, each loading in
MuJoCo (and in Isaac once the Spark lands), with one TWIN, two held-out scenes,
mechanically-asserted asset disjointness for C7, and a calibrated diversity
threshold — reproducible byte-for-byte from a seed.
