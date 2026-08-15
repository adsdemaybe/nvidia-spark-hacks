"""A 300 mm rover carrying a two-link arm — the first design that exercises the
whole criteria set.

    python designs/rover_arm_300mm.py
    python -m engine.evaluate designs/rover_arm_300mm.ir.json --max-tier 2

Why this design and not another rover: `arm_reach` registers `payload` and
`backlash` at tier 1, and both return *no result at all* for a robot with no
arm. Every design in `designs/` so far is a plain rover, so those two criteria
have never once fired — the most substantial checks in tier 1 have been dead
code. This robot is the one that asks them the question.

The base is `rover_4wd_300mm` imported whole rather than retyped: it already
settles flat and drives 16 m under tier 2, and a second hand-written copy of a
proven layout is a second place for it to drift.

Two catalogue-driven decisions the criteria force, which is the point of having
them:

- **Smart servos, not a geared stepper, on the arm.** `nema17_planetary_13.73`
  gives 4.45 N*m against the XM430's 4.1, and would be the obvious pick on
  torque alone. It carries 60 arcmin of backlash. Two of those at a 0.255 m
  reach is 8.7 mm of end-effector slop against a 2 mm budget — `backlash` fails
  it outright while `joint_torque_budget` waves it through.
- **Bounded arm joints, continuous wheel joints, in one robot.** The wheels
  declare no lower/upper because a drive wheel turns without end; the arm joints
  declare real bounds because an arm that can rotate through the deck is not an
  arm. Both are now sayable, and this is the first design that needs both.

Layout, metres, Z up, ground at Z=0:

      Z                       gripper
      |                     ___/
      |        shoulder  __/  elbow
      |            (o)==/==(o)===[]
      |             ||
      |      _______||________
      |     |__________________|   <- 300 x 160 deck, 50 mm clearance
      |      (O)            (O)    <- 90 mm wheels, continuous joints
      +------------------------- X
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rover_4wd_300mm import CHASSIS_L, CHASSIS_T, GROUND_CLEARANCE  # noqa: E402
from rover_4wd_300mm import rover as _base_rover  # noqa: E402

from engine.catalogue import resolve as resolve_catalogue  # noqa: E402
from engine.ir import (  # noqa: E402
    CatalogueParam,
    GeometrySpec,
    Joint,
    JointLimits,
    Link,
    Pose,
    Provenance,
    Quantity,
    RobotIR,
    Vec3,
)

# --- arm dimensions -----------------------------------------------------
DECK_TOP = GROUND_CLEARANCE + CHASSIS_T          # 0.056

MAST_OD, MAST_ID, MAST_H = 0.030, 0.020, 0.100
MAST_X = -0.020                                   # just behind the deck centre
MAST_EMBED = 0.004                                # into the deck, for mount_fits

SHOULDER_Z = 0.150                                # near the mast top

UPPER_OD, UPPER_ID, UPPER_L = 0.025, 0.017, 0.115
FORE_OD, FORE_ID, FORE_L = 0.020, 0.013, 0.115
LINK_OVERLAP = 0.005                              # each joint sits inside its parent tube

GRIP_L, GRIP_W, GRIP_T = 0.035, 0.030, 0.006

# Arm joint travel. Negative lifts: a right-handed rotation about +Y carries +X
# toward -Z, so a *positive* shoulder angle points the arm at the floor. Written
# the intuitive way round first, and the home pose — the midpoint of the limits,
# not zero — put the gripper 65 mm underground. Nothing complained about an arm
# below the floor directly; instead the gripper became the lowest link, so
# `_ground_contact_joints` declared the shoulder and elbow to be the wheels, and
# `static_margin`, `payload` and `drives` all went on to answer questions about
# an arm they thought was a drivetrain.
#
# The ranges also keep the arm clear of the deck and the ground at every swept
# pose. Nothing in the harness checks self-collision, so a range that let it
# fold through its own chassis would pass every criterion and still be wrong.
SHOULDER_RANGE = (-1.75, 0.25)   # rad — ~100 deg up, ~14 deg below horizontal
ELBOW_RANGE = (-2.40, 0.20)      # rad — folds up and back, never down into the deck

_ALUMINIUM = CatalogueParam(kind="catalogue", value="aluminum_6061", catalogue="materials")
_ARM_SERVO_KEY = "dynamixel_xm430_w350"
_ARM_SERVO = CatalogueParam(kind="catalogue", value=_ARM_SERVO_KEY, catalogue="servos")


def _q(value: float, unit: str, note: str) -> Quantity:
    return Quantity(
        value=value, unit=unit,
        provenance=Provenance(status="ASSUMED", source="", note=note),
    )


def _from_catalogue(quantity: Quantity, note: str) -> Quantity:
    """Re-stamp a catalogue figure as INFERRED for this joint.

    The number is the manufacturer's, but "this joint is rated at the actuator's
    stall torque" is our inference about the assembly, not something the
    datasheet says. Copying the CONFIRMED provenance across would claim the
    datasheet had been read about *this joint*, which it has not.
    """
    return Quantity(
        value=quantity.value, unit=quantity.unit,
        provenance=Provenance(
            status="INFERRED", source=f"catalogue:servos/{_ARM_SERVO_KEY}", note=note
        ),
    )


def _tube(link_id: str, od: float, idia: float, length: float, note: str, rotation: Vec3) -> Link:
    return Link(
        id=link_id,
        geometry=GeometrySpec(
            generator="tube",
            params={
                "outer_diameter": _q(od, "m", note),
                "inner_diameter": _q(idia, "m", f"{note} — wall"),
                "length": _q(length, "m", f"{note} — length"),
            },
            material=_ALUMINIUM,
        ),
        pose=Pose(rotation=rotation),
    )


def rover_with_arm() -> RobotIR:
    ir = _base_rover()
    links = list(ir.links)
    joints = list(ir.joints)

    servo = resolve_catalogue("servos", _ARM_SERVO_KEY)
    # Local Z is a tube's axis. Ry(+90 deg) puts it on world +X, so an arm
    # segment lies along the direction it reaches. Kept in the *link pose* so
    # each joint frame stays axis-aligned and every arm joint axis is a plain
    # world Y — the same reason the base does it for its wheels.
    along_x = Vec3(x=0.0, y=math.pi / 2, z=0.0)

    # --- mast: fixed to the deck, carries the shoulder ---
    links.append(_tube("mast", MAST_OD, MAST_ID, MAST_H, "arm mast", Vec3(x=0.0, y=0.0, z=0.0)))
    joints.append(
        Joint(
            id="deck_to_mast", kind="fixed", parent="chassis", child="mast",
            origin=Pose(position=Vec3(x=MAST_X, y=0.0, z=DECK_TOP - MAST_EMBED)),
        )
    )

    # --- shoulder ---
    links.append(_tube("upper_arm", UPPER_OD, UPPER_ID, UPPER_L, "upper arm", along_x))
    joints.append(
        Joint(
            id="shoulder", kind="revolute", parent="mast", child="upper_arm",
            origin=Pose(position=Vec3(x=0.0, y=0.0, z=SHOULDER_Z - (DECK_TOP - MAST_EMBED))),
            axis=Vec3(x=0.0, y=1.0, z=0.0),
            limits=JointLimits(
                lower=_q(SHOULDER_RANGE[0], "rad", "lifts to ~100 deg above horizontal"),
                upper=_q(SHOULDER_RANGE[1], "rad", "stops ~14 deg below horizontal, clear of the ground"),
                effort=_from_catalogue(servo.stall_torque, "servo stall torque at 12 V"),
                velocity=_from_catalogue(servo.no_load_speed, "servo no-load speed"),
            ),
            actuator=_ARM_SERVO,
        )
    )

    # --- elbow ---
    links.append(_tube("forearm", FORE_OD, FORE_ID, FORE_L, "forearm", along_x))
    joints.append(
        Joint(
            id="elbow", kind="revolute", parent="upper_arm", child="forearm",
            origin=Pose(position=Vec3(x=UPPER_L - LINK_OVERLAP, y=0.0, z=0.0)),
            axis=Vec3(x=0.0, y=1.0, z=0.0),
            limits=JointLimits(
                lower=_q(ELBOW_RANGE[0], "rad", "folds the forearm back over the upper arm"),
                upper=_q(ELBOW_RANGE[1], "rad", "just past straight, never folding down into the deck"),
                effort=_from_catalogue(servo.stall_torque, "servo stall torque at 12 V"),
                velocity=_from_catalogue(servo.no_load_speed, "servo no-load speed"),
            ),
            actuator=_ARM_SERVO,
        )
    )

    # --- gripper plate: the leaf link, and therefore the end effector ---
    links.append(
        Link(
            id="gripper_plate",
            geometry=GeometrySpec(
                generator="plate",
                params={
                    "length": _q(GRIP_L, "m", "tool mounting plate"),
                    "width": _q(GRIP_W, "m", "tool mounting plate"),
                    "thickness": _q(GRIP_T, "m", "6 mm, tapped for a gripper"),
                },
                material=_ALUMINIUM,
            ),
            # Plate builds from Align.MIN in Z, so drop it half its thickness to
            # sit centred on the forearm axis rather than perched on top of it.
            pose=Pose(position=Vec3(x=0.0, y=0.0, z=-GRIP_T / 2)),
        )
    )
    joints.append(
        Joint(
            id="wrist_to_gripper", kind="fixed", parent="forearm", child="gripper_plate",
            origin=Pose(position=Vec3(x=FORE_L - LINK_OVERLAP, y=0.0, z=0.0)),
        )
    )

    return RobotIR(
        name="rover_arm_300mm", root_link=ir.root_link, links=links, joints=joints
    )


def main() -> int:
    ir = rover_with_arm()
    out = Path(__file__).with_suffix(".ir.json")
    out.write_text(ir.model_dump_json(indent=2), encoding="utf-8")
    reach = MAST_X + (UPPER_L - LINK_OVERLAP) + (FORE_L - LINK_OVERLAP) + GRIP_L
    print(
        f"{ir.name}: {len(ir.links)} links, {len(ir.joints)} joints, "
        f"max reach x={reach * 1000:.0f}mm -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
