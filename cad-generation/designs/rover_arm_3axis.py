r"""The 4WD rover with a 3-axis arm on the deck.

    python designs/rover_arm_3axis.py           # writes rover_arm_3axis.ir.json
    python -m engine.evaluate designs/rover_arm_3axis.ir.json --max-tier 2

This design exists so `payload` and `backlash` have something to bite on. Both
criteria were the two that actually drove the prototype's design loop, and
neither can say anything about a rover with no arm — `arm_reach` returns no
result at all for `rover_4wd_300mm`, which is correct and also means those code
paths were never exercised by a real design until this one.

It reuses `rover_4wd_300mm.rover()` rather than restating the chassis: the base
is already verified by tier 0, 1 and 2, and a second hand-typed copy of it would
drift.

Layout, metres, Z up, ground at Z=0, arm extended along +X at the home pose:

      Z                          .-- gripper
      |                         /
      |        shoulder   elbow'
      |            \_______\_________.
      |    turntable |
      |     ________[=]________
      |    |___________________|   <- deck, 56 mm up
      |    (O)              (O)
      +--------------------------- X
           |<--- 220 mm --->|          wheelbase
           |<------- 330 mm reach ---->|

The arm is aluminium and direct-driven throughout. That is a deliberate choice
rather than a default: a 13.73:1 planetary would give the shoulder ten times the
torque it needs, and pay for it with 60 arcmin of lost motion at a 305 mm lever
— 5 mm of slop against a 2 mm budget. `test_tier1_reach` asserts exactly that
trade, so the reason this design is direct-drive stays checkable instead of
becoming folklore.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

# `designs/` is a directory of scripts, not a package, so the sibling design has
# to be made importable before it can be reused. Reused rather than restated
# because the chassis below it is already verified at tiers 0, 1 and 2, and a
# second hand-typed copy would drift from the one under test.
sys.path.insert(0, str(Path(__file__).parent))

from engine.ir import (
    CatalogueParam,
    GeometrySpec,
    Joint,
    JointLimits,
    Link,
    Pose,
    RobotIR,
    Vec3,
)

from rover_4wd_300mm import (
    CHASSIS_T,
    GROUND_CLEARANCE,
    _ALUMINIUM,
    _MOTOR,
    _q,
    rover,
)

# --- arm dimensions -----------------------------------------------------
DECK_TOP = GROUND_CLEARANCE + CHASSIS_T  # 0.056 — where the turntable bolts on

TURNTABLE_OD, TURNTABLE_ID, TURNTABLE_H = 0.060, 0.040, 0.030

SHOULDER_L, SHOULDER_W, SHOULDER_T = 0.150, 0.035, 0.008
ELBOW_L, ELBOW_W, ELBOW_T = 0.130, 0.030, 0.006
GRIPPER_L, GRIPPER_W, GRIPPER_T = 0.050, 0.025, 0.005


def _arm_link(link_id: str, length: float, width: float, thickness: float, note: str) -> Link:
    """A plate arm segment, centred on its own joint axis.

    The plate generator builds up from Z=0, so without the -T/2 offset the link
    hangs entirely above the axis it pivots about and the arm bends out of its
    own plane — geometry that still passes every static check.
    """
    return Link(
        id=link_id,
        geometry=GeometrySpec(
            generator="plate",
            params={
                "length": _q(length, "m", note),
                "width": _q(width, "m", "web width"),
                "thickness": _q(thickness, "m", "6061 plate"),
            },
            material=_ALUMINIUM,
        ),
        pose=Pose(position=Vec3(x=0.0, y=0.0, z=-thickness / 2)),
    )


def _revolute(joint_id: str, parent: str, child: str, origin: Pose, axis: Vec3, span: float) -> Joint:
    """An arm joint with symmetric limits, so the home pose is the extended one.

    Symmetric on purpose: the home configuration is the midpoint of the limits,
    and an arm whose limits are not centred starts every tier-0 criterion in a
    half-folded pose that nobody chose.
    """
    return Joint(
        id=joint_id,
        kind="revolute",
        parent=parent,
        child=child,
        origin=origin,
        axis=axis,
        limits=JointLimits(
            lower=_q(-span, "rad", "symmetric travel"),
            upper=_q(span, "rad", "symmetric travel"),
            effort=_q(0.4, "N*m", "NEMA 17 holding torque"),
            velocity=_q(6.0, "rad/s", "unloaded slew"),
        ),
        actuator=_MOTOR,
    )


def rover_with_arm() -> RobotIR:
    base = rover()
    links = list(base.links)
    joints = list(base.joints)

    links.append(
        Link(
            id="turntable",
            geometry=GeometrySpec(
                generator="tube",
                params={
                    "outer_diameter": _q(TURNTABLE_OD, "m", "slew ring outer race"),
                    "inner_diameter": _q(TURNTABLE_ID, "m", "cable pass-through"),
                    "length": _q(TURNTABLE_H, "m", "riser height"),
                },
                material=_ALUMINIUM,
            ),
        )
    )
    links.append(_arm_link("link_shoulder", SHOULDER_L, SHOULDER_W, SHOULDER_T, "upper arm"))
    links.append(_arm_link("link_elbow", ELBOW_L, ELBOW_W, ELBOW_T, "forearm"))
    links.append(_arm_link("gripper", GRIPPER_L, GRIPPER_W, GRIPPER_T, "jaw carrier"))

    _Y = Vec3(x=0.0, y=1.0, z=0.0)

    joints.append(
        _revolute(
            "yaw", "chassis", "turntable",
            # The chassis link frame is the robot root; its plate is placed by the
            # link's own pose, so the deck top is a fixed height in this frame.
            origin=Pose(position=Vec3(x=0.0, y=0.0, z=DECK_TOP)),
            axis=Vec3(x=0.0, y=0.0, z=1.0),
            span=math.pi,
        )
    )
    joints.append(
        _revolute(
            "shoulder", "turntable", "link_shoulder",
            origin=Pose(position=Vec3(x=0.0, y=0.0, z=TURNTABLE_H)),
            axis=_Y, span=math.pi / 2,
        )
    )
    joints.append(
        _revolute(
            "elbow", "link_shoulder", "link_elbow",
            origin=Pose(position=Vec3(x=SHOULDER_L, y=0.0, z=0.0)),
            axis=_Y, span=2.0,
        )
    )
    joints.append(
        _revolute(
            "wrist", "link_elbow", "gripper",
            origin=Pose(position=Vec3(x=ELBOW_L, y=0.0, z=0.0)),
            axis=_Y, span=1.5,
        )
    )

    return RobotIR(
        name="rover_arm_3axis", root_link=base.root_link, links=links, joints=joints
    )


def main() -> int:
    ir = rover_with_arm()
    out = Path(__file__).with_suffix(".ir.json")
    out.write_text(ir.model_dump_json(indent=2), encoding="utf-8")
    print(f"{ir.name}: {len(ir.links)} links, {len(ir.joints)} joints -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
