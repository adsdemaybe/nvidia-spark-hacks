"""ar_contracts — frozen spatial schemas for the STRUCT AR/XR subsystem.

See arvr/docs/CONTRACTS.md for the coordinate convention and freeze status.
"""

from .common import (
    IDENTITY_QUATERNION,
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
from .follow_session import DEFAULT_FOLLOW_DISTANCE_M, FollowSession, FollowSessionMode
from .follow_state import FollowMode, FollowState
from .hand_frame import HAND_JOINT_NAMES, HandFrame, HandJoint, HandSide, HandSourceDevice
from .human_episode import (
    HumanEpisode,
    HumanEpisodeEvent,
    HumanEpisodeMetadata,
    HumanEpisodeStatus,
    HumanEventType,
)
from .interactable_asset import AssetPart, InteractableAsset, InteractionKind
from .interaction_ir import InteractionIR, InteractionPhase, InteractionPhaseType
from .robot_bundle import (
    EndEffectorKind,
    JointType,
    RobotBundle,
    RobotCapabilityProfile,
    RobotIR,
    RobotJoint,
    RobotManifest,
    RobotSource,
)
from .robot_episode import RobotEpisode, RobotEpisodeMetadata, Simulator
from .robot_shadow_state import RobotEndEffector, RobotShadowState
from .robot_trajectory import (
    IkStatus,
    RobotTrajectory,
    RobotTrajectoryFrame,
    RobotTrajectoryMetadata,
)
from .scene_manifest import SceneManifest, VisualAsset
from .spatial_episode import EpisodeSource, EventType, SpatialEpisode, SpatialEvent
from .spatial_frame import SpatialFrame
from .twin_state import ObjectState, RobotState, TaskState, TaskStatus, TwinState
from .verification_result import VerificationChecks, VerificationResult, VerificationStatus

__all__ = [
    "DEFAULT_FOLLOW_DISTANCE_M",
    "HAND_JOINT_NAMES",
    "IDENTITY_QUATERNION",
    "SCHEMA_VERSION",
    "AssetPart",
    "CoordinateFrame",
    "CorrectionEvent",
    "CorrectionReason",
    "DeviceType",
    "EndEffectorKind",
    "EpisodeSource",
    "EventType",
    "FollowMode",
    "FollowSession",
    "FollowSessionMode",
    "FollowState",
    "HandFrame",
    "HandJoint",
    "HandSide",
    "HandSourceDevice",
    "HumanEpisode",
    "HumanEpisodeEvent",
    "HumanEpisodeMetadata",
    "HumanEpisodeStatus",
    "HumanEventType",
    "IkStatus",
    "InputType",
    "InteractableAsset",
    "InteractionIR",
    "InteractionKind",
    "InteractionPhase",
    "InteractionPhaseType",
    "JointType",
    "ObjectState",
    "OrientationXYZW",
    "Pose",
    "PositionM",
    "RobotBundle",
    "RobotCapabilityProfile",
    "RobotEndEffector",
    "RobotEpisode",
    "RobotEpisodeMetadata",
    "RobotIR",
    "RobotJoint",
    "RobotManifest",
    "RobotShadowState",
    "RobotSource",
    "RobotState",
    "RobotTrajectory",
    "RobotTrajectoryFrame",
    "RobotTrajectoryMetadata",
    "SceneManifest",
    "SchemaVersion",
    "Simulator",
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
