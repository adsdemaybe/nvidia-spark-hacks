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
from ar_contracts import OrientationXYZW, PositionM

from .robot_model import DEFAULT_MODEL, RobotModel

try:
    import pinocchio as pin
except ImportError:  # pragma: no cover - exercised via pytest.importorskip
    # Pinocchio is Linux-only here (see package README). Deferring the
    # ImportError to IkSolver.__init__ instead of module import time means
    # `import ar_datapipe` — and anything that transitively imports it, like
    # ar_backend's FastAPI app — still succeeds on Windows; only code paths
    # that actually need IK fail, with a clear message instead of a bare
    # "No module named 'pinocchio'" at an unrelated import line.
    pin = None

MAX_ITERS = 4000
DT = 0.2
DAMPING = 1e-2
MAX_STEP_NORM = 0.3  # rad, per-iteration joint-velocity*DT clamp
POSE_ERR_TOL = 1e-4  # meters/radians (log6 norm)
# Post-convergence nullspace refinement (see the comment in solve()): how
# many extra small steps to try pulling toward the warm-start pose, and how
# big each step is allowed to be. Deliberately small/few — this only needs
# to resolve a local wrist-singularity ambiguity, not do real work.
NULLSPACE_REFINE_ITERS = 50
NULLSPACE_STEP_NORM = 0.05  # rad


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
        if pin is None:
            raise ImportError(
                "pinocchio is not installed on this platform (Linux only — "
                "see packages/ar-datapipe/README.md). ar_datapipe itself "
                "imports fine everywhere; only IkSolver needs it."
            )
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
        # From each joint's URDF <limit velocity="..."/>. Used by
        # pipeline.py to catch the "velocity limits valid" / "no
        # unexplained discontinuity" acceptance gate (spec section 62) —
        # a joint solution that jumps faster than the robot could
        # physically move between two consecutive demo frames.
        self.velocity_limits = self.model.velocityLimit.copy()

    def solve(
        self,
        position_m: PositionM,
        orientation_xyzw: OrientationXYZW,
        q_init: np.ndarray | None = None,
        position_only: bool = False,
    ) -> RetargetResult:
        """`position_only` solves for end-effector position alone, ignoring
        orientation error entirely (first 3 rows of the 6D pose
        error/Jacobian -- log6's first 3 components are linear, last 3
        angular, confirmed against Pinocchio directly, not assumed).

        Needed for any robot with fewer than 6 arm DOF: position AND an
        arbitrary target orientation are only *jointly* reachable on a
        lower-dimensional manifold of the full 6D pose space, not
        independently — a full 6-DOF pose solve routinely fails to
        converge for such an arm even when the position alone is trivially
        reachable (found on the real SO-101, a 5-DOF arm: a target that
        converged from one exact FK-derived pose failed to converge again
        after rounding it to 4 decimal places — the position moved by
        under half a centimeter, but that was enough to leave the
        reachable (position, orientation) manifold entirely). A 6-DOF arm
        is unaffected either way — see the module-level position_only
        selection in ArmRetargeter's callers, keyed off
        RobotCapabilityProfile.arm_dof.
        """
        if not all(math.isfinite(c) for c in position_m):
            raise ValueError("position_m must be finite")
        if not all(math.isfinite(c) for c in orientation_xyzw):
            raise ValueError("orientation_xyzw must be finite")

        target = pin.SE3(_xyzw_to_pin_quat(orientation_xyzw).matrix(), np.array(position_m))
        q = pin.neutral(self.model) if q_init is None else q_init.copy()
        # The regularization target for the post-convergence nullspace
        # refinement below — "stay near where you started" — not the
        # target *pose*, which `target` already is.
        q_reg = q.copy()
        task_dim = 3 if position_only else 6

        err_norm = float("inf")
        for _ in range(MAX_ITERS):
            pin.forwardKinematics(self.model, self.data, q)
            pin.updateFramePlacement(self.model, self.data, self.frame_id)
            current = self.data.oMf[self.frame_id]
            err_full = pin.log6(current.inverse() * target)
            err_vec = err_full.vector[:task_dim]
            err_norm = float(np.linalg.norm(err_vec))
            if err_norm < POSE_ERR_TOL:
                break
            jac_full = pin.computeFrameJacobian(
                self.model, self.data, q, self.frame_id, pin.ReferenceFrame.LOCAL
            )
            jac = jac_full[:task_dim, :]
            jjt = jac @ jac.T + DAMPING * np.eye(task_dim)
            v = jac.T @ np.linalg.solve(jjt, err_vec)
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

        # Post-convergence nullspace refinement, warm-start case only.
        #
        # A non-redundant 6-DOF arm has no *global* nullspace, but one opens
        # up locally at a singularity (e.g. two wrist axes aligning while
        # orientation is held constant across frames, which every TEACH demo
        # does whenever the recorded hand doesn't rotate). Left alone, the
        # solver above is free to land on a different joint-space solution
        # for the *same* end-effector pose on each independent per-frame
        # solve — every individual frame passes IK/joint-limit checks, but
        # consecutive frames can differ by several radians. That was
        # invisible until pipeline.py's velocity/discontinuity gate (spec
        # section 62) started checking for it, at which point it turned out
        # to be silently corrupting the fixture demo's retargeted
        # trajectory. Nudging inside the primary loop (tried first) fights
        # convergence and made even ordinary single-pose IK fail; doing a
        # few small pulls *after* convergence, each verified to not regress
        # pose accuracy, is safe because it only ever moves along directions
        # that don't change the achieved pose.
        if err_norm < POSE_ERR_TOL and q_init is not None:
            for _ in range(NULLSPACE_REFINE_ITERS):
                pin.forwardKinematics(self.model, self.data, q)
                pin.updateFramePlacement(self.model, self.data, self.frame_id)
                current = self.data.oMf[self.frame_id]
                jac_full = pin.computeFrameJacobian(
                    self.model, self.data, q, self.frame_id, pin.ReferenceFrame.LOCAL
                )
                jac = jac_full[:task_dim, :]
                jjt = jac @ jac.T + DAMPING * np.eye(task_dim)
                jac_pinv = jac.T @ np.linalg.solve(jjt, np.eye(task_dim))
                nullspace_proj = np.eye(self.model.nv) - jac_pinv @ jac
                pull = nullspace_proj @ (q_reg - q)
                pull_norm = float(np.linalg.norm(pull))
                if pull_norm < 1e-9:
                    break  # already as close to q_reg as the nullspace allows
                if pull_norm > NULLSPACE_STEP_NORM:
                    pull = pull * (NULLSPACE_STEP_NORM / pull_norm)
                q_candidate = pin.integrate(self.model, q, pull)

                pin.forwardKinematics(self.model, self.data, q_candidate)
                pin.updateFramePlacement(self.model, self.data, self.frame_id)
                candidate_err = pin.log6(
                    self.data.oMf[self.frame_id].inverse() * target
                )
                candidate_err_norm = float(np.linalg.norm(candidate_err.vector[:task_dim]))
                if candidate_err_norm >= POSE_ERR_TOL:
                    # This pull direction would leave the target pose —
                    # not a true nullspace direction at this q. Stop rather
                    # than risk drifting off the converged solution.
                    break
                q, err_norm = q_candidate, candidate_err_norm

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
