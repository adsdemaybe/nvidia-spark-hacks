"""Tier 1: what the arm costs the robot when it is stretched out.

`payload` and `backlash` share one registered generator because they share the
expensive part — a sweep over arm configurations looking for the worst pose.
Evaluating them at the home configuration instead would be cheaper and close to
meaningless: an arm folded up is stable and precise, and neither criterion is
asking about that arm.

Both are carried over from the prototype, where they were the two criteria that
actually drove the design. The prototype could only ask them of one hard-coded
rover; here they are asked of whatever topology the IR describes, and both
return no result at all for a robot with no arm rather than inventing one.

What neither measures, stated so a pass is not mistaken for a working arm:
deflection under load (links are rigid here), dynamic torque while accelerating,
and whether the gripper can actually hold the payload it is rated for.
"""

from __future__ import annotations

import math

import numpy as np

from engine.catalogue import MotorSpec, resolve as resolve_catalogue
from engine.criteria.assembly_checks import _ground_contact_joints
from engine.criteria.base import CriterionResult
from engine.criteria.builtin import _world_bbox
from engine.criteria.registry import register
from engine.ir import RobotIR
from engine.kinematics import (
    configured_frames,
    joint_world_frame,
    link_geometry_transform,
    subtree_links,
)
from engine.mass_properties import MassProperties

# Engineering policy, not physics — the prototype's targets, kept so the numbers
# stay comparable across the port.
_TARGET_PAYLOAD = 0.5  # kg at the worst-case reachable pose
_MAX_BACKLASH = 0.002  # m of end-effector slop

_MAX_POSES = 2048  # sweep budget; see `_sweep_angles` for what happens at the cap
_SAMPLES_PER_JOINT = 5
_UNBOUNDED_SWEEP = math.pi  # +/- this, for an arm joint that declares no bounds

_ARCMIN_TO_RAD = math.pi / (180.0 * 60.0)


def _to_radians(quantity) -> float:
    unit = quantity.unit.lower()
    if unit in ("rad", "radian", "radians"):
        return quantity.value
    if unit in ("arcmin", "arcminute", "arcminutes", "'"):
        return quantity.value * _ARCMIN_TO_RAD
    if unit in ("deg", "degree", "degrees"):
        return math.radians(quantity.value)
    raise ValueError(f"cannot read {unit!r} as an angle; expected rad, deg or arcmin")


def _wheel_links(ir: RobotIR, mass_props: dict[str, MassProperties], home) -> set[str]:
    """Every link belonging to a ground-contact (wheel) subtree, plus the joints."""
    links: set[str] = set()
    for joint, _radius in _ground_contact_joints(ir, mass_props, home):
        links.update(subtree_links(ir, joint.child))
    return links


def _arm_joints(ir: RobotIR, wheel_links: set[str]) -> list:
    """Revolute joints that move the arm — i.e. every revolute joint that is not
    driving a wheel. A prismatic arm would belong here too once a generator
    builds one; today the catalogue stocks only rotary actuators.
    """
    return [j for j in ir.joints if j.kind == "revolute" and j.child not in wheel_links]


def _end_effectors(ir: RobotIR, wheel_links: set[str]) -> list[str]:
    """Leaf links that are not wheels. These are what the arm carries a load on."""
    parents = {joint.parent for joint in ir.joints}
    return [
        link.id
        for link in ir.links
        if link.id not in parents and link.id not in wheel_links and link.id != ir.root_link
    ]


def _support_box(
    ir: RobotIR, mass_props: dict[str, MassProperties], home, wheel_links: set[str]
) -> tuple[np.ndarray, np.ndarray] | None:
    """The footprint the robot tips about: the horizontal extent of its wheels.

    Taken from the wheels only, and at the home pose, because the wheels do not
    move when the arm does. Deriving it from whatever happens to be lowest would
    let a drooping arm touching the floor count as extra support, which is the
    opposite of the truth — it is the load that tips the robot.
    """
    boxes = [
        _world_bbox(link_geometry_transform(ir, link_id, home), mass_props[link_id])
        for link_id in wheel_links
    ]
    if not boxes:
        return None
    lo = np.min([b[0][:2] for b in boxes], axis=0)
    hi = np.max([b[1][:2] for b in boxes], axis=0)
    return lo, hi


def _ray_box_exit(origin: np.ndarray, direction: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    """Distance from `origin` (inside the box) to the boundary along `direction`."""
    distances = []
    for axis in (0, 1):
        if abs(direction[axis]) < 1e-12:
            continue
        bound = hi[axis] if direction[axis] > 0 else lo[axis]
        distances.append((bound - origin[axis]) / direction[axis])
    return min(distances) if distances else 0.0


def _sweep_angles(joints) -> list[dict[str, float]]:
    """A coarse grid over the arm's configuration space.

    Coarse on purpose: this is tier 1, and the question is which pose is worst,
    not exactly how bad it is there. The per-joint sample count is reduced until
    the total fits `_MAX_POSES`, and a sweep that had to be thinned says so in
    the criterion's detail rather than quietly covering less.
    """
    if not joints:
        return [{}]

    samples = _SAMPLES_PER_JOINT
    while samples > 2 and samples ** len(joints) > _MAX_POSES:
        samples -= 1

    per_joint: list[list[tuple[str, float]]] = []
    for joint in joints:
        if joint.limits is not None and joint.limits.bounded:
            lo, hi = joint.limits.lower.value, joint.limits.upper.value
        else:
            lo, hi = -_UNBOUNDED_SWEEP, _UNBOUNDED_SWEEP
        per_joint.append([(joint.id, float(v)) for v in np.linspace(lo, hi, samples)])

    poses: list[dict[str, float]] = [{}]
    for options in per_joint:
        if len(poses) * len(options) > _MAX_POSES:
            break
        poses = [{**pose, jid: value} for pose in poses for jid, value in options]
    return poses


def _world_com(ir: RobotIR, mass_props: dict[str, MassProperties], frames) -> tuple[np.ndarray, float]:
    total = 0.0
    weighted = np.zeros(3)
    for link in ir.links:
        transform = link_geometry_transform(ir, link.id, frames)
        mp = mass_props[link.id]
        com = transform[:3, :3] @ np.array(mp.com.as_tuple()) + transform[:3, 3]
        weighted += mp.mass * com
        total += mp.mass
    return weighted / total, total


def _geared_joints(arm_joints) -> tuple[list[tuple], list[str]]:
    """Split the actuated arm joints into those whose play is known and those
    whose play is merely unrecorded.

    `MotorSpec.backlash is None` carries two different facts and conflating them
    is the failure this exists to prevent. A direct-drive motor has no gear teeth
    and so genuinely no play. A geared one has play that nobody has transcribed —
    reporting that as zero turns a missing datasheet figure into a passing
    criterion, which is exactly what the provenance ladder exists to stop.
    """
    geared: list[tuple] = []
    unknown: list[str] = []
    for joint in arm_joints:
        if joint.actuator is None:
            continue
        motor: MotorSpec = resolve_catalogue(joint.actuator.catalogue, joint.actuator.value)
        if motor.backlash is not None:
            geared.append(
                (joint, _to_radians(motor.backlash),
                 f"{joint.id} ({motor.backlash.value:g}{motor.backlash.unit})")
            )
        elif motor.gear_ratio > 1.0 + 1e-9:
            unknown.append(f"{joint.id} ({joint.actuator.value}, {motor.gear_ratio:g}:1)")
    return geared, unknown


def _slop_at(ir: RobotIR, geared: list[tuple], frames, tip: np.ndarray) -> float:
    """End-effector slop in this pose: play times lever, summed over stages.

    The lever is the perpendicular distance from the tip to the joint's axis —
    play about an axis pointing straight at the end effector moves it not at all.
    Summed rather than combined in quadrature because the stages can align, and
    this is the worst case.
    """
    total = 0.0
    for joint, play, _label in geared:
        pivot = joint_world_frame(ir, joint, frames)
        axis = pivot[:3, :3] @ np.array(joint.axis.as_tuple(), dtype=float)
        axis /= np.linalg.norm(axis)
        offset = tip - pivot[:3, 3]
        total += play * float(np.linalg.norm(offset - np.dot(offset, axis) * axis))
    return total


@register("arm_reach", tier=1)
def _arm_reach(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    home = configured_frames(ir)
    wheels = _wheel_links(ir, mass_props, home)
    arm_joints = _arm_joints(ir, wheels)
    effectors = _end_effectors(ir, wheels)

    # No arm at all: neither question applies. Emitting a failure here would make
    # a plain rover unbuildable, and `passed` already refuses to call a report
    # with no results a pass.
    if not arm_joints or not effectors:
        return []

    # A bolted-down arm has no footprint to tip out of, so there is no payload to
    # measure — but it still has gear play, and that is the criterion a bench arm
    # cares about most. The two questions are separated here rather than sharing
    # an early return, because tying them together silently dropped `backlash`
    # for every fixed-base robot.
    support = _support_box(ir, mass_props, home, wheels) if ir.base == "floating" else None
    centre = (support[0] + support[1]) / 2.0 if support is not None else None

    poses = _sweep_angles(arm_joints)
    thinned = len(poses) < _SAMPLES_PER_JOINT ** len(arm_joints)

    worst_payload = math.inf
    worst_pose: dict[str, float] = {}
    worst_reach = 0.0
    worst_frames = home
    worst_effector = effectors[0]

    # Backlash is searched on its own terms, not read off the payload pose. The
    # two worst cases are different poses: slop is driven by each joint's
    # *perpendicular* distance to its own axis, so the arm stretched straight up
    # is the furthest the tip ever gets from the base while contributing nothing
    # at all to play about a vertical shoulder-pan axis.
    geared, unknown = _geared_joints(arm_joints)
    worst_slop = -1.0
    slop_reach = 0.0
    slop_effector = effectors[0]

    for angles in poses:
        frames = configured_frames(ir, angles)
        com, mass = _world_com(ir, mass_props, frames)

        for effector in effectors:
            transform = link_geometry_transform(ir, effector, frames)
            mp = mass_props[effector]
            tip = transform[:3, :3] @ np.array(mp.com.as_tuple()) + transform[:3, 3]

            slop = _slop_at(ir, geared, frames, tip)
            if slop > worst_slop:
                worst_slop = slop
                slop_effector = effector
                slop_reach = float(np.linalg.norm(tip - frames[ir.root_link][:3, 3]))

            if support is None:
                continue
            lo, hi = support

            direction = tip[:2] - centre
            distance = float(np.linalg.norm(direction))
            if distance < 1e-9:
                continue
            direction = direction / distance

            edge = _ray_box_exit(centre, direction, lo, hi)
            beyond = distance - edge
            if beyond <= 0:
                continue  # the load hangs inside the footprint; it cannot tip the robot

            inside = edge - float(np.dot(com[:2] - centre, direction))
            payload = max(mass * inside / beyond, 0.0)

            if payload < worst_payload:
                worst_payload = payload
                worst_pose = angles
                worst_reach = distance
                worst_frames = frames
                worst_effector = effector

    results: list[CriterionResult] = []

    if support is not None:
        if not np.isfinite(worst_payload):
            # Every sampled pose kept the end effector over the footprint. Honest
            # answer: this criterion never found a tipping case to measure.
            results.append(
                CriterionResult(
                    name="payload",
                    magnitude=0.0,
                    passed=False,
                    unit="ratio",
                    detail=(
                        f"no sampled pose put {', '.join(effectors)} outside the support "
                        "footprint, so no tip-over payload could be measured — the sweep "
                        "found nothing to weigh"
                    ),
                )
            )
        else:
            results.append(
                CriterionResult(
                    name="payload",
                    magnitude=worst_payload / _TARGET_PAYLOAD,
                    passed=bool(worst_payload >= _TARGET_PAYLOAD),
                    unit="ratio",
                    detail=(
                        f"{worst_payload * 1000:.0f}g at the worst pose of {len(poses)} sampled "
                        f"({worst_reach * 1000:.0f}mm reach on {worst_effector}), "
                        f"{_TARGET_PAYLOAD * 1000:.0f}g required"
                        + ("; sweep thinned to fit the pose budget" if thinned else "")
                    ),
                )
            )

    # --- backlash, at the pose that maximises it --------------------------
    slop = max(worst_slop, 0.0)
    measured = [label for _joint, _play, label in geared]

    if unknown:
        detail = (
            f"cannot be measured: {', '.join(unknown)} are geared but carry no backlash "
            "figure in the catalogue. A missing datasheet number is not zero play"
        )
        if measured:
            detail += f"; the joints that do have one contribute {slop * 1000:.2f}mm so far"
    else:
        detail = (
            f"{slop * 1000:.2f}mm of end-effector slop at the worst of {len(poses)} poses "
            f"({slop_reach * 1000:.0f}mm reach on {slop_effector}), "
            f"{_MAX_BACKLASH * 1000:.1f}mm allowed"
            + (f"; from {', '.join(measured)}" if measured
               else "; every arm joint is direct drive, so there is no gear play to add")
        )

    results.append(
        CriterionResult(
            name="backlash",
            magnitude=slop / _MAX_BACKLASH,
            passed=bool(not unknown and slop <= _MAX_BACKLASH),
            unit="ratio",
            detail=detail,
        )
    )
    return results
