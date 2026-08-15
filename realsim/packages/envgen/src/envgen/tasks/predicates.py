"""Scalar predicate evaluation over a scene state -- the generation-time truth.

Each closed-library predicate gets one implementation here (geometry over
poses + asset boxes) used by gate 3 (not-presatisfied + witness verification).
The batched-torch runtime twins land with Isaac Lab in V2; the SIGNATURES are
frozen now so the runtime cannot drift from what generation validated.

Evaluation state is {instance_id: (Pose, AssetPayload)} plus "FLOOR" implicit.
"""
from __future__ import annotations

import numpy as np

from r2s.core import Pose
from r2s.core.schemas import AssetPayload, Predicate

State = dict[str, tuple[Pose, AssetPayload]]


def _aabb_world(pose: Pose, asset: AssetPayload) -> tuple[np.ndarray, np.ndarray]:
    """Yaw-aware world AABB of the asset's canonical box (origin center-bottom)."""
    hx, hy, hz = asset.bbox_m[0] / 2, asset.bbox_m[1] / 2, asset.bbox_m[2]
    w, x, y, z = pose.quaternion
    yaw = 2 * np.arctan2(z, w)
    c, s = abs(np.cos(yaw)), abs(np.sin(yaw))
    ex, ey = hx * c + hy * s, hx * s + hy * c
    p = np.asarray(pose.position)
    return p + np.array([-ex, -ey, 0.0]), p + np.array([ex, ey, hz])


def eval_on(a_id: str, b_id: str, state: State, *, tol_z: float = 0.03,
            overlap_frac: float = 0.5) -> bool:
    """a rests on b: bottom of a within [top(b)-5mm, top(b)+tol_z] AND XY
    overlap >= overlap_frac of a's footprint. Geometry, not contact reports --
    resting contacts flicker; geometry does not."""
    if b_id == "FLOOR":
        lo_a, hi_a = _aabb_world(*state[a_id])
        return bool(lo_a[2] <= 0.02)
    lo_a, hi_a = _aabb_world(*state[a_id])
    lo_b, hi_b = _aabb_world(*state[b_id])
    # b's top: prefer its highest support face if it has one
    _, b_asset = state[b_id]
    if b_asset.support_faces:
        top_b = state[b_id][0].position[2] + max(f.height_m for f in b_asset.support_faces)
    else:
        top_b = hi_b[2]
    if not (top_b - 0.005 <= lo_a[2] <= top_b + tol_z):
        return False
    ix = max(0.0, min(hi_a[0], hi_b[0]) - max(lo_a[0], lo_b[0]))
    iy = max(0.0, min(hi_a[1], hi_b[1]) - max(lo_a[1], lo_b[1]))
    a_area = (hi_a[0] - lo_a[0]) * (hi_a[1] - lo_a[1])
    return bool(ix * iy >= overlap_frac * max(a_area, 1e-9))


def eval_near(a_id: str, b_id: str, state: State, *, d_max: float) -> bool:
    """Closest-point distance between world AABBs -- NOT centroids, which are
    meaningless when one object is a table."""
    lo_a, hi_a = _aabb_world(*state[a_id])
    lo_b, hi_b = _aabb_world(*state[b_id])
    d = np.maximum(0.0, np.maximum(lo_a - hi_b, lo_b - hi_a))
    return bool(np.linalg.norm(d) <= d_max)


def eval_inside(a_id: str, b_id: str, state: State, *, containment_frac: float = 0.9) -> bool:
    """Fragile by nature (needs a validated interior volume) -- V1 approximates
    with AABB containment and the task generator does not compose it unless a
    container is present."""
    lo_a, hi_a = _aabb_world(*state[a_id])
    lo_b, hi_b = _aabb_world(*state[b_id])
    inside = np.all(lo_a >= lo_b - 1e-6) and np.all(hi_a <= hi_b + 1e-6)
    return bool(inside)


def eval_grasped(a_id: str, state: State) -> bool:
    """Generation-time: nothing is ever grasped in a static scene. The runtime
    twin (gripper force + pose stability) lands with the sim. Returning False
    here is CORRECT for gate 3: a task whose success requires grasped(x) is
    never pre-satisfied at t=0."""
    return False


def eval_pose_within(a_id: str, state: State, *, target: dict, pos_tol: float = 0.05,
                     rot_tol_deg: float = 15.0, axis: str | None = None) -> bool:
    pose, _ = state[a_id]
    tp = np.asarray(target["position"])
    if np.linalg.norm(np.asarray(pose.position) - tp) > pos_tol:
        return False
    if axis == "z" or axis is None:
        # up-axis alignment only -- what "upright" actually means
        w, x, y, z = pose.quaternion
        up_z = 1 - 2 * (x * x + y * y)
        return bool(np.degrees(np.arccos(np.clip(up_z, -1, 1))) <= rot_tol_deg)
    tq = np.asarray(target.get("quaternion", (1, 0, 0, 0)))
    dot = abs(float(np.dot(pose.quaternion, tq)))
    return bool(np.degrees(2 * np.arccos(np.clip(dot, -1, 1))) <= rot_tol_deg)


_LEAF_EVAL = {
    "on": lambda p, s: eval_on(p.args[0], p.args[1], s, **p.kwargs),
    "near": lambda p, s: eval_near(p.args[0], p.args[1], s, **p.kwargs),
    "inside": lambda p, s: eval_inside(p.args[0], p.args[1], s, **p.kwargs),
    "grasped": lambda p, s: eval_grasped(p.args[0], s),
    "pose_within": lambda p, s: eval_pose_within(p.args[0], s, **p.kwargs),
}


def evaluate(pred: Predicate, state: State) -> bool:
    if pred.op == "and":
        return all(evaluate(c, state) for c in pred.children)
    if pred.op == "or":
        return any(evaluate(c, state) for c in pred.children)
    if pred.op == "not":
        return not evaluate(pred.children[0], state)
    return _LEAF_EVAL[pred.op](pred, state)


def leaf_values(pred: Predicate, state: State) -> dict[str, bool]:
    """Per-leaf truth values, recorded into task.json so the runtime can
    re-assert them on reset (predicate-regression tripwire)."""
    out = {}
    for leaf in pred.leaves():
        key = f"{leaf.op}({','.join(leaf.args)})"
        out[key] = _LEAF_EVAL[leaf.op](leaf, state)
    return out
