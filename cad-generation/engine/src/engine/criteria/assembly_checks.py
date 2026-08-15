"""Tier-0 assembly criteria: is this thing actually a machine?

Every criterion here was written from a specific failure. The first design the
platform produced unattended — `four_wheel_rover_30cm`, revision 0 — passed
`static_margin` and four `joint_torque_budget` checks, exported clean STEP, and
was physically impossible in two independent ways:

  1. its rear wheels sat 90 mm behind the chassis, touching nothing;
  2. every wheel's geometric axis was perpendicular to the axis its joint spun
     about, and every joint pivoted at the chassis origin rather than at the
     wheel, so a "rotation" swung the wheel in a 140 mm orbit through the floor.

Neither is exotic. Both are invisible to every criterion that existed, because
`mount_fits` only inspects **fixed** joints and nothing else looked at a joint's
geometry at all. §8: a criterion transfers across topologies even when the CAD
doesn't — "is the child attached" and "does a wheel roll in place" are not rover
facts, they are machine facts.

All three are analytic and stay inside tier 0's <1 ms budget. `link_attached`
buys that speed with bounding boxes, at a known cost: axis-aligned boxes overlap
in cases where the solids they bound do not, so it catches gross disconnection,
not a 0.2 mm interference. Tier 2 (MuJoCo) is what settles the fine cases; a
tier-0 criterion that tried to be exact would just be a slow, wrong tier 2.

The other two avoid boxes entirely, deliberately. The first version of
`wheel_rolls_in_place` swept the joint and compared bounding boxes, and failed a
perfectly good wheel by 18.6 mm — because the box drawn round a cylinder's
*corners* grows by root-2 when the cylinder turns about its own axis. It was
measuring the box, not the wheel. Both now use closed-form quantities (distance
from a point to a line, angle between two axes) that no rotation can fake.
"""

from __future__ import annotations

import numpy as np

from engine.criteria.base import CriterionResult
from engine.criteria.registry import register
from engine.ir import RobotIR
from engine.kinematics import (
    configured_frames,
    joint_world_frame,
    link_geometry_transform,
    subtree_links,
)
from engine.mass_properties import MassProperties

# Engineering policy thresholds (§8) — requirements we chose, not physical
# constants, so no provenance applies.
#
# A joint may separate its parent and child by up to this fraction of the
# smaller part's size before it counts as detached. Non-zero because a bolted
# joint legitimately has a washer's worth of air in it, and because bounding
# boxes are a coarse stand-in for the solids.
_ATTACH_GAP_MAX = 0.02  # 2% of the smaller link's largest dimension

# How far a ground-contacting link may wander as its joint sweeps, as a fraction
# of its own radius, before it is not rolling. A real wheel scores 0.0 exactly:
# rotating it about its own axis moves no bounding box at all.
_ROLL_DEVIATION_MAX = 0.05

# Sine of the largest angle allowed between a wheel's axis of symmetry and the
# axis it is driven about. ~1.7 degrees: enough to absorb a rounding error in a
# hand-written Euler angle, far short of a wheel mounted the wrong way round.
_AXIS_SINE_MAX = 0.03

# How near two bounding-box extents must be to count as a round cross-section.
_ROUND_TOLERANCE = 0.01

_GROUND_EPSILON = 1e-3  # 1 mm — same ground band static_margin uses

# Engineering policy, both of them, and both stated here rather than inferred.
# `_DRIVE_TRAVEL_MIN` is deliberately the same 40 mm `tier2_sim._DRIVE_MIN_TRAVEL`
# uses: a tier-0 criterion that predicted a *different* number than the tier it
# is standing in for would be a second opinion, not an early warning.
_DRIVE_TRAVEL_MIN = 0.04  # m of wheel travel available before the limit
_ARTICULATION_MIN = 0.10  # rad (~5.7 deg) — below this a revolute joint is fixed



def _world_bbox(transform: np.ndarray, mp: MassProperties) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = mp.bbox_min.as_tuple(), mp.bbox_max.as_tuple()
    corners = np.array(
        [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )
    world = corners @ transform[:3, :3].T + transform[:3, 3]
    return world.min(axis=0), world.max(axis=0)


def _subtree_bbox(
    ir: RobotIR,
    link_ids: list[str],
    frames: dict[str, np.ndarray],
    mass_props: dict[str, MassProperties],
) -> tuple[np.ndarray, np.ndarray]:
    los, his = [], []
    for link_id in link_ids:
        lo, hi = _world_bbox(link_geometry_transform(ir, link_id, frames), mass_props[link_id])
        los.append(lo)
        his.append(hi)
    return np.min(los, axis=0), np.max(his, axis=0)


def rigid_groups(ir: RobotIR) -> dict[str, list[str]]:
    """Map each link to every link welded to it through fixed joints.

    One physical body is often several IR links: a real published design has an
    arm segment that is a printed bracket *plus* a servo *plus* a motor holder,
    all bolted solid. Importing SO-101 makes one IR link per part, because a
    `GeometrySpec` builds one solid — so the parts of a body arrive as a frame
    carrier with siblings fixed to it at identity.

    Any criterion asking a question about a *body* has to ask it of the whole
    group. `link_attached` measured frame-carrier against frame-carrier and
    reported four SO-101 joints as detached by up to 73 mm, because the parts
    that actually bridge those joints were siblings it never looked at. The arm
    was fine; the criterion was looking at a third of it.
    """
    parent_of: dict[str, str] = {}

    def find(link_id: str) -> str:
        root = link_id
        while parent_of.get(root, root) != root:
            root = parent_of[root]
        while parent_of.get(link_id, link_id) != root:  # path compression
            parent_of[link_id], link_id = root, parent_of[link_id]
        return root

    for link in ir.links:
        parent_of.setdefault(link.id, link.id)
    for joint in ir.joints:
        if joint.kind == "fixed":
            parent_of[find(joint.parent)] = find(joint.child)

    groups: dict[str, list[str]] = {}
    for link in ir.links:
        groups.setdefault(find(link.id), []).append(link.id)
    return {link.id: groups[find(link.id)] for link in ir.links}


@register("link_attached", tier=0)
def _link_attached(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    """Every joint's child must reach its parent.

    `mount_fits` asks the same question of fixed joints only, and asks it as
    volumetric overlap — right for a bolted mount, wrong for a revolute one,
    where a wheel properly *touches* its bracket rather than interpenetrating it.
    So this measures the gap instead of the overlap, and applies to every joint
    kind. A revolute joint is not an excuse to float.
    """
    results: list[CriterionResult] = []
    frames = configured_frames(ir)
    groups = rigid_groups(ir)

    for joint in ir.joints:
        if joint.kind == "fixed":
            # A fixed joint's two links are the same rigid body by definition, so
            # asking whether they touch is asking whether a body touches itself.
            # `mount_fits` is the criterion that judges fixed joints, and it does
            # it properly, as volumetric overlap.
            continue

        parent_group, child_group = groups[joint.parent], groups[joint.child]
        p_lo, p_hi = _subtree_bbox(ir, parent_group, frames, mass_props)
        c_lo, c_hi = _subtree_bbox(ir, child_group, frames, mass_props)

        # Per-axis separation: positive only where the boxes genuinely miss.
        gaps = np.maximum(np.maximum(p_lo - c_hi, c_lo - p_hi), 0.0)
        gap = float(np.linalg.norm(gaps))

        scale = float(max(np.max(c_hi - c_lo), np.max(p_hi - p_lo), 1e-6))
        magnitude = -gap / scale

        results.append(
            CriterionResult(
                name=f"link_attached[{joint.id}]",
                magnitude=magnitude,
                passed=bool(magnitude >= -_ATTACH_GAP_MAX),
                unit="gap_ratio",
                detail=(
                    f"{joint.parent}->{joint.child} ({joint.kind}) gap={gap * 1000:.1f}mm "
                    f"over {scale * 1000:.1f}mm part"
                    + (" — the child touches nothing" if gap > 0 else " — in contact")
                ),
            )
        )
    return results


def _revolution_axis(mp: MassProperties) -> np.ndarray | None:
    """The local axis a link is round about, or None if it isn't round.

    Inferred from the bounding box: a solid of revolution has two equal
    cross-axis extents and a third that differs, so the odd extent names the
    axis. A cube or a sphere is ambiguous (every axis qualifies) and a
    three-different-extents part is not round at all — both return None, and the
    caller reports the criterion as not applicable rather than as a pass.
    """
    extents = np.array(mp.bbox_max.as_tuple()) - np.array(mp.bbox_min.as_tuple())
    if float(np.min(extents)) <= 0:
        return None
    for axis in range(3):
        a, b = extents[(axis + 1) % 3], extents[(axis + 2) % 3]
        if abs(a - b) <= _ROUND_TOLERANCE * max(a, b) and abs(extents[axis] - a) > _ROUND_TOLERANCE * max(
            extents[axis], a
        ):
            unit = np.zeros(3)
            unit[axis] = 1.0
            return unit
    return None


def _ground_contact_joints(
    ir: RobotIR, mass_props: dict[str, MassProperties], home: dict[str, np.ndarray]
):
    """Revolute joints whose subtree reaches the ground — i.e. the wheels.

    Scope is deliberate. An arm link swinging through a wide arc is doing its
    job, so a criterion demanding that revolute children stay put would fail
    every manipulator ever designed. What carries the robot is what has to roll.
    """
    ground_z = min(
        _world_bbox(link_geometry_transform(ir, link.id, home), mass_props[link.id])[0][2]
        for link in ir.links
    )
    for joint in (j for j in ir.joints if j.kind == "revolute"):
        subtree = subtree_links(ir, joint.child)
        lo, hi = _subtree_bbox(ir, subtree, home, mass_props)
        if lo[2] <= ground_z + _GROUND_EPSILON:
            yield joint, float(max(np.max(hi - lo) / 2.0, 1e-6))


@register("wheel_rolls_in_place", tier=0)
def _wheel_rolls_in_place(
    ir: RobotIR, mass_props: dict[str, MassProperties]
) -> list[CriterionResult]:
    """A driven wheel must turn about itself, not orbit something else.

    Measured as the perpendicular distance from the wheel's centre of mass to
    the joint's axis line. That distance *is* the orbit radius: spin the joint
    and the wheel's mass sweeps a circle of exactly that size, so zero means the
    wheel turns in place and 0.1 m means it swings 0.1 m out and back.

    Exact and closed-form, so no sweep sampling — which also avoids the trap the
    first version of this criterion fell into. Sampling the sweep and comparing
    bounding boxes measures the box, not the solid, and the axis-aligned box
    around a cylinder's *corners* grows by root-2 when the cylinder turns about
    its own axis. That reported a perfectly good wheel as swinging 18.6 mm.
    """
    results: list[CriterionResult] = []
    home = configured_frames(ir)

    for joint, radius in _ground_contact_joints(ir, mass_props, home):
        pivot = joint_world_frame(ir, joint, home)
        axis = pivot[:3, :3] @ np.array(joint.axis.as_tuple(), dtype=float)
        norm = float(np.linalg.norm(axis))
        if norm < 1e-12:
            continue
        axis /= norm

        transform = link_geometry_transform(ir, joint.child, home)
        com = transform[:3, :3] @ np.array(mass_props[joint.child].com.as_tuple()) + transform[:3, 3]

        offset = com - pivot[:3, 3]
        orbit = float(np.linalg.norm(offset - np.dot(offset, axis) * axis))
        magnitude = -orbit / radius

        results.append(
            CriterionResult(
                name=f"wheel_rolls_in_place[{joint.id}]",
                magnitude=magnitude,
                passed=bool(magnitude >= -_ROLL_DEVIATION_MAX),
                unit="radius_ratio",
                detail=(
                    f"{joint.child} centre of mass sits {orbit * 1000:.1f}mm off the "
                    f"{joint.id} axis (wheel radius {radius * 1000:.1f}mm)"
                    + (" — turns in place" if orbit <= _ROLL_DEVIATION_MAX * radius
                       else " — orbits; the pivot is not on the wheel")
                ),
            )
        )
    return results


@register("wheel_axis_aligned", tier=0)
def _wheel_axis_aligned(
    ir: RobotIR, mass_props: dict[str, MassProperties]
) -> list[CriterionResult]:
    """A wheel's axis of symmetry must be the axis it spins about.

    The two can disagree while every other check passes, and revision 0's wheels
    did exactly that: round about world X, driven about world Y. Such a wheel
    rotates about an axis lying in its own disc plane — it flips like a tossed
    coin, and no static view or mass property makes that visible.

    Magnitude is the sine of the angle between the two axes: 0 when they agree,
    1 at the full 90-degree disagreement.
    """
    results: list[CriterionResult] = []
    home = configured_frames(ir)

    for joint, _radius in _ground_contact_joints(ir, mass_props, home):
        local_axis = _revolution_axis(mass_props[joint.child])
        if local_axis is None:
            continue  # not a solid of revolution — nothing to align

        transform = link_geometry_transform(ir, joint.child, home)
        geometry_axis = transform[:3, :3] @ local_axis
        pivot = joint_world_frame(ir, joint, home)
        joint_axis = pivot[:3, :3] @ np.array(joint.axis.as_tuple(), dtype=float)

        norms = np.linalg.norm(geometry_axis) * np.linalg.norm(joint_axis)
        if norms < 1e-12:
            continue
        sine = float(np.linalg.norm(np.cross(geometry_axis, joint_axis)) / norms)

        results.append(
            CriterionResult(
                name=f"wheel_axis_aligned[{joint.id}]",
                magnitude=-sine,
                passed=bool(sine <= _AXIS_SINE_MAX),
                unit="sine",
                detail=(
                    f"{joint.child} is round about {geometry_axis.round(3).tolist()}, "
                    f"{joint.id} spins about {(joint_axis / np.linalg.norm(joint_axis)).round(3).tolist()}"
                    + (" — aligned" if sine <= _AXIS_SINE_MAX
                       else f" — {np.degrees(np.arcsin(min(sine, 1.0))):.0f} degrees apart; it would tumble, not roll")
                ),
            )
        )
    return results


@register("joint_can_move", tier=0)
def _joint_can_move(
    ir: RobotIR, mass_props: dict[str, MassProperties]
) -> list[CriterionResult]:
    """A joint that declares a range too small to use is not a joint.

    This is the failure `ir.JointLimits` already documents, moved from tier 2 to
    tier 0. The rover's drive joints carried +/-pi with the provenance note
    "continuous rotation": every static criterion passed, MuJoCo was compiled,
    two seconds of torque were simulated, and the rover drove 34 mm before
    welding solid against the limit. Seconds per candidate to learn something a
    subtraction knows.

    The two cases are different questions, so they get different measurements:

    - A **wheel** — a revolute joint whose subtree reaches the ground — must turn
      without end, so *any* bound on one is the defect. The verdict is
      categorical rather than a threshold on the range, because the arithmetic
      that looks like it should work does not: the first version of this
      criterion computed `(upper - lower) * radius` and passed the historical
      rover at 283 mm of predicted travel while tier 2 measured 34 mm. Two
      reasons, and both survive fixing the obvious one. Home is the *midpoint* of
      the limits, so only half the span is available in the driving direction;
      and the rolled arc is an upper bound on how far the body actually goes,
      because the sim spends part of the run accelerating and slipping. A tier-0
      criterion that guesses at a tier-2 number is a second opinion, and a wrong
      one. The magnitude still reports the arc the bound leaves, labelled as the
      optimistic bound it is.
    - An **arm** joint is legitimately bounded, so the only question is whether
      the bound leaves room to articulate. `_ARTICULATION_MIN` is a floor, not an
      opinion about the design: below it the joint is a fixed joint that costs an
      actuator.

    Unbounded joints pass by construction — that is what unbounded means — and
    fixed joints are not asked, since the IR forbids them limits at all.
    """
    results: list[CriterionResult] = []
    home = configured_frames(ir)
    wheel_radius = {j.id: r for j, r in _ground_contact_joints(ir, mass_props, home)}

    for joint in ir.joints:
        if joint.kind == "fixed":
            continue
        limits = joint.limits
        if limits is None or not limits.bounded:
            continue  # continuous: no limit to hit

        span = float(limits.upper.value - limits.lower.value)
        radius = wheel_radius.get(joint.id)

        if radius is not None:
            # Half the span: home is the midpoint of the limits, so that is what
            # is left in the driving direction. Still an upper bound on distance
            # travelled, not a prediction of it.
            arc = (span / 2.0) * radius
            results.append(
                CriterionResult(
                    name=f"joint_can_move[{joint.id}]",
                    magnitude=arc / _DRIVE_TRAVEL_MIN,
                    passed=False,
                    unit="arc_ratio",
                    detail=(
                        f"{joint.id} carries a ground-contact wheel and declares limits "
                        f"({span:.3f}rad). A driven wheel must turn without end: leave "
                        f"lower/upper unset, which is how the IR says 'continuous'. From "
                        f"home at the midpoint it has {arc * 1000:.0f}mm of arc before the "
                        f"limit welds it — an optimistic bound, since the measured rover "
                        f"managed 34mm where this arithmetic said 141mm"
                    ),
                )
            )
            continue

        results.append(
            CriterionResult(
                name=f"joint_can_move[{joint.id}]",
                magnitude=span / _ARTICULATION_MIN - 1.0,
                passed=bool(span >= _ARTICULATION_MIN),
                unit="range_ratio",
                detail=(
                    f"{joint.id} ({joint.kind}) has a range of {span:.4f}"
                    f"{limits.upper.unit} "
                    + (f"({np.degrees(span):.1f} degrees) " if limits.upper.unit == "rad" else "")
                    + f"against a {_ARTICULATION_MIN:.2f} minimum"
                    + ("" if span >= _ARTICULATION_MIN
                       else " — it cannot articulate, so it is a fixed joint carrying the cost "
                            "of an actuator")
                ),
            )
        )
    return results
