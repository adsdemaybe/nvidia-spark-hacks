# realsim V2 — one phone video → 8 trainable cousin scenes + 1 photoreal eval twin

**Supersedes PLAN v1** (base-room-first pivot, LiDAR-less scale, ensemble roomgen).
Decisions dated 2026-08-14/15 with the user. STATE.md tracks build position.

## 0. What changed since v1, and why

| v1 | v2 | Driver |
|---|---|---|
| Splat shell (objects stripped) is the scene's visual base; holes occluded by placement bias | **Base-room-first**: a clean, empty, parametric room is *derived* from evidence; nothing is ever cut out of pixels | Holes + baked shadows are unanswerable by editing; plane/layout *parameters* have no holes |
| LiDAR mesh required for metric scale | **Depth-consensus** (Depth Pro) is the default anchor; LiDAR is rung 1 when present | Capture UX: one video, no second app. Consensus gate keeps it honest |
| RANSAC-only room fitting | **Ensemble roomgen**: SpatialLM topology → RANSAC per-wall refinement → deterministic gates | PolyLayout ablation: RANSAC-only layout on cluttered real scans ≈26% IoU vs ≈92% learned. Prior fills occlusion; geometry sharpens; gates police |
| NuRec renders every scene | NuRec renders **only the eval twin**; training scenes use ordinary RTX on clean USD | Quarantines the #1 unverified risk (NuRec headless on GB10) from "blocks everything" to "blocks one scene". Hybrid mode kept as config for unmoved layouts if Gate 1 passes |
| Scanned splat crops appear in training scenes | Training scenes are **fully synthetic**: generated/procedural similars in the generated room | ACDC: training on the twin is *worse* (25% vs 90%); twin demoted to eval-only |

Unchanged from v1: the invariant (**agents propose, the harness disposes** — enforced
structurally: `r2s-core` has no LLM client dep), `Measured[T]` provenance, monotone
degradation, CAS store + resume, gate registry, the cousin stratification, constructive
sampling, MuJoCo settle-and-bake, closed predicate library, retry budgets + rejection
ledger, simulator-dual output (USD + MJCF), the Spark bring-up order.

## 1. Architecture: deconstruction → recreation

```
DECONSTRUCTION  (scan/ package: video → specs; ends at descriptions, not things)
  [1] capture      ffmpeg + Laplacian filter        → frames, intrinsics prior
  [2] poses        COLMAP (VGGT rung later)         → camera poses + sparse pts (scale-free)
  [3] scale        ★ depth-consensus (Depth Pro)    → metres, INFERRED, spread-gated
  [4] splat        3DGUT (gsplat+usd-convert-gsplat → gaussian cloud
                    fallback)
  [5] gravity      RANSAC floor → z-up @ 0          → THE MEASUREMENT INSTRUMENT
  [6] identify     OWLv2+SAM2 + depth-tested vote   → ① OBJECT SPECS (category, dims,
                                                        pose, support relations)
  [7] layout       ★ SpatialLM → RANSAC refit →     → ② STRUCTURE SPEC (walls/doors/
                    gates (ensemble)                    footprint/colors, per-wall provenance)
                   untouched splat                  → ③ EVAL REFERENCE (the twin)

RECREATION      (envgen/ package: specs → worlds; never reads pixels)
  [8] roomgen      ② → extrude → clean empty USD room, albedo from plane-inlier colors
  [9] generate     ① → TRELLIS → omni.kit.asset_converter → LLM-PhysX (clamped) →
                    CoACD/trimesh assetize → SO-101-filtered pools   [scan/ package —
                    shares assetize; envgen still never imports scan]
  [10] cousins     stratified compose (constructive sampling) → MuJoCo settle-bake →
                    diversity floor → 8 scenes (c0–c5 train, c6–c7 heldout, c7 disjoint)
  [11] tasks       closed predicates + witness + not-presatisfied + non-trivial
  [12] validate    cold-load MuJoCo re-check; Isaac RTX render; twin via NuRec (Spark)
```

## 2. The two new deconstruction stages, fully specified

### [3] Scale: depth-consensus (`scan/reconstruct/depth_anchor.py`)

Ladder (best available wins, provenance recorded):
1. LiDAR mesh supplied → sim(3) ICP, **MEASURED**, gate `RECON.SCALE_RESIDUAL` rmse≤3cm.
2. **Depth consensus (new default)**: Depth Pro on ~20 orbit-spread frames; per frame,
   median ratio of predicted metric depth to COLMAP sparse depth at the same pixels
   (indoor band 0.2–12m); global scale = median of per-frame ratios. **INFERRED**,
   `method="depth_consensus[depth_pro]"`. Gate `RECON.SCALE_CONSENSUS` (HARD):
   `std/mean over frames ≤ 0.05` — 20 views must independently agree or the number
   is refused. Model behind a `DepthModel` protocol: `depth_pro | metric3d | stub`
   (stub = fixture ground-truth depth maps, so the consensus math is fixture-tested).
3. Known-object anchor (A4/ArUco) → MEASURED, coarse.
4. `assume_scale` → ASSUMED + DEGRADED stamped downstream (existing behavior).

Second/third jobs of the same weights: depth-tested lift in [6] (a gaussian votes only
if its projected depth ≈ predicted depth — closes the flagged V1 visibility gap and
earns `lift_method="projection_depthtest_majority"`), and an INFO cross-check of
predicted depth range vs reconstructed room extent.

Accepted cost: 2–8% scale error (vs <1% LiDAR). Touches masses ∝scale³ (~within
density-prior noise), reach (±1.7cm at 0.35m — absorbed by the 2cm polygon erosion),
grasp widths (±4mm on 8cm). Every artifact says INFERRED; LiDAR auto-wins when present.

### [7] Layout: the roomgen ensemble (`envgen/roomgen/`)

Three layers, each doing the only thing it is good at:

```
L1 TOPOLOGY   (learned, global, fills occlusion)
   SpatialLM-1.1-Qwen-0.5B reads the metric cloud → wall graph, doors, windows,
   object boxes — including structure behind furniture, from its 54k-room prior.
L2 PRECISION  (geometric, local, millimetres)
   Per proposed wall: gaussians within ±8cm → RANSAC + least-squares refit.
   Snaps ~5cm proposals onto ~5mm measurement. Too few inliers (occluded wall):
   keep L1 params, stamp that wall INFERRED. Per-wall provenance in the artifact.
L3 ACCEPTANCE (deterministic gates)
   ROOM.EVIDENCE_COVERAGE  HARD  observable span of each wall has inlier support
                                  (hallucinated walls contradicting evidence die)
   ROOM.CLOSURE            HARD  walls form a closed footprint polygon
   ROOM.FITS_SCAN          HARD  chamfer(shell gaussians → room mesh) ≤ 5cm
   ROOM.HEIGHT_SANE        SOFT  2.0–5.0 m
   ROOM.OBJECT_XCHECK      SOFT  SpatialLM object boxes ⨯ OWLv2 clusters agree
                                  (IoU-matched); disagreement = flagged degradation
```

Backend ladder: `spatiallm_ransac` (primary) → `ransac_only` (fallback, DEGRADED —
the v1 shell-stage plane fitter, demoted) → `stub`. Roomgen proper then extrudes the
accepted layout: footprint → floor slab; walls clipped to footprint up to ceiling
height (p99 z); albedo per plane = median color of its inlier gaussians; output
`room.usda` (UsdGeom meshes + UsdPhysics colliders + UsdLux rig) and the room's MJCF
mirror. Doors/windows become openings in V2.1; ignored for tabletop tasks now.

Appearance upgrades stay quarantined: diffusion re-texture (Matterport-Defurnish
style) is eval-twin-only, labeled, never in the physics path. AI fills appearance,
never structure.

### Schema deltas

- `ScaleSolution`: `method` += `"depth_consensus"`; new `spread: float | None`.
- New artifact `room_layout` (`r2s/core/schemas/layout.py`): `WallSpec{plane, span,
  provenance MEASURED|INFERRED, inlier_stats}`, `OpeningSpec{kind: door|window, wall_id,
  rect}`, `footprint`, `height`, `object_boxes`, `backend`, per-gate evidence.
- `ShellPayload` retires as the primary structure artifact (kept for the twin's
  gaussian bookkeeping); `scene.usda` references `room.usda` instead of a NuRec shell.
- `ScenePayload.render_mode` += `"synthetic"` (new default for all training scenes);
  `"hybrid_splat_mesh"` retained behind config for unmoved layouts if NuRec Gate 1 passes.

## 3. Technology stack (locked)

| Stage | Primary | Fallback rungs |
|---|---|---|
| decode/filter | ffmpeg, OpenCV Laplacian | — |
| poses | COLMAP | CPU-SIFT → Mac-side SfM → VGGT (Spark rung, robustness) |
| scale | **Depth Pro** consensus | LiDAR sim(3) (auto-wins if supplied) → known-object → assumed |
| splat | 3DGUT (3DGRUT repo) | gsplat + **usd-convert-gsplat** (official PLY→USD) |
| objects | OWLv2 + SAM2 (transformers, pure PyTorch) | manual prompts → fixture |
| layout | **SpatialLM** + RANSAC refit | ransac_only (DEGRADED) |
| room build | roomgen (shapely/trimesh extrusion) | — |
| asset gen | **TRELLIS → omni.kit.asset_converter → LLM-PhysX (clamped)** | Hunyuan3D → fixture → procedural |
| collision | CoACD | convex hull → OBB (recorded) |
| physics oracle | MuJoCo settle-and-bake | — (CPU, both machines) |
| compose | shapely region intersection, PCG64 seed tree | — |
| scene format | OpenUSD + UsdPhysics schemas (+ MJCF mirror) | — |
| training render | Isaac Sim 5.1 RTX + Replicator randomization | — |
| eval twin render | **NuRec** (quarantined risk; Spark Gate 1 first) | USD-only validation + 3DGRUT playground renderer |
| RL (V2 scope) | Isaac Lab; LeRobot/SmolVLA | MJX second opinion |

Model weights to stage on the Spark: Depth Pro (~1.9GB), SpatialLM-1.1-Qwen-0.5B
(~1GB), OWLv2+SAM2 (~1.5GB), TRELLIS (~5GB+). All but TRELLIS run on the Mac (MPS)
for fixture development.

## 4. Where AI proposes vs where determinism disposes

| AI proposes | Deterministic harness disposes |
|---|---|
| metric scale (Depth Pro) | 5% cross-frame consensus gate |
| room topology incl. occluded walls (SpatialLM) | inlier refit, evidence coverage, closure, chamfer |
| object labels/masks (OWLv2/SAM2) | vote ratios, depth test, size bands, floor test |
| asset geometry (TRELLIS) | assetize gates, SO-101 pool filter |
| PhysX properties (LLM) | category band clamps, inertia validity, settle |
| (later) placement priors, instructions | region intersection, predicate proofs |

## 5. Implementation milestones (V2 delta on the working V1 tree)

| M | Where | Work | Gate to green |
|---|---|---|---|
| **M6** | Mac | Fix open cousins settle bug (7mm hull-contact penetration — spawn epsilon 5mm, then measure penetration after step 10; see STATE.md) | `make loop` fully green incl. tasks+validate |
| **M7** | Mac | `depth_anchor.py` + DepthModel protocol + stub depth maps in fixture + `RECON.SCALE_CONSENSUS` + depth-tested lift | fixture runs LiDAR-less end-to-end |
| **M8** | Mac | `envgen/roomgen/`: layout schema, `ransac_only` backend (port from shell stage), extrusion + `room.usda`/MJCF, L3 gates, `render_mode="synthetic"` wiring in cousins/usda | cousins compose into the generated room |
| **M9** | Mac | `spatiallm_ransac` backend (HF weights, MPS), cluttered-fixture test (wall 70% occluded by wardrobe: ransac rung degrades, ensemble passes — the objection as a test) | ensemble beats ransac_only on the fixture |
| **M10** | Spark | Bring-up gates 0–6 unchanged (Gate 1 = NuRec sample render FIRST) + stage model weights + TRELLIS chain + real video end-to-end | 8 scenes + twin from a real room |
| **M11** | Spark | Depth Pro/SpatialLM/VGGT at speed; hybrid render_mode decision on Gate-1 result; Isaac Lab RL entry (V2 scope boundary) | — |

## 6. Risks (v2 ranking)

| # | Risk | Mitigation |
|---|---|---|
| 0 | NuRec headless on GB10 (unchanged #1 unknown) | Quarantined to the twin; Gate 1 buys the answer in hour one; USD-only fallback |
| 1 | SpatialLM quality on *our* clouds (trained on MASt3R-SLAM clouds; ours are splat means) | Point-density/noise domain gap is real: L2 refit + L3 gates bound the damage; ransac_only rung ships DEGRADED; cluttered fixture measures it |
| 2 | Depth-consensus systematic bias (consistent-but-wrong scale passes the spread gate) | Known-object spot check in capture guidance; LiDAR auto-wins when present; INFERRED provenance keeps it visible |
| 3 | TRELLIS sim-readiness per asset (unchanged from v1) | Same assetize path + pool filters + procedural rung |
| 4 | sm_121/aarch64 toolchain (unchanged) | Containers split on ABI, preflight probes, gsplat rung |
| 5 | SO-101 5-DOF IK (unchanged, V2 scope) | reach-envelope now; position+approach IK later |

## 7. Verification

```bash
make loop                         # ruff + 156 tests + fixture pipeline, exit code = verdict
uv run r2s run-all fixtures/tiny_room --fixture -n 8 --seed 1337   # no --lidar: depth-consensus path
uv run r2s gates explain ROOM.FITS_SCAN
diff -r runA runB                 # same seed → byte-identical scenes
# Spark, after ssh-copy-id:
./spark.sh doctor && NUREC_SAMPLE=... ./spark/preflight.sh --group sim   # Gate 1 before anything
```

**Definition of done (V2):** a real phone video, no LiDAR, produces 8 gated synthetic
cousin scenes (train/heldout split, c7 asset-disjoint) + 1 NuRec eval twin, each scene
with a proven task, reproducible from a seed, on the DGX Spark.
