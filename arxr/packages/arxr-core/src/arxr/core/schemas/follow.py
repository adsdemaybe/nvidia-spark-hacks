"""FollowState -- where the human is, and where the robot should ideally be.

The client owns those two facts and nothing else. How the robot avoids the
table, turns its wheels, or gets through the doorway belongs to the navigation
layer, which may reject this target without that being an AR failure
(STRUCT_2.md 24).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .pose import Pose

SCHEMA_VERSION = "1.0"

DEFAULT_FOLLOW_DISTANCE_M = 1.5


class FollowState(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    timestamp_ns: int
    human_pose: Pose
    desired_follow_distance_m: float = Field(default=DEFAULT_FOLLOW_DISTANCE_M, gt=0.0)
    follow_target: Pose
