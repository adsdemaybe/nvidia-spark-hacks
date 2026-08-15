"""Stage 6: shell. reconstruction + segmentation -> reusable blank room.

Strips object gaussians, fits walls (RANSAC on the vertical bands), extracts
the room footprint polygon and the floor as a placeable surface, and records
the holes the removed objects leave. No inpainting -- holes are data, and the
cousin composer occludes them by construction.

envgen deliberately does not import scan: the gaussian PLY is re-read here with
a local minimal reader. The 30 duplicated lines are the price of a real package
boundary, and they are worth it.
"""
from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from r2s.core import ArtifactRef, Degradation, DegradationLevel, Plane, inferred
from r2s.core.geometry import AABB, SupportSurface
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import Hole, ReconstructionPayload, SegmentationPayload, ShellPayload

SPEC = StageSpec(
    name="shell",
    version="1",
    artifact_type="shell",
    payload_model=ShellPayload,
    relevant_packages=["r2s-core", "envgen", "numpy", "shapely"],
)


class ShellConfig(BaseModel):
    wall_dist_thresh: float = 0.03
    min_wall_inliers: int = 200
    floor_margin: float = 0.05  # erosion of the floor polygon (edge margin)


def _read_means(store, ref: ArtifactRef, scratch) -> np.ndarray:
    from plyfile import PlyData

    p = scratch / "shell_cloud.ply"
    store.fetch(ref, p)
    v = PlyData.read(str(p))["vertex"]
    return np.stack([np.asarray(v[c], dtype=np.float32) for c in ("x", "y", "z")], axis=-1)


def _fit_walls(pts: np.ndarray, rng: np.random.Generator, cfg: ShellConfig) -> list[Plane]:
    """RANSAC vertical planes, greedily removing inliers, up to 6 walls."""
    remaining = pts[pts[:, 2] > 0.15]  # ignore the floor band
    walls: list[Plane] = []
    for _ in range(6):
        if len(remaining) < cfg.min_wall_inliers:
            break
        best = None
        for _ in range(200):
            i = rng.choice(len(remaining), 2, replace=False)
            p0, p1 = remaining[i]
            d = p1[:2] - p0[:2]
            n2 = np.array([-d[1], d[0]])
            norm = np.linalg.norm(n2)
            if norm < 1e-9:
                continue
            n = np.array([n2[0] / norm, n2[1] / norm, 0.0])
            off = float(n[:2] @ p0[:2])
            dist = np.abs(remaining[:, :2] @ n[:2] - off)
            inl = dist < cfg.wall_dist_thresh
            if best is None or inl.sum() > best[2].sum():
                best = (n, off, inl)
        if best is None or best[2].sum() < cfg.min_wall_inliers:
            break
        n, off, inl = best
        resid = np.abs(remaining[inl][:, :2] @ n[:2] - off)
        walls.append(Plane(
            normal=tuple(float(v) for v in n), offset=off,
            inlier_count=int(inl.sum()),
            inlier_ratio=float(inl.mean()),
            rmse_m=float(np.sqrt((resid**2).mean())),
        ))
        remaining = remaining[~inl]
    return walls


def run(inputs: dict, cfg: ShellConfig, ctx: RunContext):
    import shapely.geometry as sg

    recon = inputs["reconstruct"]
    seg = inputs["segment"]
    rp: ReconstructionPayload = recon.payload
    sp: SegmentationPayload = seg.payload
    scratch = ctx.scratch("shell")
    rng = ctx.rng("shell")
    degs: list[Degradation] = []

    means = _read_means(ctx.store, rp.splat.ply_ref, scratch)
    n_total = len(means)

    # strip object gaussians
    obj_idx = np.zeros(n_total, dtype=bool)
    holes: list[Hole] = []
    for o in sp.objects:
        if o.gaussian_indices_ref is None:
            continue
        idx = np.frombuffer(ctx.store.get_bytes(o.gaussian_indices_ref), dtype=np.int32)
        obj_idx[idx] = True
        holes.append(Hole(
            source_object_id=o.object_id, label=o.label,
            aabb_m=o.aabb_m, n_gaussians_removed=len(idx),
        ))
    shell_pts = means[~obj_idx]

    if len(shell_pts) < 100:
        raise RuntimeError("shell has almost no gaussians left; segmentation ate the room")

    # floor comes from reconstruction (already gravity-aligned, z=0)
    floor = rp.floor_plane or Plane(normal=(0, 0, 1), offset=0.0)

    walls = _fit_walls(shell_pts, rng, cfg)
    if len(walls) < 3:
        degs.append(Degradation(
            code="SHELL.FEW_WALLS", level=DegradationLevel.DEGRADED, stage="shell",
            message=f"only {len(walls)} wall(s) fitted; footprint from point hull instead",
            remediation="a 2-wall corner scan still works; capture more of the room to improve",
        ))

    # room footprint: 2D convex hull of near-floor points, eroded
    low = shell_pts[shell_pts[:, 2] < 0.3][:, :2]
    if len(low) < 10:
        low = shell_pts[:, :2]
    hull = sg.MultiPoint([tuple(p) for p in low[
        rng.choice(len(low), min(len(low), 2000), replace=False)
    ]]).convex_hull
    room_poly = hull.buffer(-cfg.floor_margin)
    if room_poly.is_empty:
        room_poly = hull
    if hasattr(room_poly, "geoms"):
        room_poly = max(room_poly.geoms, key=lambda g: g.area)

    ceiling_h = float(np.percentile(shell_pts[:, 2], 99))

    # persist the stripped cloud (means only -- colors/quats ride along in V2)
    shell_ply = scratch / "shell.npy"
    np.save(shell_ply, shell_pts)
    shell_ref = ctx.store.put_file(shell_ply, "application/x-npy")

    floor_surface = SupportSurface(
        surface_id="floor",
        height_m=0.0,
        polygon=[(float(x), float(y)) for x, y in room_poly.exterior.coords],
        area_m2=float(room_poly.area),
        owner_instance_id=None,
    )

    payload = ShellPayload(
        reconstruction_ref=_ref(recon),
        segmentation_ref=_ref(seg),
        shell_ply_ref=shell_ref,
        n_gaussians_kept=int((~obj_idx).sum()),
        n_gaussians_removed=int(obj_idx.sum()),
        floor=floor,
        walls=walls,
        room_polygon_m=floor_surface.polygon,
        room_area_m2=floor_surface.area_m2,
        room_height_m=inferred(ceiling_h, "m", method="p99_of_shell_gaussians"),
        placeable_surfaces=[floor_surface],
        holes=holes,
    )
    return payload, "ransac", degs


def _ref(art) -> ArtifactRef:
    return ArtifactRef(uri=f"cas://sha256/{art.header.artifact_id}",
                       sha256=art.header.artifact_id, size_bytes=0,
                       media_type="application/json")
