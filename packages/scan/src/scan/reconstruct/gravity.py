"""Floor detection + world-up canonicalization: RANSAC plane over the low band
of the point cloud, then a rotation taking the floor normal to +Z."""
from __future__ import annotations

import numpy as np

from r2s.core.geometry import Plane


def fit_floor(
    points: np.ndarray,
    rng: np.random.Generator,
    *,
    iters: int = 300,
    dist_thresh: float = 0.02,
) -> Plane | None:
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 50:
        return None
    # floor candidates: lowest 30% by z
    zcut = np.percentile(pts[:, 2], 30)
    cand = pts[pts[:, 2] <= zcut]
    if len(cand) < 30:
        cand = pts

    best_inl, best = 0, None
    for _ in range(iters):
        i = rng.choice(len(cand), 3, replace=False)
        p0, p1, p2 = cand[i]
        n = np.cross(p1 - p0, p2 - p0)
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            continue
        n = n / norm
        if abs(n[2]) < 0.8:  # a floor is roughly horizontal
            continue
        if n[2] < 0:
            n = -n
        off = float(n @ p0)
        d = np.abs(cand @ n - off)
        inl = int((d < dist_thresh).sum())
        if inl > best_inl:
            best_inl, best = inl, (n, off, d < dist_thresh)

    if best is None:
        return None
    n, off, mask = best
    # refine on inliers via least squares
    A = cand[mask]
    centroid = A.mean(0)
    _, _, Vt = np.linalg.svd(A - centroid)
    n_ref = Vt[2]
    if n_ref[2] < 0:
        n_ref = -n_ref
    off_ref = float(n_ref @ centroid)
    resid = np.abs(A @ n_ref - off_ref)
    return Plane(
        normal=tuple(float(v) for v in n_ref),
        offset=off_ref,
        inlier_count=int(mask.sum()),
        inlier_ratio=float(mask.mean()),
        rmse_m=float(np.sqrt((resid**2).mean())),
    )


def gravity_align_transform(floor: Plane) -> np.ndarray:
    """4x4 rotating floor normal to +Z and putting the floor at z=0."""
    n = np.asarray(floor.normal, dtype=np.float64)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(n, z)
    c = float(n @ z)
    T = np.eye(4)
    if np.linalg.norm(v) > 1e-9:
        vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        R = np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))
        T[:3, :3] = R
    T[2, 3] = -floor.offset
    return T
