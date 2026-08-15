"""Interaction IR derivation — Shadow Robot Spatial Demonstration Pipeline
spec section 22-23.

Derives object-relative, robot-independent physical intent from a
HumanEpisode plus the InteractableAsset it was recorded against:

    T_object_hand = inverse(T_world_object) x T_world_hand

Pure numpy quaternion math, not Pinocchio's `pin.SE3` -- this module should
import cleanly everywhere (Windows included) per the workspace's lazy-import
convention, and genuinely doesn't need Pinocchio for a rigid-transform
compose/invert.

Never mutates `human_episode` -- the acceptance gate for this phase is
literally "raw HumanEpisode unchanged" (spec section 6's core invariant,
re-checked here specifically because this is the first stage that reads a
HumanEpisode after it's recorded).
"""

from __future__ import annotations

import numpy as np
from ar_contracts import (
    HumanEpisode,
    InteractableAsset,
    InteractionIR,
    InteractionPhase,
    OrientationXYZW,
    Pose,
)

# Judgment call, not derived from a formula the spec gives (it shows one
# illustrative example, section 22, with no stated relationship between its
# numbers): how far "above" the press point (along the negative press axis)
# the approach phase's target sits, and how far the retract phase backs off
# afterward. Same order of magnitude as the spec's own example.
APPROACH_HOVER_M = 0.02
RETRACT_DISTANCE_M = 0.04


def _quat_conjugate(q: OrientationXYZW) -> np.ndarray:
    x, y, z, w = q
    return np.array([-x, -y, -z, w])


def _quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product a (x) b, xyzw convention."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array(
        [
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        ]
    )


def _rotate_vector(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate v by unit quaternion q (xyzw) -- v + 2*cross(qxyz, cross(qxyz, v) + w*v)."""
    qxyz, w = q[:3], q[3]
    t = 2.0 * np.cross(qxyz, v)
    return v + w * t + np.cross(qxyz, t)


def object_relative_pose(world_object: Pose, world_hand: Pose) -> tuple[np.ndarray, np.ndarray]:
    """T_object_hand = inverse(T_world_object) x T_world_hand.

    Returns (position, orientation_xyzw) as raw numpy arrays -- callers that
    need a validated ar_contracts.Pose should wrap the result themselves;
    this function is the pure math, kept separate so it's testable against
    a hand-computed expectation without contract-validation noise.
    """
    q_hand = np.array(world_hand.orientation_xyzw)
    t_object = np.array(world_object.position_m)
    t_hand = np.array(world_hand.position_m)

    q_object_inv = _quat_conjugate(world_object.orientation_xyzw)
    position = _rotate_vector(q_object_inv, t_hand - t_object)
    orientation = _quat_multiply(q_object_inv, q_hand)
    orientation = orientation / np.linalg.norm(orientation)
    return position, orientation


def derive_interaction_ir(
    human_episode: HumanEpisode,
    asset: InteractableAsset,
    asset_world_pose: Pose,
) -> InteractionIR:
    part_name = next(iter(asset.parts))
    part = asset.parts[part_name]
    local_origin_m = part.local_origin_m or (0.0, 0.0, 0.0)
    axis = part.axis or (0.0, 0.0, -1.0)

    contact_events = [e for e in human_episode.events if e.type == "contact"]
    contact_timestamp_ns = contact_events[0].timestamp_ns if contact_events else None

    hover_offset = tuple(-a * APPROACH_HOVER_M for a in axis)
    approach_target = tuple(o + h for o, h in zip(local_origin_m, hover_offset, strict=True))

    phases: list[InteractionPhase] = [
        InteractionPhase(type="approach", target_position_m=approach_target)
    ]
    if contact_timestamp_ns is not None:
        phases.append(InteractionPhase(type="contact"))
    if part.interaction == "press" and part.travel_m is not None:
        phases.append(InteractionPhase(type="press", axis=axis, distance_m=part.travel_m))
    elif part.interaction == "pull" and part.limit_m is not None:
        pull_distance = part.limit_m[1] - part.limit_m[0]
        phases.append(InteractionPhase(type="pull", axis=axis, distance_m=pull_distance))
    elif part.interaction == "grasp":
        phases.append(InteractionPhase(type="grasp"))
    phases.append(InteractionPhase(type="retract", distance_m=RETRACT_DISTANCE_M))

    return InteractionIR(
        task_id=human_episode.metadata.task_id,
        asset_id=asset.asset_id,
        reference_frame=f"{asset.asset_id}.{part_name}",
        phases=tuple(phases),
    )
