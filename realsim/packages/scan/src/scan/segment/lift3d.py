"""Lift 2D masks to per-gaussian labels by multi-view majority vote.

For each camera: project every gaussian mean; a gaussian visible in that view
votes for whichever object's mask contains its projection. Assignment goes to
the object with the highest supporting-view ratio above threshold; conflicts
resolve to the higher ratio and are counted, never silently dropped.

V1 visibility is z-ordering-free (no depth test) -- honest limitation, recorded
as lift_method="projection_depthtest_majority" only when the depth test lands
with the Spark's rendered depth buffers. Until then the method string says what
actually ran.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class CameraView:
    K: np.ndarray  # (3,3)
    w2c: np.ndarray  # (4,4) world -> camera
    masks: dict[str, np.ndarray]  # object_id -> (H,W) bool
    width: int
    height: int


def lift_by_vote(
    means: np.ndarray,
    views: list[CameraView],
    *,
    min_views: int = 5,
    min_ratio: float = 0.6,
) -> tuple[dict[str, np.ndarray], int]:
    """-> ({object_id: gaussian index array}, n_conflicts)."""
    n = len(means)
    obj_ids = sorted({o for v in views for o in v.masks})
    votes = {o: np.zeros(n, dtype=np.int32) for o in obj_ids}
    seen = np.zeros(n, dtype=np.int32)

    hom = np.concatenate([means, np.ones((n, 1))], axis=1)
    for v in views:
        cam = hom @ v.w2c.T
        z = cam[:, 2]
        front = z > 0.05
        uv = (cam[:, :3] @ v.K.T)
        uv = uv[:, :2] / np.maximum(uv[:, 2:3], 1e-9)
        inside = front & (uv[:, 0] >= 0) & (uv[:, 0] < v.width) & (uv[:, 1] >= 0) & (uv[:, 1] < v.height)
        seen += inside.astype(np.int32)
        ui = np.clip(uv[:, 0].astype(np.int64), 0, v.width - 1)
        vi = np.clip(uv[:, 1].astype(np.int64), 0, v.height - 1)
        for oid, mask in v.masks.items():
            votes[oid] += (inside & mask[vi, ui]).astype(np.int32)

    ratios = np.stack([votes[o] / np.maximum(seen, 1) for o in obj_ids], axis=0)
    best = ratios.argmax(axis=0)
    best_ratio = ratios.max(axis=0)
    second = np.partition(ratios, -2, axis=0)[-2] if len(obj_ids) > 1 else np.zeros(n)

    assigned_mask = (best_ratio >= min_ratio) & (seen >= min_views)
    conflicts = int(((second >= min_ratio) & assigned_mask).sum())

    out = {}
    for k, oid in enumerate(obj_ids):
        idx = np.nonzero(assigned_mask & (best == k))[0]
        if len(idx):
            out[oid] = idx.astype(np.int32)
    return out, conflicts
