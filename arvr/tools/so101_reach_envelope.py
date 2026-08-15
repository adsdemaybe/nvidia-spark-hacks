"""Measure the real SO-101's reachable envelope, without Pinocchio.

`ar_datapipe.retarget.IkSolver` is the authority on whether a target is
reachable, but Pinocchio is Linux-only and absent on the Windows dev machine
(see packages/ar-datapipe/README.md). That is not a reason to guess where a
task's objects go: the URDF carries every joint origin, rotation and limit,
and forward kinematics over five revolute joints is a few lines of numpy.

So this samples the joint space, runs FK, and reports the envelope the tool
frame can actually occupy. It is used to place the ball-sorting scene
(packages/xr-web/src/sortLayout.ts) on geometry the arm can reach rather than
on a spec's round numbers -- "validate actual robot reach, do not fake
reachability".

FK here is a measurement tool, not a second implementation of the
retargeter: nothing in the pipeline imports it, and IkSolver remains the
thing that decides accept/reject at verification time.

    uv run --no-sync python tools/so101_reach_envelope.py
"""

from __future__ import annotations

import itertools
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

URDF = Path(__file__).resolve().parents[1] / "fixtures/robot/so101_real/so101_new_calib.urdf"
# ar_datapipe.robot_model's END_EFFECTOR_FRAME -- the tool point the
# retargeter drives, so the envelope has to be measured at the same frame.
END_EFFECTOR_LINK = "gripper_frame_link"
BASE_LINK = "base_link"


def rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]
    )


def axis_angle_to_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    norm = np.linalg.norm(axis)
    if norm == 0:
        return np.eye(3)
    x, y, z = axis / norm
    c, s = math.cos(angle), math.sin(angle)
    return np.array(
        [
            [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
            [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
            [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
        ]
    )


class Joint:
    def __init__(self, element: ET.Element) -> None:
        self.name = element.get("name", "")
        self.type = element.get("type", "fixed")
        self.parent = element.find("parent").get("link")  # type: ignore[union-attr]
        self.child = element.find("child").get("link")  # type: ignore[union-attr]
        origin = element.find("origin")
        xyz = (origin.get("xyz", "0 0 0") if origin is not None else "0 0 0").split()
        rpy = (origin.get("rpy", "0 0 0") if origin is not None else "0 0 0").split()
        self.xyz = np.array([float(v) for v in xyz])
        self.rpy = np.array([float(v) for v in rpy])
        axis = element.find("axis")
        axis_xyz = axis.get("xyz", "0 0 1") if axis is not None else "0 0 1"
        self.axis = np.array([float(v) for v in axis_xyz.split()])
        limit = element.find("limit")
        self.lower = float(limit.get("lower", 0.0)) if limit is not None else 0.0
        self.upper = float(limit.get("upper", 0.0)) if limit is not None else 0.0


def load_chain() -> list[Joint]:
    root = ET.parse(URDF).getroot()
    joints = {}
    for element in root.findall("joint"):
        joint = Joint(element)
        joints.setdefault(joint.child, joint)

    chain: list[Joint] = []
    link = END_EFFECTOR_LINK
    while link != BASE_LINK:
        joint = joints.get(link)
        if joint is None:
            raise SystemExit(f"no joint produces link {link!r}")
        chain.append(joint)
        link = joint.parent
    chain.reverse()
    return chain


def forward_kinematics(chain: list[Joint], angles: dict[str, float]) -> np.ndarray:
    position = np.zeros(3)
    rotation = np.eye(3)
    for joint in chain:
        rotation_static = rpy_to_matrix(*joint.rpy)
        position = position + rotation @ joint.xyz
        rotation = rotation @ rotation_static
        if joint.type in ("revolute", "continuous"):
            rotation = rotation @ axis_angle_to_matrix(joint.axis, angles.get(joint.name, 0.0))
    return position


def solve_position_only(
    chain: list[Joint],
    actuated: list[Joint],
    target: np.ndarray,
    seeds: list[np.ndarray],
    tolerance_m: float = 0.005,
    iterations: int = 300,
) -> float:
    """Damped least squares, position error only. Returns the best residual.

    Position-only because this arm has 5 actuated joints: position and an
    arbitrary orientation are only jointly reachable on a lower-dimensional
    manifold, so a 6-DOF solve routinely fails on targets whose position is
    trivially reachable. IkSolver makes the same choice for the same reason
    (`position_only`, keyed off arm_dof) -- this mirrors it rather than
    inventing a different notion of reachable.
    """
    lower = np.array([j.lower for j in actuated])
    upper = np.array([j.upper for j in actuated])
    best = math.inf

    for seed in seeds:
        q = np.clip(seed.copy(), lower, upper)
        for _ in range(iterations):
            angles = {j.name: float(v) for j, v in zip(actuated, q, strict=True)}
            current = forward_kinematics(chain, angles)
            error = target - current
            residual = float(np.linalg.norm(error))
            if residual < tolerance_m:
                return residual

            # Numerical Jacobian: 5 columns, cheap enough at this scale and
            # free of a hand-derived analytic form that could be quietly wrong.
            jacobian = np.zeros((3, len(actuated)))
            step = 1e-6
            for col, joint in enumerate(actuated):
                perturbed = dict(angles)
                perturbed[joint.name] = angles[joint.name] + step
                jacobian[:, col] = (forward_kinematics(chain, perturbed) - current) / step

            damping = 1e-4
            jjt = jacobian @ jacobian.T + damping * np.eye(3)
            dq = jacobian.T @ np.linalg.solve(jjt, error)
            q = np.clip(q + dq, lower, upper)

        angles = {j.name: float(v) for j, v in zip(actuated, q, strict=True)}
        best = min(best, float(np.linalg.norm(target - forward_kinematics(chain, angles))))

    return best


def main() -> None:
    chain = load_chain()
    actuated = [j for j in chain if j.type in ("revolute", "continuous")]
    print(f"chain: {' -> '.join(j.name for j in chain)}")
    print(f"actuated joints: {[j.name for j in actuated]}\n")

    # A coarse grid bounds the envelope; IK below decides individual points.
    samples = [np.linspace(j.lower, j.upper, 9) for j in actuated]
    points = []
    for combo in itertools.product(*samples):
        angles = {j.name: float(v) for j, v in zip(actuated, combo, strict=True)}
        points.append(forward_kinematics(chain, angles))
    cloud = np.array(points)

    radii = np.linalg.norm(cloud, axis=1)
    print(f"poses sampled       {len(cloud)}")
    print(f"tool radius from base   min {radii.min():.3f} m   max {radii.max():.3f} m")
    for axis, name in enumerate("xyz"):
        print(f"  {name}: [{cloud[:, axis].min():+.3f}, {cloud[:, axis].max():+.3f}]")

    rng = np.random.default_rng(20260815)
    lower = np.array([j.lower for j in actuated])
    upper = np.array([j.upper for j in actuated])
    seeds = [np.zeros(len(actuated))] + [
        lower + rng.random(len(actuated)) * (upper - lower) for _ in range(5)
    ]

    print("\nposition-only IK residual (cm). '.' = converged under 0.5cm:")
    ys = (-0.24, -0.18, -0.12, -0.06, 0.0, 0.06, 0.12, 0.18, 0.24)
    for table_z in (0.10, 0.14, 0.18):
        print(f"  z = {table_z:.2f} m" + "".join(f"{y:+7.2f}" for y in ys))
        for x in (0.14, 0.18, 0.22, 0.26, 0.30, 0.34, 0.38):
            row = []
            for y in ys:
                residual = solve_position_only(
                    chain, actuated, np.array([x, y, table_z]), seeds
                )
                row.append("      ." if residual < 0.005 else f"{residual * 100:7.1f}")
            print(f"  x={x:.2f}       " + "".join(row))
        print()


if __name__ == "__main__":
    main()
