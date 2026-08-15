"""The frozen STRUCT spatial contracts (STRUCT_2.md 29-34).

One module per contract, each with its own SCHEMA_VERSION -- bump the version
whose contract changed, not all of them. These are shared by the phone client
and the backend; changing one is a reviewed, coordinated act.
"""
from .correction import CorrectionEvent
from .episode import EpisodeEvent, EventType, SpatialEpisode
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
from .scene import SceneManifest, VisualAsset
from .twin import ObjectState, RobotState, TaskState, TaskStatus, TwinState

CONTRACTS = {
    "spatial_frame": SpatialFrame,
    "spatial_episode": SpatialEpisode,
    "twin_state": TwinState,
    "follow_state": FollowState,
    "correction_event": CorrectionEvent,
    "scene_manifest": SceneManifest,
}

__all__ = [
    "CONTRACTS",
    "DEFAULT_FOLLOW_DISTANCE_M",
    "IDENTITY_QUATERNION",
    "CoordinateFrame",
    "CorrectionEvent",
    "DeviceType",
    "EpisodeEvent",
    "EventType",
    "FollowState",
    "FrameSource",
    "InputType",
    "ObjectState",
    "Pose",
    "Quat",
    "RobotState",
    "SceneManifest",
    "SpatialEpisode",
    "SpatialFrame",
    "TaskState",
    "TaskStatus",
    "TwinState",
    "Vec3",
    "VisualAsset",
    "normalize_quaternion",
    "require_finite",
    "rotate_vector",
]
