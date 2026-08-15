"""Forward kinematics and pose handling for the dual-arm torso.

The assembly places every link by evaluating this chain, so a pose change is a
parameter change, not a geometry edit. Joint limits are enforced here: an
out-of-range pose raises rather than silently producing an impossible model.
"""

from __future__ import annotations

from dataclasses import dataclass

from build123d import Location, Pos, Rot

from humanoid_params import (
    ARM,
    BASE_PLATE,
    COLUMN,
    HAND,
    JOINTS,
    MIRRORED_AXES,
    TORSO,
    WALL,
)

# Height of the torso's inner floor above the world origin.
TORSO_Z = BASE_PLATE["thk"] + COLUMN["flange_thk"] * 2 + COLUMN["height"]

#: Outer face of the torso's shoulder mounting pad, right (+X) side. The
#: clavicle bracket bolts here; the pad stands 8 mm proud of the side wall.
SHOULDER_PAD_PROUD = 8.0
SHOULDER_ORIGIN = (
    TORSO["width"] / 2 + SHOULDER_PAD_PROUD,
    0.0,
    TORSO["shoulder_z"],
)

#: A relaxed "ready" pose: arms forward, elbows bent, wrists level.
READY_POSE = {
    "shoulder_pitch": 25.0,
    "shoulder_roll": -12.0,
    "shoulder_yaw": 0.0,
    "elbow_pitch": 55.0,
    "wrist_yaw": 0.0,
    "wrist_pitch": -10.0,
    "wrist_roll": 0.0,
}

#: Arm straight out horizontally: the worst case for the torque budget.
OUTSTRETCHED_POSE = {
    "shoulder_pitch": 90.0,
    "shoulder_roll": 0.0,
    "shoulder_yaw": 0.0,
    "elbow_pitch": 0.0,
    "wrist_yaw": 0.0,
    "wrist_pitch": 0.0,
    "wrist_roll": 0.0,
}

#: Everything at zero: arms hanging straight down.
ZERO_POSE = {j.name: 0.0 for j in JOINTS}

#: Fully folded, to exercise the self-collision check.
TUCKED_POSE = {
    "shoulder_pitch": 15.0,
    "shoulder_roll": -8.0,
    "shoulder_yaw": -60.0,
    "elbow_pitch": 130.0,
    "wrist_yaw": 45.0,
    "wrist_pitch": 30.0,
    "wrist_roll": 0.0,
}

#: Arm abducted straight out sideways: the worst case for shoulder ROLL,
#: which sees no load at all in the outstretched (forward) pose.
ABDUCTED_POSE = {
    "shoulder_pitch": 0.0,
    "shoulder_roll": -90.0,
    "shoulder_yaw": 0.0,
    "elbow_pitch": 0.0,
    "wrist_yaw": 0.0,
    "wrist_pitch": 0.0,
    "wrist_roll": 0.0,
}

POSES = {
    "ready": READY_POSE,
    "abducted": ABDUCTED_POSE,
    "zero": ZERO_POSE,
    "outstretched": OUTSTRETCHED_POSE,
    "tucked": TUCKED_POSE,
}


class JointLimitError(ValueError):
    """Raised when a requested pose violates a mechanical joint limit."""


def _rot(axis: str, angle: float) -> Location:
    if axis == "x":
        return Rot(angle, 0, 0)
    if axis == "y":
        return Rot(0, angle, 0)
    if axis == "z":
        return Rot(0, 0, angle)
    raise ValueError(f"unknown joint axis {axis!r}")


def check_pose(pose: dict[str, float], side: int = 1) -> None:
    """Validate a pose against the joint limits, accounting for mirroring."""
    for joint in JOINTS:
        angle = pose.get(joint.name, 0.0)
        lo, hi = joint.limits
        if side < 0 and joint.axis in MIRRORED_AXES:
            lo, hi = -hi, -lo
        if not (lo - 1e-9 <= angle <= hi + 1e-9):
            raise JointLimitError(
                f"{joint.name}={angle:g} deg is outside [{lo:g}, {hi:g}] "
                f"for the {'left' if side < 0 else 'right'} arm"
            )


@dataclass
class Frame:
    """A resolved joint frame: where the axis is and how the child is posed."""

    name: str
    #: Location of the joint origin BEFORE this joint's own rotation.
    origin: Location
    #: Location of the joint origin AFTER rotating by the pose angle.
    posed: Location
    axis: str
    angle: float
    limits: tuple[float, float]


def arm_frames(pose: dict[str, float], side: int = 1) -> dict[str, Frame]:
    """Resolve every joint frame of one arm, in TORSO-local coordinates.

    ``side`` is +1 for the right arm and -1 for the left. The left arm is a
    mirror image, so its geometry is mirrored about the YZ plane at build time;
    the frames returned here are always for the right arm and the caller
    mirrors the finished compound.
    """
    check_pose(pose, side)

    sx, sy, sz = SHOULDER_ORIGIN
    current = Pos(sx + ARM["clavicle_len"], sy, sz)

    frames: dict[str, Frame] = {}
    for joint in JOINTS:
        origin = current * Pos(*joint.offset)
        angle = pose.get(joint.name, 0.0)
        posed = origin * _rot(joint.axis, angle)
        frames[joint.name] = Frame(
            name=joint.name,
            origin=origin,
            posed=posed,
            axis=joint.axis,
            angle=angle,
            limits=joint.limits,
        )
        current = posed

    # The palm hangs below the last wrist joint.
    frames["palm"] = Frame(
        name="palm",
        origin=current * Pos(0, 0, -ARM["wrist_to_palm"]),
        posed=current * Pos(0, 0, -ARM["wrist_to_palm"]),
        axis="z",
        angle=0.0,
        limits=(0.0, 0.0),
    )
    return frames


def fingertip_position(pose: dict[str, float]) -> tuple[float, float, float]:
    """World-frame position of the middle fingertip for the right arm."""
    frames = arm_frames(pose)
    palm = frames["palm"]
    tip = palm.posed * Pos(0, 0, -(HAND["palm"][2] + HAND["proximal_len"] + HAND["distal_len"]))
    p = tip.position
    return (p.X, p.Y, p.Z + TORSO_Z)


def reach_envelope(samples: int = 9) -> dict:
    """Sample the shoulder/elbow plane to report the real working envelope.

    Only the pitch joints are swept, which bounds the sagittal-plane reach.
    """
    from humanoid_params import JOINTS as _J

    limits = {j.name: j.limits for j in _J}
    best_far = None
    best_low = None
    best_high = None

    sp_lo, sp_hi = limits["shoulder_pitch"]
    el_lo, el_hi = limits["elbow_pitch"]
    for i in range(samples):
        sp = sp_lo + (sp_hi - sp_lo) * i / (samples - 1)
        for k in range(samples):
            el = el_lo + (el_hi - el_lo) * k / (samples - 1)
            pose = dict(ZERO_POSE, shoulder_pitch=sp, elbow_pitch=el)
            x, y, z = fingertip_position(pose)
            radius = (y**2 + (z - (TORSO_Z + TORSO["shoulder_z"])) ** 2) ** 0.5
            if best_far is None or radius > best_far[0]:
                best_far = (radius, sp, el)
            if best_low is None or z < best_low[0]:
                best_low = (z, sp, el)
            if best_high is None or z > best_high[0]:
                best_high = (z, sp, el)

    return {
        "max_radius_mm": best_far[0],
        "max_radius_pose": {"shoulder_pitch": best_far[1], "elbow_pitch": best_far[2]},
        "lowest_tip_mm": best_low[0],
        "highest_tip_mm": best_high[0],
    }


if __name__ == "__main__":
    for name, pose in POSES.items():
        x, y, z = fingertip_position(pose)
        print(f"{name:<14} right fingertip  x={x:7.1f}  y={y:7.1f}  z={z:7.1f}")
    print()
    env = reach_envelope()
    print(f"max sagittal reach  {env['max_radius_mm']:.1f} mm from the shoulder")
    print(f"fingertip height    {env['lowest_tip_mm']:.1f} .. {env['highest_tip_mm']:.1f} mm")
