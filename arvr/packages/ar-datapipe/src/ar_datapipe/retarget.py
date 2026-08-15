"""Deterministic inverse kinematics — spec section 13D.

    Input: end-effector target pose (position_m, orientation_xyzw)
    Output: robot joint target (+ convergence/limit diagnostics)

Uses Pinocchio's standard damped closed-loop IK (CLIK) pattern: iterate
`v = J^T (JJ^T + damp*I)^-1 * log6(error)`, integrate `q += v*dt`, repeat
until the pose error is small or iterations run out. This is deterministic
given a fixed seed configuration — no randomness, no LLM, matches rule
85.9's spirit (verification must never be a judgment call).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pinocchio as pin
from ar_contracts import OrientationXYZW, PositionM

from .robot_model import DEFAULT_MODEL, RobotModel

MAX_ITERS = 1500
DT = 0.2
DAMPING = 1e-2
MAX_STEP_NORM = 0.3  # rad, per-iteration joint-velocity*DT clamp
POSE_ERR_TOL = 1e-4  # meters/radians (log6 norm)


@dataclass(frozen=True)
class RetargetResult:
    converged: bool
    joint_positions: tuple[float, ...]
    joint_names: tuple[str, ...]
    within_limits: bool
    final_error_norm: float


def _xyzw_to_pin_quat(orientation_xyzw: OrientationXYZW) -> pin.Quaternion:
    x, y, z, w = orientation_xyzw
    return pin.Quaternion(w, x, y, z)


class IkSolver:
    """Wraps a loaded Pinocchio model so repeated IK calls (one per demo
    frame) don't re-parse the URDF every time."""

    def __init__(self, robot: RobotModel = DEFAULT_MODEL) -> None:
        self.model = pin.buildModelFromUrdf(str(robot.urdf_path))
        self.data = self.model.createData()
        self.frame_id = self.model.getFrameId(robot.end_effector_frame)
        if self.frame_id >= self.model.nframes:
            raise ValueError(
                f"URDF {robot.urdf_path} has no frame named "
                f"{robot.end_effector_frame!r}"
            )
        self.joint_names = tuple(
            name for name in self.model.names if name != "universe"
        )
        self.lower_limits = self.model.lowerPositionLimit.copy()
        self.upper_limits = self.model.upperPositionLimit.copy()

    def solve(
        self,
        position_m: PositionM,
        orientation_xyzw: OrientationXYZW,
        q_init: np.ndarray | None = None,
    ) -> RetargetResult:
        if not all(math.isfinite(c) for c in position_m):
            raise ValueError("position_m must be finite")
        if not all(math.isfinite(c) for c in orientation_xyzw):
            raise ValueError("orientation_xyzw must be finite")

        target = pin.SE3(_xyzw_to_pin_quat(orientation_xyzw).matrix(), np.array(position_m))
        q = pin.neutral(self.model) if q_init is None else q_init.copy()

        err_norm = float("inf")
        for _ in range(MAX_ITERS):
            pin.forwardKinematics(self.model, self.data, q)
            pin.updateFramePlacement(self.model, self.data, self.frame_id)
            current = self.data.oMf[self.frame_id]
            err = pin.log6(current.inverse() * target)
            err_norm = float(np.linalg.norm(err.vector))
            if err_norm < POSE_ERR_TOL:
                break
            jac = pin.computeFrameJacobian(
                self.model, self.data, q, self.frame_id, pin.ReferenceFrame.LOCAL
            )
            jjt = jac @ jac.T + DAMPING * np.eye(6)
            v = jac.T @ np.linalg.solve(jjt, err.vector)
            step = v * DT
            # Without this clamp, a large initial error can produce a huge
            # first step that overshoots past the target and spirals through
            # multiple full turns of a revolute joint before "converging" —
            # kinematically valid (FK is periodic) but a nonsense multi-turn
            # solution that then fails the joint-limit check for no good
            # reason. Clamping keeps every step physically small.
            step_norm = float(np.linalg.norm(step))
            if step_norm > MAX_STEP_NORM:
                step = step * (MAX_STEP_NORM / step_norm)
            q = pin.integrate(self.model, q, step)

        # Revolute joints are periodic in the FK sense (q and q + 2*pi*k give
        # the same end-effector pose); wrap into (-pi, pi] so the *reported*
        # solution is the natural one instead of an arbitrary multi-turn
        # equivalent, before checking against the URDF's declared limits.
        q = np.array([((qi + math.pi) % (2 * math.pi)) - math.pi for qi in q])
        converged = err_norm < POSE_ERR_TOL
        within_limits = bool(
            np.all(q >= self.lower_limits) and np.all(q <= self.upper_limits)
        )
        return RetargetResult(
            converged=converged,
            joint_positions=tuple(float(x) for x in q),
            joint_names=self.joint_names,
            within_limits=within_limits,
            final_error_norm=err_norm,
        )
