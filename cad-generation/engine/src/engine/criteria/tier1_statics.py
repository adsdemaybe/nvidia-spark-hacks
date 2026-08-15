"""Tier 1 (§3): torque budgets at the home configuration.

The tech-stack doc names Pinocchio for this tier (Jacobians, inverse
dynamics, ~10x faster than Drake for the inner optimization loop). Pinocchio
has no prebuilt wheel for Windows and needs a C++ toolchain to build from
source, which this dev machine doesn't have — so this module is a numpy
stand-in with the *same role* (static torque budget check, cheap, every
candidate) and the *same* CriterionResult shape. Swapping in real Pinocchio
inverse dynamics later (e.g. once running on Linux/Spark) means adding a
generator function and registering it at tier=1 — evaluate() and every
caller of it are unaffected, exactly per §2's registry pattern.

Static holding torque only: sum of moments about each revolute joint's axis
from the weight of everything in its downstream subtree, evaluated at the
home configuration (§3's tier-0/1 checks are single-pose analytic, not a
trajectory). Prismatic joints are out of scope until a fixture exercises one
— the catalogue only stocks rotary actuators today (§ catalogue.py).
"""

from __future__ import annotations

import numpy as np

from engine.catalogue import MotorSpec, resolve as resolve_catalogue
from engine.criteria.base import CriterionResult
from engine.criteria.registry import register
from engine.ir import RobotIR
from engine.kinematics import joint_world_frame, link_geometry_transform, subtree_links
from engine.mass_properties import MassProperties

_GRAVITY = 9.80665  # m/s^2


@register("joint_torque_budget", tier=1)
def _joint_torque_budget(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    results: list[CriterionResult] = []

    for joint in ir.joints:
        if joint.kind != "revolute" or joint.actuator is None:
            continue

        motor: MotorSpec = resolve_catalogue(joint.actuator.catalogue, joint.actuator.value)
        joint_frame = joint_world_frame(ir, joint)
        joint_pos = joint_frame[:3, 3]
        axis_world = joint_frame[:3, :3] @ np.array(joint.axis.as_tuple(), dtype=float)
        axis_world /= np.linalg.norm(axis_world)

        torque = 0.0
        for link_id in subtree_links(ir, joint.child):
            mp = mass_props[link_id]
            transform = link_geometry_transform(ir, link_id)
            world_com = transform[:3, :3] @ np.array(mp.com.as_tuple()) + transform[:3, 3]
            lever_arm = world_com - joint_pos
            weight = np.array([0.0, 0.0, -mp.mass * _GRAVITY])
            torque += np.dot(np.cross(lever_arm, weight), axis_world)

        required = abs(float(torque))
        available = motor.stall_torque.value
        margin = (available - required) / available if available > 0 else float("-inf")

        results.append(
            CriterionResult(
                name=f"joint_torque_budget[{joint.id}]",
                magnitude=margin,
                passed=bool(margin > 0),
                unit="ratio",
                detail=(
                    f"required={required:.4f}N*m available={available:.4f}N*m "
                    f"(actuator={joint.actuator.value})"
                ),
            )
        )
    return results
