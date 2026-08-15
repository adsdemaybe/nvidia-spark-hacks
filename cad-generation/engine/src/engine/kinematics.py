"""Tier 0 forward kinematics — the world transform of every link at a fixed
"home" configuration (joint value = midpoint of its limits, or identity for
fixed joints). Real Pinocchio-driven multi-configuration kinematics is tier 1
(§3) and not implemented yet; this module only needs to be fast and correct
for the single static pose tier-0 analytic criteria check (mount fit, reach,
static margin per the tier table in §3).

Follows URDF convention: a joint's origin places the child *link frame*
relative to the parent link frame; a link's own `pose` is then a further,
separate offset for where that link's geometry sits within its own frame.
"""

from __future__ import annotations

import numpy as np

from engine.ir import Joint, Pose, RobotIR, Vec3


def _euler_xyz_to_matrix(rot: Vec3) -> np.ndarray:
    cx, sx = np.cos(rot.x), np.sin(rot.x)
    cy, sy = np.cos(rot.y), np.sin(rot.y)
    cz, sz = np.cos(rot.z), np.sin(rot.z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def _axis_angle_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    x, y, z = axis
    c, s = np.cos(angle), np.sin(angle)
    cc = 1 - c
    return np.array(
        [
            [x * x * cc + c, x * y * cc - z * s, x * z * cc + y * s],
            [y * x * cc + z * s, y * y * cc + c, y * z * cc - x * s],
            [z * x * cc - y * s, z * y * cc + x * s, z * z * cc + c],
        ]
    )


def pose_to_matrix(pose: Pose) -> np.ndarray:
    t = np.eye(4)
    t[:3, :3] = _euler_xyz_to_matrix(pose.rotation)
    t[:3, 3] = pose.position.as_tuple()
    return t


def _joint_matrix(joint: Joint, value: float | None = None) -> np.ndarray:
    """The joint's transform with its DOF driven to `value` (radians for
    revolute, metres for prismatic). `None` means the home configuration: the
    midpoint of its limits, or no DOF at all if it declares none.
    """
    t = pose_to_matrix(joint.origin)
    if joint.kind == "fixed":
        return t
    if value is None:
        # An unbounded joint has no midpoint to sit at; home is zero, which for
        # a wheel is the only meaningful "home" anyway.
        if joint.limits is None or not joint.limits.bounded:
            return t
        value = (joint.limits.lower.value + joint.limits.upper.value) / 2.0

    axis = np.array(joint.axis.as_tuple(), dtype=float)
    norm = np.linalg.norm(axis)
    if norm == 0:
        raise ValueError(f"joint {joint.id!r} has a zero-length axis")
    axis = axis / norm

    tj = np.eye(4)
    if joint.kind == "revolute":
        tj[:3, :3] = _axis_angle_matrix(axis, value)
    elif joint.kind == "prismatic":
        tj[:3, 3] = axis * value
    else:
        raise ValueError(f"unknown joint kind {joint.kind!r}")
    return t @ tj


def _joint_home_matrix(joint: Joint) -> np.ndarray:
    return _joint_matrix(joint, None)


def link_frames(ir: RobotIR) -> dict[str, np.ndarray]:
    """World transform (4x4) of every link's *frame* (not its geometry
    origin — see `link_geometry_transform` for that), at the home pose.
    """
    return configured_frames(ir, None)


def configured_frames(
    ir: RobotIR, angles: dict[str, float] | None = None
) -> dict[str, np.ndarray]:
    """`link_frames`, but with each joint driven to `angles[joint_id]`.

    Joints absent from `angles` stay at home. Driving the tree from the root
    here — rather than rotating subtrees after the fact — is what keeps a child
    joint's pivot read from an already-moved parent frame; the after-the-fact
    form is correct on a one-level tree like a rover and silently wrong on an
    arm, which is the worst way for it to be wrong.

    Kinematics belongs here and not in a viewer: `evaluate()`'s criteria need to
    sweep a joint through its range just as much as a slider does.
    """
    angles = angles or {}
    children: dict[str, list[Joint]] = {}
    for joint in ir.joints:
        children.setdefault(joint.parent, []).append(joint)

    frames: dict[str, np.ndarray] = {ir.root_link: np.eye(4)}
    stack = [ir.root_link]
    # `visited` is load-bearing, not defensive: RobotIR validates that every joint
    # names known links but not that the joint graph is acyclic, so a cycle would
    # otherwise walk this loop forever — a hang, with no error and no progress, in
    # the middle of an agent loop that has no timeout around it.
    visited: set[str] = {ir.root_link}
    while stack:
        parent_id = stack.pop()
        parent_t = frames[parent_id]
        for joint in children.get(parent_id, []):
            if joint.child in visited:
                raise ValueError(
                    f"joint {joint.id!r} closes a cycle back onto link {joint.child!r}: "
                    "the joint graph must be a tree"
                )
            visited.add(joint.child)
            frames[joint.child] = parent_t @ _joint_matrix(joint, angles.get(joint.id))
            stack.append(joint.child)

    missing = {link.id for link in ir.links} - frames.keys()
    if missing:
        raise ValueError(f"links not reachable from root {ir.root_link!r} via joints: {sorted(missing)}")
    return frames


def link_geometry_transform(
    ir: RobotIR, link_id: str, frames: dict[str, np.ndarray] | None = None
) -> np.ndarray:
    """World transform of a link's geometry origin (frame transform composed
    with the link's own local pose offset).

    Pass `frames` from a single `link_frames(ir)` when transforming more than one
    link. Without it every call re-walks the whole tree, which makes a per-link
    loop quadratic — `static_margin` did exactly that, and `joint_torque_budget`
    did it once per joint *per subtree link*, against a docstring promising
    "<1ms, run on every candidate".
    """
    if frames is None:
        frames = link_frames(ir)
    return frames[link_id] @ pose_to_matrix(ir.link(link_id).pose)


def joint_world_frame(
    ir: RobotIR, joint: Joint, frames: dict[str, np.ndarray] | None = None
) -> np.ndarray:
    """World transform of a joint's pivot: the parent link's frame composed
    with the joint's origin only — *before* the joint's own DOF is applied.
    This is where the joint physically sits and how its local axis is
    oriented in world space, independent of the home-configuration angle.
    """
    if frames is None:
        frames = link_frames(ir)
    return frames[joint.parent] @ pose_to_matrix(joint.origin)


def subtree_links(ir: RobotIR, root_link_id: str) -> list[str]:
    """Every link in the kinematic subtree rooted at `root_link_id`,
    inclusive — i.e. everything a joint at `root_link_id`'s parent joint
    must hold up.
    """
    children: dict[str, list[str]] = {}
    for joint in ir.joints:
        children.setdefault(joint.parent, []).append(joint.child)

    result = []
    seen: set[str] = set()
    stack = [root_link_id]
    while stack:
        link_id = stack.pop()
        if link_id in seen:  # same cycle guard as link_frames, same reason
            continue
        seen.add(link_id)
        result.append(link_id)
        stack.extend(children.get(link_id, []))
    return result
