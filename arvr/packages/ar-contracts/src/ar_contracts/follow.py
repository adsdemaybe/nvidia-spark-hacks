"""Deterministic follow-target calculation — spec sections 22-24.

    follow_target = human_position - human_forward * desired_follow_distance

This module owns ONLY the target calculation (where the robot should ideally
stand). It must never grow navigation/obstacle-avoidance logic — spec
section 24 is explicit that deciding *how* the robot reaches the target is
out of AR/XR scope.

Forward-vector convention: local +X is "forward" in struct_world (ROS
REP-103: X-forward, Y-left, Z-up, right-handed). The spec does not pin this
down explicitly, so it is called out here as a Sky judgment call rather than
silently assumed — flag for review before Phase 8 (Follow) freezes for real.
"""

from __future__ import annotations

import math

FORWARD_LOCAL = (1.0, 0.0, 0.0)


def rotate_vector(
    orientation_xyzw: tuple[float, float, float, float],
    v: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Rotate vector `v` by unit quaternion `orientation_xyzw` (x, y, z, w)."""
    qx, qy, qz, qw = orientation_xyzw
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    cx = qy * tz - qz * ty
    cy = qz * tx - qx * tz
    cz = qx * ty - qy * tx
    return (vx + qw * tx + cx, vy + qw * ty + cy, vz + qw * tz + cz)


def compute_follow_target(
    human_position_m: tuple[float, float, float],
    human_orientation_xyzw: tuple[float, float, float, float],
    desired_follow_distance_m: float,
) -> tuple[float, float, float]:
    if not all(math.isfinite(c) for c in human_position_m):
        raise ValueError("human_position_m must be finite")
    if not all(math.isfinite(c) for c in human_orientation_xyzw):
        raise ValueError("human_orientation_xyzw must be finite")
    if not math.isfinite(desired_follow_distance_m) or desired_follow_distance_m <= 0:
        raise ValueError("desired_follow_distance_m must be finite and > 0")

    fx, fy, fz = rotate_vector(human_orientation_xyzw, FORWARD_LOCAL)
    hx, hy, hz = human_position_m
    return (
        hx - fx * desired_follow_distance_m,
        hy - fy * desired_follow_distance_m,
        hz - fz * desired_follow_distance_m,
    )
