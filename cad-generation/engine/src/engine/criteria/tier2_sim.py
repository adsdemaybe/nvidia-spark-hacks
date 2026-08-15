"""Tier 2 (§3): MuJoCo contact simulation — the first tier where the design
has to survive gravity and time rather than satisfy an equation at one pose.

All three results come from one registered generator because they share a
compiled model, and compiling is the expensive part. That is also why they are
tier 2 and not tier 0: seconds per candidate, not milliseconds.

Thresholds carried over from the prototype, which measured them against a rover
that actually worked — 6 degrees of settled tilt, 40 mm of travel under two
seconds of torque. They are engineering policy, not physics, and are stated here
rather than buried in the simulator so that changing the requirement is an
obvious edit rather than a silent one.
"""

from __future__ import annotations

import math

from engine.catalogue import MotorSpec, resolve as resolve_catalogue
from engine.criteria.assembly_checks import _ground_contact_joints, rigid_groups
from engine.criteria.base import CriterionResult
from engine.criteria.registry import register
from engine.ir import RobotIR
from engine.kinematics import configured_frames
from engine.mass_properties import MassProperties

_SETTLE_TILT_MAX = math.radians(6.0)
_DRIVE_MIN_TRAVEL = 0.04  # m under _DRIVE_STEPS of commanded torque
_MASS_TOLERANCE = 0.01  # 1% — see `sim_loads` on why this is checked at all
_DUTY = 0.6  # fraction of stall torque a stepper is commanded at; stall is not a working point


def _simulatable_mass(ir: RobotIR, mass_props: dict[str, MassProperties]) -> tuple[float, str]:
    """The mass MuJoCo should end up with, and why.

    For a floating base that is the whole robot. For a fixed base it is the whole
    robot *minus* the root body, because MuJoCo welds a URDF root to the world
    and a welded body carries no mass in the model — correctly, since it is the
    bench. Comparing against the full authored mass there would report a 24%
    shortfall on every bench arm forever.
    """
    total = sum(mp.mass for mp in mass_props.values())
    if ir.base == "floating":
        return total, "whole robot"
    welded = sum(mass_props[link_id].mass for link_id in rigid_groups(ir)[ir.root_link])
    return total - welded, f"whole robot minus the {welded:.4f}kg welded base"


def _drive_torques(
    ir: RobotIR, mass_props: dict[str, MassProperties]
) -> tuple[dict[str, float], list[str]]:
    """Torque to command at each wheel, from the catalogue — never a typed-in number.

    Returns the torques and the full list of ground-contact revolute joints,
    because the difference between "found none" and "found some, none actuated"
    is the difference between a criterion that does not apply and one that fails.

    Wheels are found the same way `wheel_rolls_in_place` finds them (a revolute
    joint whose subtree reaches the ground), so the drive test and the tier-0
    rolling checks are always talking about the same joints.
    """
    home = configured_frames(ir)
    by_id = {j.id: j for j in ir.joints}
    torques: dict[str, float] = {}
    wheels: list[str] = []
    for joint, _radius in _ground_contact_joints(ir, mass_props, home):
        wheels.append(joint.id)
        actuator = by_id[joint.id].actuator
        if actuator is None:
            continue
        motor: MotorSpec = resolve_catalogue(actuator.catalogue, actuator.value)
        torques[joint.id] = motor.stall_torque.value * _DUTY
    return torques, wheels


@register("mujoco_rollout", tier=2)
def _mujoco_rollout(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    # Imported here, not at module scope: importing mujoco is what makes tier 2
    # cost anything, and tier 0 runs on every candidate in the search loop. It
    # also keeps `engine.criteria` importable where MuJoCo is not installed —
    # the API service is exactly that environment.
    try:
        from engine.sim.mujoco_harness import compile_floating, drive, settle, total_mass
    except ImportError as exc:
        # §11 #5: a validation step that did not run is not a pass, and the
        # report has to name the engine that was unavailable. Crashing here
        # would take down a caller that only asked for a verdict.
        detail = f"MuJoCo is not available in this environment, so contact simulation did not run: {exc}"
        return [
            CriterionResult("sim_loads", 1.0, False, "ratio", detail),
            CriterionResult("settles", math.inf, False, "ratio", detail),
            CriterionResult("drives", 0.0, False, "ratio", detail),
        ]

    try:
        model = compile_floating(ir)
    except Exception as exc:  # a model that will not compile is a result, not a crash
        detail = f"MuJoCo rejected the model: {exc}"
        return [
            CriterionResult("sim_loads", 1.0, False, "ratio", detail),
            CriterionResult("settles", math.inf, False, "ratio", "not run — the model did not compile"),
            CriterionResult("drives", 0.0, False, "ratio", "not run — the model did not compile"),
        ]

    results: list[CriterionResult] = []

    # A URDF root is welded to the world by MuJoCo, which drops its mass from the
    # model. `compile_floating` adds a free joint to prevent that; this measures
    # whether it worked instead of trusting that it did. The failure is otherwise
    # invisible — the model compiles, simulates, and quietly omits the chassis.
    simulated, (authored, basis) = total_mass(model), _simulatable_mass(ir, mass_props)
    mass_error = abs(simulated - authored) / authored if authored > 0 else 1.0
    results.append(
        CriterionResult(
            name="sim_loads",
            magnitude=mass_error,
            passed=bool(mass_error <= _MASS_TOLERANCE),
            unit="ratio",
            detail=(
                f"{model.nbody} bodies, {model.nv} dofs; simulated mass {simulated:.4f}kg "
                f"vs {authored:.4f}kg expected ({basis})"
                + ("" if mass_error <= _MASS_TOLERANCE
                   else " — mass was lost in translation, most likely a welded root")
            ),
        )
    )

    settled = settle(model)
    tilt_ratio = settled.tilt_rad / _SETTLE_TILT_MAX
    results.append(
        CriterionResult(
            name="settles",
            magnitude=tilt_ratio,
            passed=bool(not settled.diverged and tilt_ratio <= 1.0 and settled.max_speed < 0.5),
            unit="ratio",
            detail=(
                "diverged — NaN in the state, the model is numerically unstable"
                if settled.diverged
                else (
                    f"tilt {math.degrees(settled.tilt_rad):.1f}deg of "
                    f"{math.degrees(_SETTLE_TILT_MAX):.0f}deg allowed, "
                    f"residual |qvel| {settled.max_speed:.3f}"
                )
            ),
        )
    )

    torques, wheels = _drive_torques(ir, mass_props)
    if not wheels:
        # No ground-contact revolute joint at all: this robot does not drive, the
        # way an arm bolted to a bench does not drive. The criterion does not
        # apply, and emitting a failure would make every stationary manipulator
        # unbuildable — the same trap as a criterion nobody can pass. Silence is
        # safe here only because `EvaluationReport.passed` refuses to call a
        # report with no results a pass; a robot that measures nothing still fails.
        return results
    if not torques:
        # It *does* have wheels, and none of them has a motor. That is a defect
        # in the design, not a category it falls outside of.
        results.append(
            CriterionResult(
                name="drives",
                magnitude=0.0,
                passed=False,
                unit="ratio",
                detail=(
                    f"{len(wheels)} ground-contact revolute joint(s) ({', '.join(wheels)}) "
                    "and not one carries an actuator — there is nothing to drive the robot with"
                ),
            )
        )
        return results

    driven = drive(model, torques)
    travel_ratio = driven.travel / _DRIVE_MIN_TRAVEL
    results.append(
        CriterionResult(
            name="drives",
            magnitude=travel_ratio,
            passed=bool(travel_ratio >= 1.0),
            unit="ratio",
            detail=(
                f"{driven.travel * 1000:.0f}mm of {_DRIVE_MIN_TRAVEL * 1000:.0f}mm required, "
                f"at {driven.torque_applied:.3f}N*m on {len(driven.driven_joints)} joints "
                f"({_DUTY:.0%} of stall)"
            ),
        )
    )
    return results
