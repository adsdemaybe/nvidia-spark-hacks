"""Stationary dual-arm humanoid torso - full assembly.

A fixed, floor-bolted robot: steel base plate, pedestal column, an electronics
torso carrying the compute, drivers, power distribution and battery, and two
7-DOF arms ending in three-finger hands with an opposable thumb.

Build:
    python <cad-skill>/scripts/gen humanoid_torso.step.py --write

Every placement below comes from ``humanoid_kinematics.arm_frames`` evaluated
at ``POSE``, so changing a joint angle re-poses the model rather than moving
geometry by hand. The left arm is a true mirror of the right about the YZ
plane, which is how the handed parts would actually be manufactured.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build123d import Compound, Location, Plane, Pos, Rot, mirror  # noqa: E402

from cadgen.assembly import AssemblyHelper  # noqa: E402

import humanoid_parts as P  # noqa: E402
import humanoid_params as PR  # noqa: E402
import humanoid_kinematics as K  # noqa: E402

# --------------------------------------------------------------------------
# Pose
# --------------------------------------------------------------------------

#: Joint angles the exported model is built at. Any key of K.POSES, or a dict.
POSE = dict(K.READY_POSE)

#: Finger flexion for the exported pose, degrees.
FINGER_POSE = {"proximal": 22.0, "distal": 30.0, "thumb_base": 32.0}


# --------------------------------------------------------------------------
# Placement helpers
# --------------------------------------------------------------------------

#: Maps a module axis onto the actuator's local +Z (stator -> output).
AXIS_ROT = {
    "+x": Rot(0, 90, 0),
    "+y": Rot(-90, 0, 0),
    "-z": Rot(180, 0, 0),
}


def build_arm(asm, pose: dict[str, float]) -> list:
    """Build the RIGHT arm in torso-local coordinates.

    Returns the list of shapes; the caller mirrors them for the left arm.
    """
    f = K.arm_frames(pose, side=1)
    sx, _, sz = K.SHOULDER_ORIGIN
    parts: list = []

    def add(shape, name, *details):
        shape = P._label(shape, name if not details else f"{name}_{'_'.join(map(str, details))}")
        parts.append(shape)
        return shape

    a100 = PR.ACTUATORS["A100"]
    a80, a60, a40 = PR.ACTUATORS["A80"], PR.ACTUATORS["A60"], PR.ACTUATORS["A40"]
    wall_mount = Pos(sx, 0, sz)

    # -- shoulder -------------------------------------------------------
    add(wall_mount * P.clavicle_bracket("A100"), "clavicle_bracket")
    add(
        wall_mount
        * Pos(PR.ARM["clavicle_len"] - a100.length, 0, 0)
        * AXIS_ROT["+x"]
        * P.actuator_module("A100", pose["shoulder_pitch"]),
        "shoulder_pitch_module",
    )

    pitch = f["shoulder_pitch"].posed
    add(pitch * P.shoulder_roll_housing(), "shoulder_roll_housing")
    add(
        pitch
        * Pos(PR.ARM["shoulder_roll_dx"], -a100.length, 0)
        * AXIS_ROT["+y"]
        * P.actuator_module("A100", pose["shoulder_roll"]),
        "shoulder_roll_module",
    )

    roll = f["shoulder_roll"].posed
    add(roll * P.shoulder_yaw_housing(), "shoulder_yaw_housing")
    add(
        roll
        * Pos(0, 0, -(PR.ARM["shoulder_yaw_drop"] - a60.length))
        * AXIS_ROT["-z"]
        * P.actuator_module("A60", pose["shoulder_yaw"]),
        "shoulder_yaw_module",
    )

    # -- upper arm and elbow --------------------------------------------
    yaw = f["shoulder_yaw"].posed
    add(
        yaw
        * P.tube_link(
            PR.ARM["upper_arm_len"],
            PR.ARM["link_od"],
            PR.ARM["link_wall"],
            "A60",
            "A80",
            "upper_arm",
            bottom_style="pitch",
            driven_clear_r=P.crank_joint_radius(PR.ARM["link_od_fore"]),
        ),
        "upper_arm",
    )
    add(
        yaw
        * Pos(0, 0, -PR.ARM["upper_arm_len"])
        * Pos(-a80.length, 0, 0)
        * AXIS_ROT["+x"]
        * P.actuator_module("A80", pose["elbow_pitch"]),
        "elbow_module",
    )

    # -- forearm and wrist ----------------------------------------------
    elbow = f["elbow_pitch"].posed
    fore_seat = PR.WRIST_YAW_DROP - a40.length  # tube length to the roll stator
    add(
        elbow
        * P.cranked_link(
            "A80", fore_seat, PR.ARM["link_od_fore"], PR.ARM["link_wall"], "A40", "forearm"
        ),
        "forearm",
    )
    add(
        elbow * Pos(0, 0, -fore_seat) * AXIS_ROT["-z"]
        * P.actuator_module("A40", pose["wrist_yaw"]),
        "forearm_roll_module",
    )

    wyaw = f["wrist_yaw"].posed
    add(
        wyaw
        * P.tube_link(
            PR.WRIST_PITCH_DROP,
            PR.ARM["link_od_fore"],
            PR.ARM["link_wall"],
            "A40",
            "A40",
            "forearm_tube",
            bottom_style="pitch",
            windows=False,
            driven_clear_r=P.crank_joint_radius(46.0),
        ),
        "forearm_tube",
    )
    # Finger drives live here: far enough below the elbow to clear the elbow
    # bracket's sweep, in a tube wide enough to actually hold them.
    for unit in P.tendon_drive_pack().children:
        add(wyaw * Pos(0, 0, -12.0) * unit, unit.label)
    add(
        wyaw
        * Pos(0, 0, -PR.WRIST_PITCH_DROP)
        * Pos(-a40.length, 0, 0)
        * AXIS_ROT["+x"]
        * P.actuator_module("A40", pose["wrist_pitch"]),
        "wrist_pitch_module",
    )

    wpitch = f["wrist_pitch"].posed
    add(
        wpitch
        * P.cranked_link("A40", PR.WRIST_ROLL_DROP, 46.0, 3.0, "A40", "wrist_roll_bracket",
                         bottom_style="roll"),
        "wrist_roll_bracket",
    )
    add(
        wpitch
        * Pos(0, 0, -PR.WRIST_ROLL_DROP)
        * Pos(0, -a40.length, 0)
        * AXIS_ROT["+y"]
        * P.actuator_module("A40", pose["wrist_roll"]),
        "wrist_roll_module",
    )

    # -- hand ------------------------------------------------------------
    wroll = f["wrist_roll"].posed
    add(wroll * P.palm(), "palm")

    h = PR.HAND
    knuckle_z = -(PR.ARM["wrist_to_palm"] + h["palm"][2])
    span = (h["n_fingers"] - 1) * h["finger_pitch"]
    prox_a, dist_a = FINGER_POSE["proximal"], FINGER_POSE["distal"]

    for i in range(h["n_fingers"]):
        x = -span / 2 + i * h["finger_pitch"]
        knuckle = wroll * Pos(x, 0, knuckle_z) * Rot(prox_a, 0, 0)
        add(
            knuckle * P.phalanx(h["proximal_len"], h["finger_w"], h["finger_t"], "proximal"),
            f"finger{i + 1}_proximal",
        )
        add(knuckle * Rot(0, 90, 0) * P.bearing_ring("623"), f"finger{i + 1}_knuckle_bearing")
        pip = knuckle * Pos(0, 0, -h["proximal_len"]) * Rot(dist_a, 0, 0)
        add(
            pip * P.phalanx(h["distal_len"], h["finger_w"], h["finger_t"], "distal", tip=True),
            f"finger{i + 1}_distal",
        )
        add(pip * Rot(0, 90, 0) * P.bearing_ring("623"), f"finger{i + 1}_pip_bearing")

    # Thumb: rotated into opposition on the palm's +X pad.
    thumb_root = (
        wroll
        * P.thumb_pad_frame()
        * Pos(0, 0, -h["thumb_pad_len"])
        * Rot(FINGER_POSE["thumb_base"], 0, 0)
    )
    add(
        thumb_root * P.phalanx(h["thumb_proximal_len"], h["finger_w"], h["finger_t"], "proximal"),
        "thumb_proximal",
    )
    thumb_pip = thumb_root * Pos(0, 0, -h["thumb_proximal_len"]) * Rot(dist_a, 0, 0)
    add(
        thumb_pip
        * P.phalanx(h["thumb_distal_len"], h["finger_w"], h["finger_t"], "distal", tip=True),
        "thumb_distal",
    )

    return parts


def build_torso(asm):
    """Base, column, torso shell, internal structure and electronics."""
    plate = asm.add(P.base_plate(), "base_plate")
    col = asm.add(Pos(0, 0, PR.BASE_PLATE["thk"]) * P.column(), "pedestal_column")
    asm.add(
        Pos(0, -PR.COLUMN["width"] / 2 - PR.COVER_WALL / 2,
            PR.BASE_PLATE["thk"] + PR.COLUMN["flange_thk"] + PR.COLUMN["access_z"]
            + PR.COLUMN["access_h"] / 2)
        * P.column_hatch_cover(),
        "column_hatch_cover",
    )

    at = Pos(0, 0, K.TORSO_Z)
    shell = asm.add(at * P.torso_shell(), "torso_shell")
    rails = asm.add_module(
        "torso_equipment", [at * s for s in P.torso_equipment_rails().children]
    )
    payload = asm.add_module(
        "electronics", [at * s for s in P.electronics_payload().children]
    )

    # Datums that record how the stack actually bolts together.
    asm.rigid_frame(plate, "column_seat", Location((0, 0, PR.BASE_PLATE["thk"])))
    asm.rigid_frame(col, "torso_seat", Location((0, 0, K.TORSO_Z)))
    asm.rigid_frame(shell, "column_flange", Location((0, 0, K.TORSO_Z)))
    return [plate, col, shell, rails, payload]


def _register_joints(asm, arm_parts, pose, side: int):
    """Record each arm joint as a native revolute frame with its real limits."""
    from build123d import Axis

    f = K.arm_frames(pose, side=1)
    by_name = {p.label: p for p in arm_parts}
    axis_dir = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
    driven = {
        "shoulder_pitch": "shoulder_roll_housing",
        "shoulder_roll": "shoulder_yaw_housing",
        "shoulder_yaw": "upper_arm",
        "elbow_pitch": "forearm",
        "wrist_yaw": "forearm_tube",
        "wrist_pitch": "wrist_roll_bracket",
        "wrist_roll": "palm",
    }
    tag = "right" if side > 0 else "left"
    for joint in PR.JOINTS:
        part = by_name.get(driven[joint.name])
        if part is None:
            continue
        origin = f[joint.name].origin.position
        # Mirroring about YZ negates X for both the axis POINT and its
        # DIRECTION; negating only the point would leave the left arm's
        # joint sense reversed.
        p = (origin.X * side, origin.Y, origin.Z + K.TORSO_Z)
        dx, dy, dz = axis_dir[joint.axis]
        d = (dx * side, dy, dz)
        lo, hi = joint.limits
        if side < 0 and joint.axis in PR.MIRRORED_AXES:
            lo, hi = -hi, -lo
        try:
            asm.revolute_frame(
                part,
                f"{tag}_{joint.name}",
                Axis(p, d),
                angular_range=(lo, hi),
            )
        except Exception:
            # A joint datum is documentation here, not a build dependency.
            pass


def build_assembly(pose: dict[str, float] | str = None):
    pose = POSE if pose is None else (K.POSES[pose] if isinstance(pose, str) else pose)
    asm = AssemblyHelper("stationary_dual_arm_humanoid")

    build_torso(asm)

    right = build_arm(asm, pose)
    right_placed = [Pos(0, 0, K.TORSO_Z) * s for s in right]
    for src, dst in zip(right, right_placed):
        dst.label = src.label
    asm.add_module("right_arm", right_placed)

    left_placed = [mirror(s, about=Plane.YZ) for s in right_placed]
    for src, dst in zip(right, left_placed):
        dst.label = src.label
    asm.add_module("left_arm", left_placed)

    _register_joints(asm, right_placed, pose, side=1)
    _register_joints(asm, left_placed, pose, side=-1)

    return asm


def gen_step():
    return build_assembly().build()


if __name__ == "__main__":
    shape = gen_step()
    bb = shape.bounding_box()
    print(f"solids: {len(shape.solids())}")
    print(f"bbox  : {bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f} mm")
    print(f"z     : {bb.min.Z:.1f} .. {bb.max.Z:.1f} mm")
