"""RobotTrajectory — Shadow Robot Spatial Demonstration Pipeline spec
section 30.

One robot's retargeted answer to a HumanEpisode: per-frame joint targets,
produced by `ar_datapipe.arm_retargeter.ArmRetargeter`, consumed by
`spatial_providers.SimulationProvider.replay_and_verify()`. Like
`HumanEpisode`, the trajectory itself is a plain dataclass (a potentially
long `list[RobotTrajectoryFrame]`), while the per-frame record and metadata
stay frozen pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .common import (
    SCHEMA_VERSION,
    FrozenModel,
    OrientationXYZW,
    PositionM,
    SchemaVersion,
    TimestampNs,
)

IkStatus = Literal["ok", "failed", "joint_limit"]


class RobotTrajectoryFrame(FrozenModel):
    timestamp_ns: TimestampNs
    q: tuple[float, ...]
    dq: tuple[float, ...]
    end_effector_position_m: PositionM
    end_effector_orientation_xyzw: OrientationXYZW
    gripper: float
    ik_status: IkStatus


class RobotTrajectoryMetadata(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    trajectory_id: str
    robot_id: str
    source_human_episode_id: str
    robot_bundle_hash: str
    asset_bundle_hash: str


@dataclass
class RobotTrajectory:
    metadata: RobotTrajectoryMetadata
    frames: list[RobotTrajectoryFrame] = field(default_factory=list)
