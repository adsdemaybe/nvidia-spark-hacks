"""SpatialFrame -- the one representation every device converts into.

No robotics code may depend on ARKitFrame, QuestControllerFrame, or
MediaPipeFrame directly (STRUCT_2.md 5). Adapters convert at the boundary and
everything downstream sees this.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from .pose import Quat, Vec3, normalize_quaternion, require_finite

SCHEMA_VERSION = "1.0"

DeviceType = Literal["phone", "xr_controller", "hand_tracking", "desktop_mock"]
InputType = Literal["tracked_controller", "hand", "mock"]
CoordinateFrame = Literal[
    "device_frame", "ar_world", "struct_world", "robot_base", "end_effector"
]


class FrameSource(BaseModel):
    """Which device produced this frame. Diagnostic only -- downstream robot
    code must behave identically regardless of what this says."""

    device_type: DeviceType
    input_type: InputType | None = None


class SpatialFrame(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    timestamp_ns: int
    source: FrameSource
    frame: CoordinateFrame
    position_m: Vec3
    orientation_xyzw: Quat
    gripper: float = 0.0

    @field_validator("position_m")
    @classmethod
    def _position_finite(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        return require_finite(v, "position_m")

    @field_validator("orientation_xyzw")
    @classmethod
    def _orientation_is_rotation(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        return normalize_quaternion(v)
