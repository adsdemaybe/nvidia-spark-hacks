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
from engine.electrical import actuator_operating_point
from engine.criteria.registry import register
from engine.ir import RobotIR
from engine.kinematics import (
    joint_world_frame,
    link_frames,
    link_geometry_transform,
    subtree_links,
)
from engine.mass_properties import MassProperties

_GRAVITY = 9.80665  # m/s^2
# See engine.criteria.builtin._DEGENERATE — finite so the report stays valid JSON.
_NO_TORQUE_AVAILABLE = -1.0e6


@register("joint_torque_budget", tier=1)
def _joint_torque_budget(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    results: list[CriterionResult] = []
    frames = link_frames(ir)

    for joint in ir.joints:
        if joint.kind != "revolute" or joint.actuator is None:
            continue

        motor: MotorSpec = resolve_catalogue(joint.actuator.catalogue, joint.actuator.value)
        joint_frame = joint_world_frame(ir, joint, frames)
        joint_pos = joint_frame[:3, 3]
        axis_world = joint_frame[:3, :3] @ np.array(joint.axis.as_tuple(), dtype=float)
        axis_world /= np.linalg.norm(axis_world)

        torque = 0.0
        for link_id in subtree_links(ir, joint.child):
            mp = mass_props[link_id]
            transform = link_geometry_transform(ir, link_id, frames)
            world_com = transform[:3, :3] @ np.array(mp.com.as_tuple()) + transform[:3, 3]
            lever_arm = world_com - joint_pos
            weight = np.array([0.0, 0.0, -mp.mass * _GRAVITY])
            torque += np.dot(np.cross(lever_arm, weight), axis_world)

        required = abs(float(torque))

        # §3 (v3): torque available is `curve(voltage_at_motor) x ratio x eta`,
        # and `voltage_at_motor` accounts for battery sag and harness drop. When
        # the robot has an electronics subsystem those numbers exist, and using
        # the datasheet figure at nominal voltage instead would be the exact
        # failure the model was added to prevent — a rail that cannot deliver
        # passes here and stalls on the bench.
        #
        # Static hold, so speed is zero. This is a holding-torque check, not a
        # motion one; the speed term arrives with trajectory criteria.
        op = actuator_operating_point(ir, joint.id, speed_rad_s=0.0)
        if op is not None:
            available = op.torque_nm
            provenance = op.provenance
            # The first note says how the number was arrived at — interpolated
            # from a curve, taken off a linear line, or not scaled at all. Saying
            # "at 10.91 V" while silently reporting an unscaled datasheet figure
            # would be worse than saying nothing: it reads as though the voltage
            # was accounted for.
            basis = op.notes[0] if op.notes else "no basis recorded"
            source = (
                f"at {op.voltage_at_motor_v:.2f}V on rail "
                f"{ir.electronics.joint_rail[joint.id]!r} — {basis}"
            )
        else:
            available = motor.stall_torque.value
            provenance = motor.stall_torque.provenance.status
            source = (
                "at the catalogue's stated condition — this joint is on no rail, "
                "so the actual voltage at the motor is unmodelled"
                + (f" ({motor.condition})" if motor.condition else "")
            )

        margin = (available - required) / available if available > 0 else _NO_TORQUE_AVAILABLE

        results.append(
            CriterionResult(
                name=f"joint_torque_budget[{joint.id}]",
                magnitude=margin,
                passed=bool(margin > 0),
                unit="ratio",
                detail=(
                    f"required={required:.4f}N*m available={available:.4f}N*m "
                    f"(actuator={joint.actuator.value}, {source})"
                ),
                provenance=provenance,
            )
        )
    return results
