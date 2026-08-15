"""Set-level diversity metric: what separates '8 environments' from '8 renders
of the same environment'.

  D(A,B) = 0.40*(1 - Jaccard(asset_ids))
         + 0.10*(1 - Jaccard(categories))
         + 0.35*layout_chamfer
         + 0.15*task_distance

Two silent-bug traps handled explicitly:
  * the SHELL is excluded from the layout cloud -- floor/walls are identical
    across all 8; include them and D saturates near 0 and everything passes;
  * fixtures (role=fixture) are excluded for the same reason.

The 0.30 threshold is a calibrated guess: run `envgen calibrate-diversity` on
50 candidates and pick the histogram valley before trusting it.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from r2s.core.schemas import ScenePayload


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / max(len(a | b), 1)


def _layout_cloud(scene: ScenePayload) -> np.ndarray:
    pts = []
    for p in scene.placements:
        if p.role == "fixture":
            continue
        pose = p.settled_pose or p.pose
        pts.append(pose.position)
    return np.asarray(pts, dtype=float) if pts else np.zeros((0, 3))


def _chamfer(a: np.ndarray, b: np.ndarray, norm: float) -> float:
    if len(a) == 0 or len(b) == 0:
        return 1.0
    ta, tb = cKDTree(a), cKDTree(b)
    d_ab, _ = tb.query(a)
    d_ba, _ = ta.query(b)
    c = (d_ab.mean() + d_ba.mean()) / 2.0 / max(norm, 1e-6)
    return float(1.0 - np.exp(-c / 0.05))


def _task_distance(fam_a: str | None, fam_b: str | None,
                   tcat_a: frozenset, tcat_b: frozenset) -> float:
    if fam_a is None or fam_b is None:
        return 0.0
    if fam_a != fam_b:
        return 1.0
    return 0.0 if tcat_a == tcat_b else 0.5


def scene_distance(
    a: ScenePayload, b: ScenePayload,
    *,
    room_diag: float,
    task_family: dict[str, str] | None = None,
    task_cats: dict[str, frozenset] | None = None,
) -> float:
    ids_a, ids_b = a.asset_ids(), b.asset_ids()
    cats_a = {p.category for p in a.placements if p.role != "fixture"}
    cats_b = {p.category for p in b.placements if p.role != "fixture"}
    tf = task_family or {}
    tc = task_cats or {}
    return (
        0.40 * (1.0 - _jaccard(ids_a, ids_b))
        + 0.10 * (1.0 - _jaccard(cats_a, cats_b))
        + 0.35 * _chamfer(_layout_cloud(a), _layout_cloud(b), room_diag)
        + 0.15 * _task_distance(tf.get(a.scene_id), tf.get(b.scene_id),
                                tc.get(a.scene_id, frozenset()),
                                tc.get(b.scene_id, frozenset()))
    )


def min_distance_to_set(
    cand: ScenePayload, accepted: list[ScenePayload], *, room_diag: float, **kw
) -> float:
    if not accepted:
        return 1.0
    return min(scene_distance(cand, s, room_diag=room_diag, **kw) for s in accepted)
