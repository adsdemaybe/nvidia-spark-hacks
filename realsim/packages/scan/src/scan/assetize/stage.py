"""Stage 4: assetize. Segmented clusters (+ later: generated meshes) -> asset bundle.

Representation-agnostic on purpose: a SCANNED cluster, a GENERATED glb, and a
PROCEDURAL primitive all flow through the same mesh -> hulls -> physics -> faces
path. The generative source is a producer, not a parallel pipeline.
"""
from __future__ import annotations

import numpy as np
import trimesh
from pydantic import BaseModel

from r2s.core import (
    AssetProvenance,
    Degradation,
    DegradationLevel,
    Proposer,
    inferred,
)
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import (
    AssetBundlePayload,
    AssetPayload,
    CollisionSummary,
    SegmentationPayload,
)

from ..reconstruct.ply import load_ply
from .geometry import canonicalize, mesh_from_points, min_antipodal_width, stable_poses, support_faces
from .hulls import decompose
from .physics import infer_physics, inertia_valid

SPEC = StageSpec(
    name="assetize",
    version="1",
    artifact_type="asset_bundle",
    payload_model=AssetBundlePayload,
    relevant_packages=["r2s-core", "scan", "trimesh", "coacd"],
)

SO101_GRIPPER_MAX_M = 0.08
SO101_PAYLOAD_KG = 0.25


class AssetizeConfig(BaseModel):
    max_hulls: int = 16
    coacd_threshold: float = 0.05
    min_mesh_triangles: int = 4


def assetize_mesh(
    mesh: trimesh.Trimesh,
    *,
    asset_id: str,
    label: str,
    category: str,
    provenance: AssetProvenance,
    proposer: Proposer,
    cfg: AssetizeConfig,
    ctx: RunContext,
    descriptor: str = "",
    source_object_id: str | None = None,
    gaussians_ref=None,
) -> tuple[AssetPayload | None, str | None]:
    """Mesh -> gated AssetPayload. Returns (payload, drop_reason)."""
    rng = ctx.rng("assetize", asset_id)

    if len(mesh.faces) < cfg.min_mesh_triangles:
        return None, f"mesh has {len(mesh.faces)} triangles (<{cfg.min_mesh_triangles})"
    mesh, _ = canonicalize(mesh)
    ext = mesh.extents
    if float(min(ext)) <= 1e-4 or abs(float(mesh.convex_hull.volume)) <= 1e-6:
        return None, "degenerate extents/volume"

    hulls = decompose(mesh, max_hulls=cfg.max_hulls, threshold=cfg.coacd_threshold)
    physics = infer_physics(mesh, category)
    ok, why = inertia_valid(physics)
    if not ok:
        return None, f"inertia invalid: {why}"

    poses, probs = stable_poses(mesh)
    faces = support_faces(mesh)
    width = min_antipodal_width(mesh, rng)
    fits = width is not None and width <= SO101_GRIPPER_MAX_M \
        and physics.mass.value <= SO101_PAYLOAD_KG

    glb = mesh.export(file_type="glb")
    visual_ref = ctx.store.put_bytes(
        glb if isinstance(glb, bytes) else glb.encode(), "model/gltf-binary"
    )
    collision_refs = []
    for h in hulls.hulls:
        obj = h.export(file_type="obj")
        collision_refs.append(ctx.store.put_bytes(
            obj.encode() if isinstance(obj, str) else obj, "model/obj"
        ))

    payload = AssetPayload(
        asset_id=asset_id,
        label=label,
        category=category,
        provenance=provenance,
        proposer=proposer,
        descriptor=descriptor,
        source_object_id=source_object_id,
        bbox_m=(float(ext[0]), float(ext[1]), float(ext[2])),
        visual_mesh_ref=visual_ref,
        gaussians_ref=gaussians_ref,
        collision_refs=collision_refs,
        collision=CollisionSummary(
            method=hulls.method,  # type: ignore[arg-type]
            n_hulls=len(hulls.hulls),
            total_vertices=int(sum(len(h.vertices) for h in hulls.hulls)),
            volume_ratio=float(hulls.volume_ratio),
            watertight=all(h.is_watertight for h in hulls.hulls),
            max_hull_vertices=int(max(len(h.vertices) for h in hulls.hulls)),
        ),
        stable_poses=poses,
        stable_pose_probs=probs,
        support_faces=faces,
        physics=physics,
        graspable_width_m=inferred(width, "m", method="horizontal_caliper_min")
        if width is not None else None,
        fits_so101_gripper=fits,
        degradation=DegradationLevel.FULL if hulls.method == "coacd"
        else DegradationLevel.DEGRADED,
    )
    return payload, None


def run(inputs: dict, cfg: AssetizeConfig, ctx: RunContext):
    seg = inputs["segment"]
    sp: SegmentationPayload = seg.payload
    recon = inputs["reconstruct"]
    scratch = ctx.scratch("assetize")
    degs: list[Degradation] = []

    ply_path = scratch / "cloud.ply"
    ctx.store.fetch(recon.payload.splat.ply_ref, ply_path)
    cloud = load_ply(ply_path)

    assets: dict[str, AssetPayload] = {}
    dropped: dict[str, str] = {}
    for obj in sp.objects:
        idx = np.frombuffer(
            ctx.store.get_bytes(obj.gaussian_indices_ref), dtype=np.int32
        )
        pts = cloud.means[idx]
        try:
            mesh = mesh_from_points(pts)
        except BaseException as exc:
            dropped[obj.object_id] = f"meshify failed: {exc}"
            continue
        payload, drop = assetize_mesh(
            mesh,
            asset_id=obj.object_id,
            label=obj.label,
            category=obj.label,
            provenance=AssetProvenance.SCANNED,
            proposer=obj.proposer,
            cfg=cfg,
            ctx=ctx,
            source_object_id=obj.object_id,
            gaussians_ref=obj.gaussian_indices_ref,
        )
        if payload is None:
            dropped[obj.object_id] = drop or "unknown"
        else:
            assets[payload.asset_id] = payload

    if dropped:
        degs.append(Degradation(
            code="ASSET.DROPPED", level=DegradationLevel.DEGRADED, stage="assetize",
            message=f"{len(dropped)} object(s) dropped: {dropped}",
            remediation="inspect the listed reasons; every drop is intentional and visible",
        ))
    non_coacd = [a.asset_id for a in assets.values() if a.collision.method != "coacd"]
    if non_coacd:
        degs.append(Degradation(
            code="ASSET.HULL_FALLBACK", level=DegradationLevel.DEGRADED, stage="assetize",
            message=f"collision below coacd quality for: {non_coacd}",
            remediation="install/build coacd; convex-hull collision loses concavity (mug cavities!)",
        ))

    payload = AssetBundlePayload(
        segmentation_ref=None,
        asset_ids=sorted(assets),
        assets=assets,
        dropped=dropped,
    )
    return payload, "trimesh", degs
