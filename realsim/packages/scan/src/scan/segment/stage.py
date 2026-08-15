"""Stage 3: segment. reconstruction -> labeled 3D object clusters.

Two backends:
  fixture  -- cluster indices come from the fixture's clusters.json; everything
              GEOMETRIC (AABB, OBB, centroid, volume, exclusivity, floor test)
              is computed for real from the metric cloud. Only the 2D part is
              stubbed.
  hf       -- OWLv2+SAM2 detection on frames, projection lift. Spark.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from r2s.core import Degradation, DegradationLevel, Pose, Proposer, inferred
from r2s.core.geometry import AABB, OBB
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import (
    PromptSpec,
    ReconstructionPayload,
    Segment3D,
    SegmentationPayload,
)

from ..reconstruct.ply import load_ply

SPEC = StageSpec(
    name="segment",
    version="1",
    artifact_type="segmentation",
    payload_model=SegmentationPayload,
    relevant_packages=["r2s-core", "scan", "numpy"],
)

DEFAULT_VOCAB = "table . chair . monitor . keyboard . mug . laptop . bottle . box . plant . lamp"


class SegmentConfig(BaseModel):
    backend: str = "fixture"  # fixture | hf
    fixture_dir: str | None = None
    prompts: str = DEFAULT_VOCAB
    propose: bool = False  # the ONLY flag that may trigger an LLM call
    min_gaussians: int = 30


def _cluster_geometry(means: np.ndarray, idx: np.ndarray) -> tuple[AABB, OBB, tuple, float]:
    pts = means[idx]
    lo, hi = pts.min(0), pts.max(0)
    aabb = AABB(lo=tuple(map(float, lo)), hi=tuple(map(float, hi)))
    centroid = tuple(float(v) for v in pts.mean(0))
    # OBB via PCA in the horizontal plane (z stays up: objects sit on surfaces)
    xy = pts[:, :2] - pts[:, :2].mean(0)
    if len(xy) >= 3:
        cov = np.cov(xy.T)
        evals, evecs = np.linalg.eigh(cov)
        major = evecs[:, np.argmax(evals)]
        yaw = float(np.arctan2(major[1], major[0]))
    else:
        yaw = 0.0
    rot = np.array([np.cos(yaw / 2), 0.0, 0.0, np.sin(yaw / 2)])
    c, s = np.cos(-yaw), np.sin(-yaw)
    R2 = np.array([[c, -s], [s, c]])
    loc = xy @ R2.T
    ext2 = loc.max(0) - loc.min(0) if len(loc) else np.zeros(2)
    obb = OBB(
        pose=Pose(position=centroid, quaternion=tuple(map(float, rot))),
        extents=(float(ext2[0]), float(ext2[1]), float(hi[2] - lo[2])),
    )
    # convex-ish volume estimate: voxel occupancy at 1cm
    vox = np.unique((pts / 0.01).astype(np.int64), axis=0)
    volume = float(len(vox)) * 1e-6
    return aabb, obb, centroid, volume


def run(inputs: dict, cfg: SegmentConfig, ctx: RunContext):
    recon = inputs["reconstruct"]
    rp: ReconstructionPayload = recon.payload
    scratch = ctx.scratch("segment")
    degs: list[Degradation] = []

    ply_path = scratch / "cloud.ply"
    ctx.store.fetch(rp.splat.ply_ref, ply_path)
    cloud = load_ply(ply_path)
    means = cloud.means

    proposer = Proposer(kind="default", detail="builtin_vocab")
    prompts = [PromptSpec(text=cfg.prompts, proposer=proposer)]

    if cfg.backend == "fixture":
        clusters, lift_method = _fixture_clusters(cfg, degs)
    elif cfg.backend == "hf":
        clusters, lift_method = _hf_clusters(cfg, rp, means, scratch, ctx, degs)
    else:
        raise ValueError(f"unknown segment backend {cfg.backend!r}")

    # ---- exclusivity resolution (deterministic, order-stable) ------------
    owner = {}
    conflicts = 0
    for oid in sorted(clusters):
        for g in clusters[oid]["indices"]:
            if g in owner:
                conflicts += 1
            else:
                owner[int(g)] = oid
    resolved: dict[str, np.ndarray] = {}
    for oid in sorted(clusters):
        resolved[oid] = np.array(
            [g for g, o in owner.items() if o == oid], dtype=np.int32
        )

    objects: list[Segment3D] = []
    assigned = 0
    for oid in sorted(resolved):
        idx = resolved[oid]
        if len(idx) < cfg.min_gaussians:
            continue
        aabb, obb, centroid, volume = _cluster_geometry(means, idx)
        idx_ref = ctx.store.put_bytes(idx.tobytes(), "application/x-npy-int32")
        meta = clusters[oid]
        objects.append(Segment3D(
            object_id=oid,
            label=meta["label"],
            prompt_term=meta.get("term", meta["label"]),
            proposer=Proposer(kind=meta.get("proposer", "deterministic"), detail=lift_method),
            gaussian_indices_ref=idx_ref,
            n_gaussians=len(idx),
            n_views_supporting=int(meta.get("views", 8)),
            n_views_total=int(meta.get("views_total", 8)),
            vote_ratio=float(meta.get("vote_ratio", 1.0)),
            aabb_m=aabb,
            obb_m=obb,
            centroid_m=centroid,
            volume_m3=inferred(volume, "m^3", method="voxel_occupancy_1cm"),
            is_movable_guess=bool(meta.get("movable", True)),
        ))
        assigned += len(idx)

    if not objects:
        degs.append(Degradation(
            code="SEG.NO_OBJECTS", level=DegradationLevel.DEGRADED, stage="segment",
            message="zero objects segmented; shell will be the whole cloud and "
                    "cousins will be fully GENERATED/PROCEDURAL",
            remediation="check prompts; check the capture actually contains objects",
        ))

    payload = SegmentationPayload(
        reconstruction_ref=_ref_of(recon),
        prompts=prompts,
        detector="fixture" if cfg.backend == "fixture" else "google/owlv2-base-patch16-ensemble",
        segmenter="fixture" if cfg.backend == "fixture" else "facebook/sam2.1-hiera-small",
        lift_method=lift_method,  # type: ignore[arg-type]
        objects=objects,
        n_gaussians_assigned=assigned,
        n_gaussians_total=len(means),
        assignment_conflicts=conflicts,
    )
    return payload, cfg.backend, degs


def _fixture_clusters(cfg: SegmentConfig, degs) -> tuple[dict, str]:
    if not cfg.fixture_dir:
        raise ValueError("fixture backend needs fixture_dir")
    p = Path(cfg.fixture_dir) / "clusters.json"
    data = json.loads(p.read_text())
    degs.append(Degradation(
        code="SEG.FIXTURE_MASKS", level=DegradationLevel.STUB, stage="segment",
        message="2D detection stubbed by fixture clusters; 3D geometry is real",
        remediation="run backend=hf on the Spark",
    ))
    return {k: v for k, v in data.items()}, "fixture"


def _hf_clusters(cfg, rp, means, scratch, ctx, degs) -> tuple[dict, str]:
    """Spark path: frames + poses -> detections -> masks -> vote lift.
    Requires camera poses in the sparse model; wired when COLMAP output lands."""
    from .detect2d import detect_and_segment  # noqa: F401
    from .lift3d import lift_by_vote  # noqa: F401

    raise NotImplementedError(
        "hf backend needs the COLMAP pose export from the Spark run; "
        "see scan/reconstruct/sfm_colmap.py -- poses land there first"
    )


def _ref_of(art):
    from r2s.core import ArtifactRef

    return ArtifactRef(uri=f"cas://sha256/{art.header.artifact_id}",
                       sha256=art.header.artifact_id, size_bytes=0,
                       media_type="application/json")
