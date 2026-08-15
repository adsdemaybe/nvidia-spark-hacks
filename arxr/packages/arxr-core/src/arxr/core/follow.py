"""Follow-target math (STRUCT_2.md 22).

    follow_target = human_position - human_forward * desired_follow_distance

Deliberately deterministic and dependency-free: this runs on the phone at
tracking rate, and the same numbers have to come out on the backend when the
episode is replayed.
"""
from __future__ import annotations

from .schemas.pose import Pose, Vec3, rotate_vector

# Forward is +X in the canonical Z-up right-handed frame. This is the choice
# that reproduces the geometry of the worked example in STRUCT_2.md 32.
FORWARD_AXIS: Vec3 = (1.0, 0.0, 0.0)


def heading(pose: Pose) -> Vec3:
    """The direction the human is facing, as a unit vector in struct_world."""
    return rotate_vector(pose.orientation_xyzw, FORWARD_AXIS)


def follow_target(human: Pose, desired_follow_distance_m: float) -> Vec3:
    """Where the robot should stand: behind the human, along their heading."""
    if desired_follow_distance_m <= 0.0:
        raise ValueError(
            f"follow distance must be positive; got {desired_follow_distance_m}. "
            "A non-positive distance puts the robot in front of the human."
        )

    fwd = heading(human)
    return (
        human.position_m[0] - fwd[0] * desired_follow_distance_m,
        human.position_m[1] - fwd[1] * desired_follow_distance_m,
        human.position_m[2] - fwd[2] * desired_follow_distance_m,
    )
