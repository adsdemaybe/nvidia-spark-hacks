r"""The SO-101 follower arm, imported from the published open-source design.

    python designs/so101_arm.py                 # writes so101_arm.ir.json
    python -m engine.evaluate designs/so101_arm.ir.json --max-tier 2

Nothing here is drawn. The arm is TheRobotStudio / Hugging Face's **SO-101**
(Apache-2.0), vendored under `vendor/so101/`, and this script is an importer:

    vendor/so101/sim/so101_new_calib.urdf   topology, joint axes, limits, and
                                            where every part sits
    vendor/so101/step/*.step                the parts themselves, as real B-rep
                                            solids
    engine.catalogue                        the STS3215 servos, at their
                                            datasheet mass

Nothing is transcribed by hand — the URDF is parsed at generation time, so the
IR cannot drift from the published design without the vendored file changing.
That matters more than it sounds: the joint origins below carry seven decimal
places and a dozen of them, and a typo in any one is a robot that is subtly not
the SO-101 while looking exactly like it.

**Why the STEP files and not the STLs the URDF names.** Both are exported from
the same Onshape model and are coincident to the last decimal — verified, not
assumed: `Upper_arm_SO101.step` spans [-65.085, 0, -35.6]..[77.085, 24.5, 31.7]
mm and `upper_arm_so101_v1.stl` spans exactly that in metres. The STEP is a
solid, so volume, centre of mass and the full inertia tensor come from
OpenCascade rather than from a triangle soup.

**Frames.** A URDF link here becomes several IR links: one per physical part,
the first carrying the link's frame and the rest fixed to it at identity, so
every part's `pose` is read in the same URDF link frame it was authored in.
The kinematic joints then connect frame-carriers, which is why the chain still
matches the published one exactly.

**What is modelled and what is not.** Printed parts are PLA at catalogue
density; servos are lumped as solid boxes at their datasheet mass and envelope.
Fasteners, cabling and the servo horns are not modelled. Collision is opt-in
bounding boxes, which is coarse for a slender arm — good enough to stand the
model up in MuJoCo, not good enough to trust a contact result at the gripper.

**This arm is heavy, and by how much is known.** Against the inertials published
in the same URDF, per link:

    link                     ours      published   ratio
    base_link               247.8 g     147.0 g     1.69
    shoulder_link           156.8 g     100.0 g     1.57
    upper_arm_link          200.6 g     103.0 g     1.95
    lower_arm_link          187.5 g     104.0 g     1.80
    wrist_link               95.1 g      79.0 g     1.20
    gripper_link            125.2 g      87.0 g     1.44
    moving_jaw              25.7 g      12.0 g     2.14
    TOTAL                  1038.7 g     632.0 g     1.64

The pattern is the diagnosis. `wrist_link` is mostly servo and is only 1.20x
out; `moving_jaw` is entirely printed and is 2.14x out. The servos are massed
from their datasheet and are right — **the printed parts are modelled as solid
PLA, and a real print is infilled.** A 20-40% infill with solid perimeters lands
almost exactly in this range.

The density is deliberately not tuned to close the gap. Picking an infill to
make the numbers match would be reverse-engineering the answer, and the infill
is a manufacturing decision nobody has made yet. Until one is, this model is an
**upper bound** on mass: torque budgets and payloads computed from it are
conservative, which is the safe direction to be wrong in, but they are not the
as-built figures. A `pla_printed` material with a stated, ASSUMED infill is the
honest fix and is not in the catalogue yet.
"""

from __future__ import annotations

import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine.assets import asset_root, sha256_of  # noqa: E402
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

_VENDOR = "so101"
_URDF = "sim/so101_new_calib.urdf"

_PLA = CatalogueParam(value="pla", catalogue="materials")
_SERVO = CatalogueParam(value="feetech_sts3215_12v", catalogue="servos")

# The two meshes that are a purchased servo rather than a printed part. Everything
# else in the URDF maps to a STEP file of the same part.
_SERVO_MESHES = {"sts3215_03a_v1.stl", "sts3215_03a_no_horn_v1.stl"}

# mesh basename -> STEP basename. The published repo names the two inconsistently
# (lowercase snake for the printed STLs, title case for the STEP exports), so the
# mapping is explicit rather than a guessed transformation.
_STEP_FOR_MESH = {
    "base_so101_v2.stl": "Base_SO101.step",
    "base_motor_holder_so101_v1.stl": "Base_motor_holder_SO101.step",
    "waveshare_mounting_plate_so101_v2.stl": "WaveShare_Mounting_Plate_SO101.step",
    "motor_holder_so101_base_v1.stl": "Motor_holder_SO101_Base.step",
    "rotation_pitch_so101_v1.stl": "Rotation_Pitch_SO101.step",
    "upper_arm_so101_v1.stl": "Upper_arm_SO101.step",
    "under_arm_so101_v1.stl": "Under_arm_SO101.step",
    "motor_holder_so101_wrist_v1.stl": "Motor_holder_SO101_Wrist.step",
    "wrist_roll_pitch_so101_v2.stl": "Wrist_Roll_Pitch_SO101.step",
    "wrist_roll_follower_so101_v1.stl": "Wrist_Roll_Follower_SO101.step",
    "moving_jaw_so101_v1.stl": "Moving_Jaw_SO101.step",
}


def _measured(note: str) -> Provenance:
    return Provenance(status="MEASURED", source=f"vendor/{_VENDOR}/{_URDF}", note=note)


def _q(value: float, unit: str, note: str) -> Quantity:
    return Quantity(value=value, unit=unit, provenance=_measured(note))


def _triple(text: str | None) -> tuple[float, float, float]:
    if not text:
        return (0.0, 0.0, 0.0)
    parts = [float(v) for v in re.split(r"\s+", text.strip())]
    return (parts[0], parts[1], parts[2])


def _pose(element: ET.Element | None) -> Pose:
    """A URDF `<origin>` as an IR Pose.

    URDF `rpy` is fixed-axis roll-pitch-yaw, i.e. Rz(yaw)Ry(pitch)Rx(roll), and
    `Pose.rotation` is intrinsic XYZ, which composes to the same matrix. So the
    three numbers carry across in order, with no conversion — the pleasant case,
    but worth stating, because silently assuming it when it is false rotates
    every part of the robot by a plausible-looking amount.
    """
    if element is None:
        return Pose()
    origin = element.find("origin")
    if origin is None:
        return Pose()
    x, y, z = _triple(origin.get("xyz"))
    roll, pitch, yaw = _triple(origin.get("rpy"))
    return Pose(
        position=Vec3(x=x, y=y, z=z),
        rotation=Vec3(x=roll, y=pitch, z=yaw),
    )


def _stl_bbox_centre(path: Path) -> tuple[float, float, float]:
    """Centre of a binary STL's bounding box, in metres.

    Needed because a servo is imported as a catalogue *component* — a box at its
    datasheet envelope — while the URDF places the vendor's servo *mesh*, whose
    local origin is its own. Aligning the two by bounding-box centre is what puts
    the lumped mass where the real servo is instead of 20 mm away from it.
    """
    data = path.read_bytes()
    count = struct.unpack("<I", data[80:84])[0]
    dtype = struct.Struct("<12fH")
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    offset = 84
    for _ in range(count):
        values = dtype.unpack_from(data, offset)
        offset += 50
        for vertex in range(3):
            for axis in range(3):
                v = values[3 + vertex * 3 + axis]
                lo[axis] = min(lo[axis], v)
                hi[axis] = max(hi[axis], v)
    return tuple((lo[i] + hi[i]) / 2.0 for i in range(3))


def _servo_geometry() -> GeometrySpec:
    return GeometrySpec(generator="component", params={"part": _SERVO}, material=_PLA)


def _step_geometry(step_name: str, root: Path) -> GeometrySpec:
    relative = f"{_VENDOR}/step/{step_name}"
    return GeometrySpec(
        generator="step_part",
        params={
            "asset": relative,
            # Pins the exact bytes evaluated. Without it the IR names a file
            # rather than a robot, and re-running against an updated vendor drop
            # would silently evaluate a different design.
            "sha256": sha256_of(root / relative),
            "collision": "bbox",
        },
        material=_PLA,
    )


def _link_id(urdf_link: str, index: int, mesh: str) -> str:
    if index == 0:
        return urdf_link
    stem = mesh.rsplit(".", 1)[0]
    if mesh in _SERVO_MESHES:
        return f"{urdf_link}__servo"
    return f"{urdf_link}__{stem}"


def so101_arm() -> RobotIR:
    root = asset_root()
    tree = ET.parse(root / _VENDOR / _URDF)
    urdf = tree.getroot()

    servo_centres: dict[str, tuple[float, float, float]] = {}
    links: list[Link] = []
    joints: list[Joint] = []
    frame_carrier: dict[str, str] = {}

    for urdf_link in urdf.findall("link"):
        name = urdf_link.get("name")
        visuals = [v for v in urdf_link.findall("visual") if v.find(".//mesh") is not None]
        if not visuals:
            # A frame marker with no geometry — SO-101's `gripper_frame_link` is
            # the tool centre point, carrying 1e-9 kg and a zero inertia tensor.
            # It is not a body, and importing it as one would fail `inertia_valid`
            # for being non-positive-definite, which would be true and useless.
            continue

        for index, visual in enumerate(visuals):
            mesh = visual.find(".//mesh").get("filename").rsplit("/", 1)[-1]
            pose = _pose(visual)
            link_id = _link_id(name, index, mesh)

            if mesh in _SERVO_MESHES:
                if mesh not in servo_centres:
                    servo_centres[mesh] = _stl_bbox_centre(root / _VENDOR / "assets" / mesh)
                cx, cy, cz = servo_centres[mesh]
                # Shift the URDF's mesh placement onto our box, whose own origin
                # is centred in X/Y with its base at Z=0.
                geometry = _servo_geometry()
                pose = Pose(
                    position=Vec3(
                        x=pose.position.x + cx,
                        y=pose.position.y + cy,
                        z=pose.position.z + cz,
                    ),
                    rotation=pose.rotation,
                )
                # `component` builds from the box's base, so drop it by half its
                # height to sit centred on the mesh centre we just aligned to.
                pose.position.z -= 0.0350 / 2
            else:
                step_name = _STEP_FOR_MESH.get(mesh)
                if step_name is None:
                    raise KeyError(f"no STEP mapping for mesh {mesh!r}; add one to _STEP_FOR_MESH")
                geometry = _step_geometry(step_name, root)

            links.append(Link(id=link_id, geometry=geometry, pose=pose))

            if index == 0:
                frame_carrier[name] = link_id
            else:
                # Identity fixed joint: the sibling shares the URDF link's frame,
                # so its pose above is read in exactly the frame it was authored in.
                joints.append(
                    Joint(
                        id=f"{link_id}__fixed",
                        kind="fixed",
                        parent=frame_carrier[name],
                        child=link_id,
                    )
                )

    for urdf_joint in urdf.findall("joint"):
        parent = urdf_joint.find("parent").get("link")
        child = urdf_joint.find("child").get("link")
        if child not in frame_carrier:
            continue  # the dropped frame marker
        kind = urdf_joint.get("type")
        if kind == "continuous":
            kind = "revolute"
        if kind not in ("revolute", "prismatic", "fixed"):
            raise ValueError(f"unsupported URDF joint type {kind!r} on {urdf_joint.get('name')}")

        limit = urdf_joint.find("limit")
        limits = None
        if kind != "fixed" and limit is not None:
            limits = JointLimits(
                lower=_q(float(limit.get("lower")), "rad", "published joint range"),
                upper=_q(float(limit.get("upper")), "rad", "published joint range"),
                effort=_q(float(limit.get("effort")), "N*m", "published effort limit"),
                velocity=_q(float(limit.get("velocity")), "rad/s", "published velocity limit"),
            )

        axis_element = urdf_joint.find("axis")
        ax, ay, az = _triple(axis_element.get("xyz")) if axis_element is not None else (0.0, 0.0, 1.0)

        joints.append(
            Joint(
                id=urdf_joint.get("name"),
                kind=kind,
                parent=frame_carrier[parent],
                child=frame_carrier[child],
                origin=_pose(urdf_joint),
                axis=Vec3(x=ax, y=ay, z=az) if kind != "fixed" else Vec3(x=0.0, y=0.0, z=1.0),
                limits=limits,
                actuator=_SERVO if kind == "revolute" else None,
            )
        )

    return RobotIR(
        name="so101_arm",
        root_link=frame_carrier["base_link"],
        links=links,
        joints=joints,
        # SO-101 is a bench arm: its base plate is bolted down, and the whole
        # design assumes it. Free-standing, its centre of mass sits 1.56 support
        # half-widths past its own footprint whenever it reaches for anything.
        base="fixed",
    )


def main() -> int:
    ir = so101_arm()
    out = Path(__file__).with_suffix(".ir.json")
    out.write_text(ir.model_dump_json(indent=2), encoding="utf-8")
    print(f"{ir.name}: {len(ir.links)} links, {len(ir.joints)} joints -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
