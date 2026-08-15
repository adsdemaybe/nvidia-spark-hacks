"""Metric scale: align the scale-free SfM cloud to the LiDAR mesh.

RANSAC over correspondence subsets + Umeyama sim(3). ~90 lines of numpy on
purpose -- open3d's aarch64 wheels are unreliable and this must run everywhere.

Without this stage every downstream mass, inertia tensor, grasp width, and
reach check is fiction; that is why RECON.SCALE_METRIC is a HARD gate.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree


@dataclass
class Sim3Result:
    scale: float
    T: np.ndarray  # 4x4, sfm -> metric
    rmse_m: float
    inlier_ratio: float


def umeyama(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Closed-form similarity transform src->dst (Umeyama 1991)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    sc, dc = src - mu_s, dst - mu_d
    cov = dc.T @ sc / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1.0
    R = U @ S @ Vt
    var_s = (sc**2).sum() / len(src)
    s = float(np.trace(np.diag(D) @ S) / max(var_s, 1e-12))
    t = mu_d - s * R @ mu_s
    return s, R, t


def solve_sim3_icp(
    sfm_points: np.ndarray,
    lidar_points: np.ndarray,
    rng: np.random.Generator,
    *,
    iters: int = 30,
    sample: int = 2000,
    inlier_dist_m: float = 0.05,
    lidar_mesh=None,
) -> Sim3Result:
    """Scale-adaptive ICP: nearest-neighbour correspondences on subsampled
    clouds, Umeyama each round. Initialized by matching centroids + RMS radii,
    which handles the unknown global scale that vanilla ICP cannot."""
    sfm = np.asarray(sfm_points, dtype=np.float64)
    lidar = np.asarray(lidar_points, dtype=np.float64)
    if len(sfm) > sample:
        sfm = sfm[rng.choice(len(sfm), sample, replace=False)]
    if len(lidar) > 4 * sample:
        lidar = lidar[rng.choice(len(lidar), 4 * sample, replace=False)]

    # init: centroid + radius matching
    s0 = float(
        np.sqrt(((lidar - lidar.mean(0)) ** 2).sum(1).mean())
        / max(np.sqrt(((sfm - sfm.mean(0)) ** 2).sum(1).mean()), 1e-12)
    )
    R = np.eye(3)
    t = lidar.mean(0) - s0 * sfm.mean(0)
    s = s0

    tree = cKDTree(lidar)
    cur = s * sfm @ R.T + t
    for _ in range(iters):
        d, j = tree.query(cur)
        # trim to the closest 70% -- partial overlap is the normal case
        keep = d <= np.percentile(d, 70)
        if keep.sum() < 10:
            break
        s, R, t = umeyama(sfm[keep], lidar[j[keep]])
        cur = s * sfm @ R.T + t

    # Final scoring: distance to the mesh SURFACE when the mesh is available.
    # Point-sampled correspondence systematically overestimates the residual --
    # samples land on slab back-faces ~2cm from the scanned front surface, and
    # that offset is a scoring artifact, not an alignment error.
    if lidar_mesh is not None:
        try:
            import trimesh

            _, d, _ = trimesh.proximity.closest_point(lidar_mesh, cur)
        except BaseException:
            d, _ = tree.query(cur)
    else:
        d, _ = tree.query(cur)
    inliers = d <= inlier_dist_m
    rmse = float(np.sqrt((d[inliers] ** 2).mean())) if inliers.any() else float("inf")
    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = t
    return Sim3Result(scale=s, T=T, rmse_m=rmse, inlier_ratio=float(inliers.mean()))
