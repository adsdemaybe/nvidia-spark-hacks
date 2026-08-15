"""Damped least-squares inverse kinematics (STRUCT_2.md 13D).

Deterministic, as the spec requires: same target, same seed pose, same joints
out. No randomness and no retry-with-jitter, because a demonstration that
retargets differently on two runs is not reproducible training data.

The spec suggests Pinocchio. This uses the Jacobian MuJoCo already computes for
the model we are simulating, which avoids a second kinematic description that
could disagree with the one physics is using -- and avoids a dependency that is
painful to install on Windows. If Pinocchio arrives later, the contract here
(target pose in, joints plus an honest error out) is what it has to satisfy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import mujoco
import numpy as np

if TYPE_CHECKING:
    from .twin import MujocoTwinSource

# Below this the pose counts as reached. 5 mm is well inside the 5 cm anchor
# budget in STRUCT_2.md 49 and tighter than any gripper tolerance we model.
TOLERANCE_M = 0.005
MAX_ITERATIONS = 200
# Damping keeps the step finite near singularities, which is where a naive
# pseudo-inverse produces the NaNs that 62 forbids.
DAMPING = 0.08
MAX_STEP_RAD = 0.20


@dataclass(frozen=True)
class IKResult:
    ok: bool
    joint_positions: tuple[float, ...]
    position_error_m: float
    iterations: int

    @property
    def unreachable(self) -> bool:
        return not self.ok


def solve_ik(
    sim: MujocoTwinSource,
    target_m: tuple[float, float, float],
    *,
    tolerance_m: float = TOLERANCE_M,
    max_iterations: int = MAX_ITERATIONS,
) -> IKResult:
    """Solve for joint positions putting the end effector at `target_m`.

    Runs on a scratch copy of the model state, so calling this never disturbs
    the simulation the caller is stepping.
    """
    model = sim.model
    data = mujoco.MjData(model)
    data.qpos[:] = sim.raw_qpos()
    mujoco.mj_forward(model, data)

    dof_indices = np.asarray(sim.joint_dof_indices(), dtype=int)
    qpos_indices = np.asarray(sim.joint_qpos_indices(), dtype=int)
    lower, upper = sim.joint_limits()
    lower_a = np.asarray(lower, dtype=float)
    upper_a = np.asarray(upper, dtype=float)

    target = np.asarray(target_m, dtype=float)
    jac_pos = np.zeros((3, model.nv))
    jac_rot = np.zeros((3, model.nv))

    error = float("inf")
    iteration = 0

    for iteration in range(1, max_iterations + 1):  # noqa: B007
        current = data.site_xpos[sim.site_id]
        delta = target - current
        error = float(np.linalg.norm(delta))
        if error < tolerance_m:
            break

        mujoco.mj_jacSite(model, data, jac_pos, jac_rot, sim.site_id)
        j = jac_pos[:, dof_indices]

        # Damped least squares: J^T (J J^T + λ²I)^-1 e
        jjt = j @ j.T + (DAMPING**2) * np.eye(3)
        step = j.T @ np.linalg.solve(jjt, delta)

        # Clamp per-joint travel so a large error cannot fling the arm across
        # its workspace in one iteration.
        largest = float(np.max(np.abs(step))) if step.size else 0.0
        if largest > MAX_STEP_RAD:
            step *= MAX_STEP_RAD / largest

        q = data.qpos[qpos_indices] + step
        data.qpos[qpos_indices] = np.clip(q, lower_a, upper_a)
        mujoco.mj_forward(model, data)

    solution = tuple(float(v) for v in data.qpos[qpos_indices])
    if not all(np.isfinite(solution)):
        # Should be unreachable given the damping, but a NaN escaping into a
        # dataset is exactly what 62 exists to prevent.
        return IKResult(False, tuple(sim.joint_positions()), float("inf"), iteration)

    return IKResult(
        ok=error < tolerance_m,
        joint_positions=solution,
        position_error_m=error,
        iterations=iteration,
    )
