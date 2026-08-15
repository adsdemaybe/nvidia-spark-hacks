"""MuJoCo-based replay/verification — spec section 13F / Phase 6.

Deliberately kinematic, not dynamic: we only need forward kinematics to
cross-check Pinocchio's IK solution against an independent engine (does
setting these joint values in a *different* physics engine really put the
end effector where retarget.py claims?), plus joint-limit and finite-value
checks. There is no PID/actuation here — see module docstring in
robot_model.py for why one URDF drives both engines.

Per user instruction: use MuJoCo for local/dev verification instead of
Isaac Sim, which is not yet provisioned on the Spark (see ../../STATE.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from ar_contracts import PositionM

from .robot_model import DEFAULT_MODEL, RobotModel

try:
    import mujoco
except ImportError:  # pragma: no cover - exercised via pytest.importorskip
    # See retarget.py's matching comment: deferred so `import ar_datapipe`
    # (and anything transitively importing it) still succeeds on Windows.
    mujoco = None

# Cross-engine tolerance: Pinocchio said "converged" at POSE_ERR_TOL (retarget.py);
# this checks MuJoCo's independent FK agrees the tip really lands there.
TRACKING_ERROR_TOL_M = 0.01


@dataclass(frozen=True)
class ReplayResult:
    achieved_position_m: tuple[float, float, float]
    tracking_error_m: float
    within_tolerance: bool


class MujocoReplay:
    def __init__(self, robot: RobotModel = DEFAULT_MODEL) -> None:
        if mujoco is None:
            raise ImportError(
                "mujoco is not installed on this platform (Linux only — "
                "see packages/ar-datapipe/README.md). ar_datapipe itself "
                "imports fine everywhere; only MujocoReplay needs it."
            )
        self.model = mujoco.MjModel.from_xml_path(str(robot.urdf_path))
        self.data = mujoco.MjData(self.model)
        self.ee_offset_m = np.zeros(3)
        self.ee_body_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, robot.end_effector_frame
        )
        if self.ee_body_id < 0 and robot.mujoco_ee_body is not None:
            # See RobotModel.mujoco_ee_body's docstring: the real end
            # effector frame was welded away by MuJoCo's URDF compiler
            # (a fixed-joint dummy link) -- fall back to the nearest real
            # body and compose the known fixed offset back on in
            # replay_pose, so tracking targets the exact same point
            # Pinocchio's IK does.
            self.ee_body_id = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_BODY, robot.mujoco_ee_body
            )
            if robot.mujoco_ee_offset_m is not None:
                self.ee_offset_m = np.array(robot.mujoco_ee_offset_m)
        if self.ee_body_id < 0:
            raise ValueError(
                f"MuJoCo model compiled from {robot.urdf_path} has no body named "
                f"{robot.end_effector_frame!r}"
                + (f" or fallback {robot.mujoco_ee_body!r}" if robot.mujoco_ee_body else "")
            )

    def _set_joint(self, name: str, value: float) -> None:
        joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint_id < 0:
            raise ValueError(f"MuJoCo model has no joint named {name!r}")
        qpos_adr = self.model.jnt_qposadr[joint_id]
        self.data.qpos[qpos_adr] = value

    def joint_limits_ok(self, joint_names: tuple[str, ...], values: tuple[float, ...]) -> bool:
        for name, value in zip(joint_names, values, strict=True):
            joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            lo, hi = self.model.jnt_range[joint_id]
            if lo < hi and not (lo - 1e-9 <= value <= hi + 1e-9):
                return False
        return True

    def replay_pose(
        self,
        joint_names: tuple[str, ...],
        joint_positions: tuple[float, ...],
        commanded_position_m: PositionM,
    ) -> ReplayResult:
        for name, value in zip(joint_names, joint_positions, strict=True):
            if not math.isfinite(value):
                raise ValueError(f"joint {name!r} value is not finite: {value}")
            self._set_joint(name, value)

        mujoco.mj_forward(self.model, self.data)
        body_pos = self.data.xpos[self.ee_body_id]
        if np.any(self.ee_offset_m):
            body_rot = self.data.xmat[self.ee_body_id].reshape(3, 3)
            achieved = tuple(float(c) for c in body_pos + body_rot @ self.ee_offset_m)
        else:
            achieved = tuple(float(c) for c in body_pos)
        error = float(
            np.linalg.norm(np.array(achieved) - np.array(commanded_position_m))
        )
        return ReplayResult(
            achieved_position_m=achieved,
            tracking_error_m=error,
            within_tolerance=error <= TRACKING_ERROR_TOL_M,
        )
