"""A four-wheel-drive rover, 300 mm chassis, authored by hand.

    python designs/rover_4wd_300mm.py            # writes rover_4wd_300mm.ir.json
    python -m engine.evaluate designs/rover_4wd_300mm.ir.json --max-tier 1

This is a *design*, not engine code — an IR document that happens to be written
by a generator so the numbers stay derived rather than typed twice. It exists
because revision 0 of the agent loop produced a rover with detached wheels that
could not roll, and a criterion nobody can pass is as useless as one nobody can
fail: the new `link_attached` and `wheel_rolls_in_place` checks need a design
that satisfies them to prove they are strict rather than impossible.

Layout, all dimensions in metres, Z up, ground at Z=0:

      Z
      |          .-- chassis plate, 300 x 160 x 6, 50 mm ground clearance
      |     ___________________
      |    |___________________|
      |     |]              [|      <- L-brackets, motor mounts, hang from
      |    (O)              (O)        the chassis underside
      +------------------------- X    <- wheels, 90 mm dia, one at each corner

Every wheel is a tube whose axis of symmetry is world Y, spinning about a joint
whose axis is also world Y, pivoting on a point that lies on that axis. Those
three agreeing is what "the wheel rolls" means, and revision 0 got all three
wrong while passing every check that existed.

The brackets embed 2 mm into the chassis so `mount_fits` sees genuine volumetric
overlap at a bolted joint rather than two faces touching at ratio 0.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from engine.ir import (
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

# --- dimensions ---------------------------------------------------------
CHASSIS_L, CHASSIS_W, CHASSIS_T = 0.300, 0.160, 0.006
GROUND_CLEARANCE = 0.050          # chassis underside above the ground plane

WHEEL_OD, WHEEL_ID, WHEEL_W = 0.090, 0.070, 0.030
WHEEL_R = WHEEL_OD / 2            # 0.045 — also the axle height, so the wheel
                                  # rests exactly on Z=0 with no interference
WHEELBASE, TRACK = 0.220, 0.171   # axle-to-axle in X, and bracket centres in Y

BRACKET_A, BRACKET_B = 0.040, 0.030   # along the chassis, then down to the axle
BRACKET_T, BRACKET_W = 0.004, 0.030

EMBED = 0.002                     # bracket into chassis, for mount_fits overlap

_ALUMINIUM = CatalogueParam(kind="catalogue", value="aluminum_6061", catalogue="materials")
_PETG = CatalogueParam(kind="catalogue", value="petg", catalogue="materials")

# A direct NEMA 17 rather than the 13.73:1 planetary: 0.4 N*m at a 45 mm radius
# is 8.9 N of tractive force per wheel against a 12 N vehicle, so the budget is
# comfortable but still *responsive* — a torque margin of 0.97 tells coverage
# nothing, because nothing the design does moves it.
_MOTOR = CatalogueParam(kind="catalogue", value="nema17_17hs4401", catalogue="stepper_motors")


def _q(value: float, unit: str, note: str) -> Quantity:
    return Quantity(
        value=value,
        unit=unit,
        provenance=Provenance(status="ASSUMED", source="", note=note),
    )


def _corner(x_sign: int, y_sign: int) -> str:
    return f"{'front' if x_sign > 0 else 'rear'}_{'left' if y_sign > 0 else 'right'}"


def rover() -> RobotIR:
    chassis = Link(
        id="chassis",
        geometry=GeometrySpec(
            generator="plate",
            params={
                "length": _q(CHASSIS_L, "m", "300 mm deck"),
                "width": _q(CHASSIS_W, "m", "clears the wheels inboard"),
                "thickness": _q(CHASSIS_T, "m", "6 mm 6061 plate"),
            },
            material=_ALUMINIUM,
        ),
        # The plate generator builds from Align.MIN in X, so a 0.30 m plate spans
        # 0..0.30 in its own frame. Shifting it back by half centres the deck on
        # the origin. Revision 0's whole detachment bug was assuming otherwise.
        pose=Pose(position=Vec3(x=-CHASSIS_L / 2, y=0.0, z=GROUND_CLEARANCE)),
    )

    links: list[Link] = [chassis]
    joints: list[Joint] = []

    for x_sign in (1, -1):
        for y_sign in (1, -1):
            corner = _corner(x_sign, y_sign)
            axle_x = x_sign * WHEELBASE / 2
            bracket_y = y_sign * TRACK / 2

            # The bracket generator puts its vertical arm at the start of the
            # horizontal one and always runs the horizontal arm along +X, so the
            # vertical arm straddles the axle at both ends of the robot and only
            # the flange direction differs: forward at the front, inboard at the
            # rear. Both stay over the deck, which is all the flange needs.
            bracket_x = axle_x - BRACKET_T / 2

            links.append(
                Link(
                    id=f"bracket_{corner}",
                    geometry=GeometrySpec(
                        generator="bracket",
                        params={
                            "arm_a_length": _q(BRACKET_A, "m", "flange along the chassis underside"),
                            "arm_b_length": _q(BRACKET_B, "m", "drops to the axle"),
                            "thickness": _q(BRACKET_T, "m", "4 mm 6061"),
                            "width": _q(BRACKET_W, "m", "motor face width"),
                        },
                        material=_ALUMINIUM,
                    ),
                    # Flipped so the L hangs *down* from the deck. This is a
                    # geometry pose, not a joint origin, so the bracket's frame
                    # stays axis-aligned and the wheel joint below can use a
                    # plain world-Y axis instead of a mirrored one.
                    pose=Pose(rotation=Vec3(x=math.pi, y=0.0, z=0.0)),
                )
            )
            joints.append(
                Joint(
                    id=f"deck_to_bracket_{corner}",
                    kind="fixed",
                    parent="chassis",
                    child=f"bracket_{corner}",
                    origin=Pose(
                        position=Vec3(
                            x=bracket_x,
                            y=bracket_y,
                            z=GROUND_CLEARANCE + EMBED,
                        )
                    ),
                )
            )

            # Wheel inboard face, just outboard of the bracket's outer face.
            hub_y = y_sign * (TRACK / 2 + BRACKET_W / 2 - EMBED / 2)

            links.append(
                Link(
                    id=f"wheel_{corner}",
                    geometry=GeometrySpec(
                        generator="tube",
                        params={
                            "outer_diameter": _q(WHEEL_OD, "m", "90 mm rolling diameter"),
                            "inner_diameter": _q(WHEEL_ID, "m", "10 mm rim wall"),
                            "length": _q(WHEEL_W, "m", "30 mm tread width"),
                        },
                        material=_PETG,
                    ),
                    # Local Z (the tube's axis) onto world +/-Y, so the axis of
                    # symmetry and the joint axis are the same line.
                    pose=Pose(rotation=Vec3(x=-y_sign * math.pi / 2, y=0.0, z=0.0)),
                )
            )
            joints.append(
                Joint(
                    id=f"drive_{corner}",
                    kind="revolute",
                    parent=f"bracket_{corner}",
                    child=f"wheel_{corner}",
                    # Relative to the bracket's frame, which sits at the fixed
                    # joint above — axis-aligned, so this is plain arithmetic.
                    origin=Pose(
                        position=Vec3(
                            x=axle_x - bracket_x,
                            y=hub_y - bracket_y,
                            z=WHEEL_R - (GROUND_CLEARANCE + EMBED),
                        )
                    ),
                    axis=Vec3(x=0.0, y=1.0, z=0.0),
                    limits=JointLimits(
                        # No lower/upper: a drive wheel turns without end. This
                        # was written as +/-pi with a note saying "continuous
                        # rotation", which is not the same claim — MuJoCo
                        # enforces the range, so the rover rolled half a turn
                        # (34 mm) and welded solid. Tier 0 and tier 1 passed it
                        # every time; tier 2 is what caught it.
                        effort=_q(0.4, "N*m", "NEMA 17 holding torque"),
                        velocity=_q(10.0, "rad/s", "~1.6 m/s at 45 mm radius"),
                    ),
                    actuator=_MOTOR,
                )
            )

    return RobotIR(name="rover_4wd_300mm", root_link="chassis", links=links, joints=joints)


def main() -> int:
    ir = rover()
    out = Path(__file__).with_suffix(".ir.json")
    out.write_text(ir.model_dump_json(indent=2), encoding="utf-8")
    print(f"{ir.name}: {len(ir.links)} links, {len(ir.joints)} joints -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
