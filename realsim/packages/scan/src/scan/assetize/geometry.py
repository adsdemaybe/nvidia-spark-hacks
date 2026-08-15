"""Mesh-side geometry for assetization: canonicalization, stable poses,
support faces, graspability. All trimesh/shapely/numpy -- runs anywhere.
"""
from __future__ import annotations

import numpy as np
import trimesh

from r2s.core import Pose
from r2s.core.schemas import SupportFace


def canonicalize(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, np.ndarray]:
    """Move origin to AABB center-bottom (z-up assumed). Returns (mesh, T_applied)."""
    lo, hi = mesh.bounds
    shift = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    out = mesh.copy()
    out.apply_translation(-shift)
    T = np.eye(4)
    T[:3, 3] = -shift
    return out, T


def stable_poses(mesh: trimesh.Trimesh, max_poses: int = 6) -> tuple[list[Pose], list[float]]:
    """trimesh.poses.compute_stable_poses -- the real thing, with a flat-on-
    bottom fallback for degenerate meshes."""
    try:
        Ts, probs = trimesh.poses.compute_stable_poses(mesh, n_samples=1)
        poses, ps = [], []
        for T, p in zip(Ts[:max_poses], probs[:max_poses], strict=False):
            q = trimesh.transformations.quaternion_from_matrix(T)  # wxyz
            poses.append(Pose(position=tuple(float(v) for v in T[:3, 3]),
                              quaternion=tuple(float(v) for v in q)))
            ps.append(float(p))
        if poses:
            return poses, ps
    except BaseException:  # trimesh can raise anything here, including cython errors
        pass
    return [Pose()], [1.0]


def support_faces(
    mesh: trimesh.Trimesh,
    *,
    min_area: float = 0.02,
    normal_tol_deg: float = 10.0,
    merge_z_tol: float = 0.005,
    edge_margin: float = 0.02,
) -> list[SupportFace]:
    """Horizontal faces things can rest on, ERODED by edge_margin.

    The erosion is the single most important line in the sampler's life: without
    it a mug lands 3mm from the table edge, tips during settle, and burns a
    retry the geometry should have prevented.
    """
    import shapely.geometry as sg
    import shapely.ops as so

    up = np.array([0.0, 0.0, 1.0])
    cos_tol = np.cos(np.radians(normal_tol_deg))
    fmask = mesh.face_normals @ up > cos_tol
    if not fmask.any():
        return []

    tri = mesh.triangles[fmask]
    zc = tri[:, :, 2].mean(axis=1)
    order = np.argsort(zc)
    faces: list[SupportFace] = []
    used = np.zeros(len(tri), dtype=bool)
    fid = 0
    for i in order:
        if used[i]:
            continue
        band = np.abs(zc - zc[i]) <= merge_z_tol
        used |= band
        polys = [sg.Polygon(t[:, :2]) for t in tri[band] if sg.Polygon(t[:, :2]).is_valid]
        if not polys:
            continue
        merged = so.unary_union(polys)
        geoms = merged.geoms if hasattr(merged, "geoms") else [merged]
        for g in geoms:
            eroded = g.buffer(-edge_margin)
            if eroded.is_empty or eroded.area < min_area:
                continue
            outer = max(
                eroded.geoms, key=lambda p: p.area
            ) if hasattr(eroded, "geoms") else eroded
            faces.append(SupportFace(
                face_id=f"face_{fid}",
                height_m=float(zc[band].mean()),
                polygon=[(float(x), float(y)) for x, y in outer.exterior.coords],
                area_m2=float(outer.area),
            ))
            fid += 1
    return faces


def min_antipodal_width(mesh: trimesh.Trimesh, rng: np.random.Generator,
                        n_pairs: int = 256) -> float | None:
    """Cheap graspable-width estimate: the smallest horizontal caliper width
    over sampled directions. None if the object is degenerate.

    True antipodal sampling with friction cones is the V2 upgrade; the caliper
    is monotone-conservative for convex-ish tabletop objects and that is what
    the index filter needs.
    """
    if mesh.is_empty or len(mesh.vertices) < 4:
        return None
    verts = np.asarray(mesh.vertices)
    widths = []
    for theta in np.linspace(0, np.pi, 32, endpoint=False):
        d = np.array([np.cos(theta), np.sin(theta), 0.0])
        proj = verts @ d
        widths.append(float(proj.max() - proj.min()))
    return min(widths) if widths else None


def mesh_from_points(points: np.ndarray) -> trimesh.Trimesh:
    """SCANNED objects, V1: convex hull of the gaussian cluster.

    Honest limitation, recorded as CollisionSummary.method="convex_hull" by the
    caller: concave objects (mugs!) lose their cavity. The visual stays the
    splat; only collision geometry pays, and the hull fallback ladder in
    hulls.py applies the same way to GENERATED meshes where CoACD does the
    concavity properly.
    """
    pc = trimesh.PointCloud(points)
    return pc.convex_hull
