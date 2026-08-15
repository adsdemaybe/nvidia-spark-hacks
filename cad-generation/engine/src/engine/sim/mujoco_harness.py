"""MuJoCo rollouts — tier 2 (§3): does the robot stand up, and does it move?

Everything above tier 2 is analytic: it evaluates one static pose and trusts
the pose. This is the first tier where the design has to survive contact,
gravity and time, and it is where designs that satisfy every equation still
fall over.

Zero I/O, like the rest of the engine (§11 non-negotiable #7): the URDF is
handed to `MjSpec.from_string` as text and never touches the filesystem.

The two traps this module exists to avoid, both inherited from the prototype
and both silent:

1. **MuJoCo welds a URDF root to the world.** A URDF has no notion of a
   floating base, so the root body is fixed and its mass is dropped from the
   model entirely. The rover measured here weighed 0.383 kg against an authored
   0.777 kg, and nothing reported it — it simply could not move, and the half of
   its mass that mattered was not being simulated. `compile_floating` adds the
   free joint, and `sim_loads` verifies the mass survived rather than assuming
   this function stayed correct.
2. **A welded root also passes the settle test perfectly.** A robot bolted to
   the sky never tips. Trap 1 makes trap 2 look like success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from engine.export.urdf import to_urdf
from engine.ir import RobotIR

# MuJoCo's default timestep is 2 ms.
_SETTLE_STEPS = 3000  # ~6 s: long enough for a dropped chassis to stop bouncing
_PRE_DRIVE_STEPS = 1500  # ~3 s: settle before measuring travel, so the drop isn't counted
_DRIVE_STEPS = 1000  # ~2 s of commanded torque
_DROP_GAP = 0.01  # m above the ground the robot is released from
_GROUND_SIZE = 5.0  # m half-extent of the test plane


@dataclass(frozen=True)
class SettleResult:
    tilt_rad: float
    max_speed: float  # max |qvel| component at the end, m/s or rad/s
    diverged: bool  # NaN in the state — the model blew up numerically


@dataclass(frozen=True)
class DriveResult:
    travel: float  # m, in the ground plane
    torque_applied: float  # N*m per driven joint
    driven_joints: tuple[str, ...]


def compile_floating(ir: RobotIR) -> mujoco.MjModel:
    """Compile `ir` into a MuJoCo model standing on a ground plane, free to move.

    Raises whatever MuJoCo raises on an invalid model — a compile failure is a
    real result (`sim_loads`), not something to swallow here.
    """
    spec = mujoco.MjSpec.from_string(to_urdf(ir))

    roots = [b for b in spec.bodies if b.name == ir.root_link]
    if not roots:
        raise ValueError(f"root link {ir.root_link!r} is not a body in the compiled spec")
    # A fixed-base robot *wants* the weld MuJoCo applies to a URDF root — it is
    # bolted to a bench. Adding a free joint would drop it on the floor and call
    # the resulting sprawl a design fault. Its root mass is genuinely absent from
    # the model in that case, which is correct and which `sim_loads` accounts for.
    if ir.base == "floating":
        roots[0].add_freejoint()

    spec.worldbody.add_geom(
        type=mujoco.mjtGeom.mjGEOM_PLANE,
        size=[_GROUND_SIZE, _GROUND_SIZE, 0.1],
        pos=[0.0, 0.0, 0.0],
    )
    return spec.compile()


def has_free_base(model: mujoco.MjModel) -> bool:
    """Whether the model's root actually floats.

    Read from the compiled model rather than passed in, so it cannot disagree
    with what is being simulated.
    """
    return bool(model.njnt > 0 and model.jnt_type[0] == mujoco.mjtJoint.mjJNT_FREE)


def _spawn(model: mujoco.MjModel) -> mujoco.MjData:
    """Place the robot just above the plane and let go.

    The drop height is derived from the model's own geometry rather than from a
    wheel diameter someone typed in: a robot whose lowest point is not a wheel
    is still released from just above the ground, and a robot spawned inside the
    floor is launched by the contact solver rather than settling.
    """
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    # Nothing to drop, and `qpos[2]` is not a height on a fixed-base robot — it
    # is that robot's third joint angle. Shifting it would silently bend the arm
    # before the test began.
    if not has_free_base(model):
        return data

    # geom_rbound is the radius of a sphere containing the geom — conservative,
    # so this can only release the robot too high, never inside the floor.
    lowest = min(
        (float(data.geom_xpos[i][2]) - float(model.geom_rbound[i]) for i in range(model.ngeom)),
        default=0.0,
    )
    data.qpos[2] += _DROP_GAP - lowest
    mujoco.mj_forward(model, data)
    return data


def _base_tilt(quat: np.ndarray) -> float:
    """Roll/pitch of the free base, whichever is larger, in radians."""
    w, x, y, z = (float(v) for v in quat)
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    return max(abs(roll), abs(pitch))


def total_mass(model: mujoco.MjModel) -> float:
    return float(np.sum(model.body_mass))


def settle(model: mujoco.MjModel) -> SettleResult:
    """Drop the robot on a flat plane and see how it ends up."""
    data = _spawn(model)
    for _ in range(_SETTLE_STEPS):
        mujoco.mj_step(model, data)

    diverged = bool(np.isnan(data.qpos).any() or np.isnan(data.qvel).any())
    if diverged:
        return SettleResult(tilt_rad=math.pi, max_speed=float("inf"), diverged=True)

    # A bolted-down robot has no base orientation to lose, and qpos[3:7] is not
    # a quaternion there — it is four joint angles that would read as a garbage
    # tilt. What settling means for a fixed base is that the joints stopped.
    tilt = _base_tilt(data.qpos[3:7]) if has_free_base(model) else 0.0
    return SettleResult(
        tilt_rad=tilt,
        max_speed=float(np.abs(data.qvel).max()) if model.nv else 0.0,
        diverged=False,
    )


def drive(model: mujoco.MjModel, joint_torques: dict[str, float]) -> DriveResult:
    """Settle, then command `joint_torques` and measure how far the base moved.

    Travel is measured in the ground plane, not along X. A robot whose wheels
    are mounted to drive it along Y is a normal thing to author and would
    otherwise be reported as having gone nowhere.
    """
    data = _spawn(model)
    for _ in range(_PRE_DRIVE_STEPS):
        mujoco.mj_step(model, data)

    start = np.array(data.qpos[0:2], dtype=float)

    dofs: list[tuple[int, float]] = []
    for joint_name, torque in joint_torques.items():
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        if jid >= 0:
            dofs.append((int(model.jnt_dofadr[jid]), torque))

    for _ in range(_DRIVE_STEPS):
        data.qfrc_applied[:] = 0.0
        for dof, torque in dofs:
            data.qfrc_applied[dof] = torque
        mujoco.mj_step(model, data)
    data.qfrc_applied[:] = 0.0

    if np.isnan(data.qpos).any():
        return DriveResult(0.0, 0.0, tuple(joint_torques))

    travel = float(np.linalg.norm(np.array(data.qpos[0:2], dtype=float) - start))
    return DriveResult(
        travel=travel,
        torque_applied=max(joint_torques.values(), default=0.0),
        driven_joints=tuple(joint_torques),
    )
