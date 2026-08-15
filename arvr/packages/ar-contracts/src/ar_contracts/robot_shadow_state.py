"""RobotShadowState — Shadow Robot Spatial Demonstration Pipeline spec
section 48.

The live-retarget wire format: one `HandFrame` in, one RobotShadowState out,
over `WS /spatial/live/{session_id}` (spec section 47). Distinct from
`RobotTrajectoryFrame` — this is the transient per-tick shape a client
renders the shadow robot from during a live demo; the trajectory is what
gets stored and replayed afterward.
"""

from __future__ import annotations

from .common import FrozenModel, TimestampNs
from .robot_trajectory import IkStatus


class RobotEndEffector(FrozenModel):
    position_m: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float]


class RobotShadowState(FrozenModel):
    timestamp_ns: TimestampNs
    robot_id: str
    joint_positions: tuple[float, ...]
    ik_status: IkStatus
    end_effector: RobotEndEffector
