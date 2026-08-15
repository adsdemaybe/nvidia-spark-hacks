"""The frozen STRUCT spatial contracts (STRUCT_2.md 29-34).

One module per contract, each with its own SCHEMA_VERSION -- bump the version
whose contract changed, not all of them. These are shared by the phone client
and the backend; changing one is a reviewed, coordinated act.
"""
from .follow import DEFAULT_FOLLOW_DISTANCE_M, FollowState
from .frames import CoordinateFrame, DeviceType, FrameSource, InputType, SpatialFrame
from .pose import (
    IDENTITY_QUATERNION,
    Pose,
    Quat,
    Vec3,
    normalize_quaternion,
    require_finite,
    rotate_vector,
)

__all__ = [
    "DEFAULT_FOLLOW_DISTANCE_M",
    "IDENTITY_QUATERNION",
    "CoordinateFrame",
    "DeviceType",
    "FollowState",
    "FrameSource",
    "InputType",
    "Pose",
    "Quat",
    "SpatialFrame",
    "Vec3",
    "normalize_quaternion",
    "require_finite",
    "rotate_vector",
]
