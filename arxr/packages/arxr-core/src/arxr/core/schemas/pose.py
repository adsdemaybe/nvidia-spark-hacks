"""The spatial primitive every other contract is built from.

Canonical convention, no exceptions: right-handed, Z-up, meters, quaternion
[x, y, z, w]. Adapters convert at the boundary; nothing downstream
re-interprets units (STRUCT_2.md 13B).
"""
from __future__ import annotations

import math

from pydantic import BaseModel, field_validator

# How far off unit-norm a quaternion may arrive and still be treated as a
# rounding artifact rather than a wrong value. 1e-2 covers the 2-decimal
# quaternions printed in STRUCT_2.md 29 (norm 0.9977) and JSON round-tripping
# through float32, while still rejecting an unnormalized axis like [1,1,0,0]
# (norm 1.414) or a zero vector.
QUAT_NORM_TOLERANCE = 1e-2

IDENTITY_QUATERNION = (0.0, 0.0, 0.0, 1.0)

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


def require_finite(values: tuple[float, ...], field: str) -> tuple[float, ...]:
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f"{field} must be finite; got {values}")
    return values


def normalize_quaternion(q: Quat) -> tuple[float, ...]:
    """Reject anything that is not plausibly a rotation, then return it exactly
    unit-norm so nothing downstream has to re-check or re-normalize."""
    require_finite(q, "orientation_xyzw")
    norm = math.sqrt(sum(v * v for v in q))
    if abs(norm - 1.0) > QUAT_NORM_TOLERANCE:
        raise ValueError(f"orientation_xyzw must be a unit quaternion; norm was {norm:.6f}")
    return tuple(v / norm for v in q)


def rotate_vector(q: Quat, v: Vec3) -> Vec3:
    """Rotate v by quaternion q. Uses the cross-product form rather than
    building a matrix -- fewer operations and no allocation per frame, which
    matters at the 30-60 Hz these contracts stream at."""
    qx, qy, qz, qw = q
    ux, uy, uz = qx, qy, qz

    # t = 2 * (u x v)
    tx = 2.0 * (uy * v[2] - uz * v[1])
    ty = 2.0 * (uz * v[0] - ux * v[2])
    tz = 2.0 * (ux * v[1] - uy * v[0])

    return (
        v[0] + qw * tx + (uy * tz - uz * ty),
        v[1] + qw * ty + (uz * tx - ux * tz),
        v[2] + qw * tz + (ux * ty - uy * tx),
    )


class Pose(BaseModel):
    """A position, and optionally an orientation. Contracts that carry only a
    point (a follow target, a grasp point) leave the orientation at identity."""

    position_m: Vec3
    orientation_xyzw: Quat = IDENTITY_QUATERNION

    @field_validator("position_m")
    @classmethod
    def _position_finite(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        return require_finite(v, "position_m")

    @field_validator("orientation_xyzw")
    @classmethod
    def _orientation_is_rotation(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        return normalize_quaternion(v)
