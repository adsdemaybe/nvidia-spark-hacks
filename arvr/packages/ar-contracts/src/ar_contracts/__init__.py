"""ar_contracts — frozen spatial schemas for the STRUCT AR/XR subsystem.

See arvr/docs/CONTRACTS.md for the coordinate convention and freeze status.
"""

from .common import (
    SCHEMA_VERSION,
    CoordinateFrame,
    DeviceType,
    InputType,
    OrientationXYZW,
    Pose,
    PositionM,
    SchemaVersion,
    Source,
    Target,
    TimestampNs,
)
from .correction_event import CorrectionEvent, CorrectionReason
from .follow import compute_follow_target, rotate_vector
from .follow_state import FollowMode, FollowState
from .scene_manifest import SceneManifest, VisualAsset
from .spatial_episode import EpisodeSource, EventType, SpatialEpisode, SpatialEvent
from .spatial_frame import SpatialFrame
from .twin_state import ObjectState, RobotState, TaskState, TaskStatus, TwinState
from .verification_result import VerificationChecks, VerificationResult, VerificationStatus

__all__ = [
    "SCHEMA_VERSION",
    "CoordinateFrame",
    "CorrectionEvent",
    "CorrectionReason",
    "DeviceType",
    "EpisodeSource",
    "EventType",
    "FollowMode",
    "FollowState",
    "InputType",
    "ObjectState",
    "OrientationXYZW",
    "Pose",
    "PositionM",
    "RobotState",
    "SceneManifest",
    "SchemaVersion",
    "Source",
    "SpatialEpisode",
    "SpatialEvent",
    "SpatialFrame",
    "Target",
    "TaskState",
    "TaskStatus",
    "TimestampNs",
    "TwinState",
    "VerificationChecks",
    "VerificationResult",
    "VerificationStatus",
    "VisualAsset",
    "compute_follow_target",
    "rotate_vector",
]
