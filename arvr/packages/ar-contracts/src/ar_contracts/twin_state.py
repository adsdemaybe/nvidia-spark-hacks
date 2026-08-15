"""TwinState — spec section 31.

The wire format published by whichever backend is authoritative for the
digital twin (MockTwinStateProvider today, IsaacTwinStateProvider once the
Spark bridge is wired — spec section 25). The AR client must not care which
one produced a given message; both MUST emit exactly this shape.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import field_validator

from .common import (
    IDENTITY_QUATERNION,
    SCHEMA_VERSION,
    FrozenModel,
    OrientationXYZW,
    PositionM,
    SchemaVersion,
    TimestampNs,
)

TaskStatus = Literal[
    "idle",
    "running",
    "approaching",
    "grasping",
    "moving",
    "placing",
    "success",
    "failed",
]


class RobotState(FrozenModel):
    id: str
    joint_positions: tuple[float, ...]

    @field_validator("joint_positions")
    @classmethod
    def _finite(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        if not all(math.isfinite(x) for x in v):
            raise ValueError("joint_positions must be finite (no NaN/Inf)")
        return v


class ObjectState(FrozenModel):
    id: str
    position_m: PositionM
    # Default identity, not required — an object with no meaningful
    # orientation (e.g. a bin) shouldn't force a producer to invent one.
    # Reconciled during the arvr/arxr consolidation (see STATE.md).
    orientation_xyzw: OrientationXYZW = IDENTITY_QUATERNION


class TaskState(FrozenModel):
    id: str
    status: TaskStatus


class TwinState(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    timestamp_ns: TimestampNs
    scene_id: str
    robot: RobotState
    objects: tuple[ObjectState, ...] = ()
    task: TaskState | None = None
