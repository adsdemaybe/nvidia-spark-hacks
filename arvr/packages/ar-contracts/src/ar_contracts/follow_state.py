"""FollowState — spec section 32.

Produced continuously by the phone while Follow mode is active (spec
section 21-24). The AR/XR subsystem owns computing `follow_target`; it does
NOT own how the robot/navigation layer reaches it (spec section 24).
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import field_validator

from .common import SCHEMA_VERSION, FrozenModel, Pose, SchemaVersion, Target, TimestampNs

FollowMode = Literal["stopped", "following", "paused"]


class FollowState(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    timestamp_ns: TimestampNs
    human_pose: Pose
    desired_follow_distance_m: float
    follow_target: Target
    state: FollowMode = "following"

    @field_validator("desired_follow_distance_m")
    @classmethod
    def _positive(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("desired_follow_distance_m must be finite and > 0")
        return v
