"""Shared primitives for the STRUCT AR/XR spatial contracts.

Canonical Struct coordinate convention (frozen — spec section 13B):

    right-handed, Z-up, meters, quaternion = [x, y, z, w], timestamps = ns.

Every device-specific adapter (ARKit, Quest controller, MediaPipe, desktop
mock) MUST convert into this convention before any value reaches these
models. No downstream robotics code may depend on a device-native frame
(spec section 5) — that boundary is enforced here, not by convention.

`FrozenModel` subclasses are immutable (a SpatialFrame that changed after
capture would be a silent data-integrity bug — recording is append-only) and
reject unknown fields (extra="forbid") so a typo or an unreviewed field
addition fails loudly at the schema boundary instead of round-tripping
silently, per rule 85.15: never change frozen contracts without review.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

SCHEMA_VERSION: Literal["1.0"] = "1.0"

# The only schema_version this codebase accepts. Bumping this is a contract
# change and requires review (spec sections 60, 85.15) — do not widen to
# Literal["1.0", "1.1"] casually.
SchemaVersion = Literal["1.0"]

CoordinateFrame = Literal[
    "device_frame",
    "ar_world",
    "struct_world",
    "robot_base",
    "end_effector",
]

DeviceType = Literal["phone", "xr_controller", "hand_tracking", "desktop_mock"]
InputType = Literal["tracked_controller", "hand", "mock"]

# Loose enough to accept float32 ARKit/sensor quaternions and rounded example
# payloads (the spec's own SpatialFrame example, section 29, has norm
# 0.997697), tight enough to reject genuinely malformed data by ~10x margin.
QUATERNION_NORM_TOLERANCE = 1e-2


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _validate_position(v: tuple[float, float, float]) -> tuple[float, float, float]:
    if not all(math.isfinite(c) for c in v):
        raise ValueError("position_m must be finite (no NaN/Inf)")
    return v


def _validate_quaternion(
    v: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if not all(math.isfinite(c) for c in v):
        raise ValueError("orientation_xyzw must be finite (no NaN/Inf)")
    norm = math.sqrt(sum(c * c for c in v))
    if abs(norm - 1.0) > QUATERNION_NORM_TOLERANCE:
        raise ValueError(
            f"orientation_xyzw must be a unit quaternion "
            f"(norm={norm:.6f}, expected 1.0 +/- {QUATERNION_NORM_TOLERANCE})"
        )
    return v


def _validate_timestamp(v: int) -> int:
    if v < 0:
        raise ValueError("timestamp_ns must be non-negative")
    return v


PositionM = Annotated[tuple[float, float, float], AfterValidator(_validate_position)]
OrientationXYZW = Annotated[
    tuple[float, float, float, float], AfterValidator(_validate_quaternion)
]
TimestampNs = Annotated[int, AfterValidator(_validate_timestamp)]

IDENTITY_QUATERNION: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)


class Source(FrozenModel):
    device_type: DeviceType
    input_type: InputType | None = None


class Pose(FrozenModel):
    """A full 6-DoF pose: position + orientation, no gripper/velocity state."""

    position_m: PositionM
    orientation_xyzw: OrientationXYZW


class Target(FrozenModel):
    """A spatial target (follow target, correction target). A target may be
    position-only (e.g. a follow point) — orientation defaults to identity
    rather than being nullable, so the wire format never carries a `null`
    a consumer (e.g. the xr-web client's optional-but-not-nullable
    `orientation_xyzw?: Quat`) would have to null-check. Reconciled during
    the arvr/arxr consolidation (see STATE.md)."""

    position_m: PositionM
    orientation_xyzw: OrientationXYZW = IDENTITY_QUATERNION
