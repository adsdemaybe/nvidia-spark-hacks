#!/usr/bin/env python3
"""Generate the real SO-101 robot bundle (Shadow Robot Spatial Demonstration
Pipeline spec sections 9-11) from the vendored upstream URDF.

    uv run python tools/make_real_so101_bundle.py

Replaces the earlier made-up placeholder (fixtures/robot/test_arm.urdf) as
the source for fixtures/spatial-training/robots/so101/ -- robot_id="so101"
now means the real, professional SO-101 arm
(github.com/TheRobotStudio/SO-ARM100, Apache-2.0), not a fabricated stand-in.
test_arm.urdf itself is untouched; the old TEACH pipeline's DEFAULT_URDF
still points at it.

Parses the vendored URDF (fixtures/robot/so101_real/so101_new_calib.urdf)
directly via ElementTree rather than hand-transcribing joint/mesh origins --
the real numbers are long floats across ~20 <visual> blocks; retyping them
by hand is exactly the kind of transcription risk this script exists to
avoid. Writes:

    fixtures/spatial-training/robots/so101/
    ├── manifest.json     (RobotManifest)
    ├── robot_ir.json      (RobotIR -- kinematic chain, joints only)
    ├── actuator.json      (5 arm joints + a real "gripper" joint, not null --
    │                        the real SO-101 is 5-DOF arm + 1-DOF gripper,
    │                        not 6-DOF; the placeholder it replaces was 6-DOF
    │                        with no gripper at all)
    ├── visual_meshes.json (NEW, additive: per-link real STL mesh list with
    │                        each mesh's own local origin -- xr-web's
    │                        ShadowRobot attaches these to the matching
    │                        joint group so a real, multi-part professional
    │                        mesh assembly renders instead of procedural
    │                        boxes. Not part of any existing pydantic
    │                        contract -- purely a client-side rendering
    │                        hint, loaded directly by shadowRobot.ts.)
    ├── robot.urdf          (copy, mesh filename="assets/..." rewritten to
    │                        "meshes/..." to match this bundle's layout)
    └── meshes/*.stl        (copied verbatim from the vendored source)
"""

from __future__ import annotations

import json
import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

ARVR_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ARVR_ROOT / "fixtures" / "robot" / "so101_real"
SOURCE_URDF = SOURCE_DIR / "so101_new_calib.urdf"
BUNDLE_DIR = ARVR_ROOT / "fixtures" / "spatial-training" / "robots" / "so101"

ROBOT_ID = "so101"
BASE_LINK = "base_link"
END_EFFECTOR_FRAME = "gripper_frame_link"
GRIPPER_JOINT_NAME = "gripper"


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n")
    print(f"wrote {path.relative_to(ARVR_ROOT)}")


def rpy_to_quat_xyzw(r: float, p: float, y: float) -> tuple[float, float, float, float]:
    """URDF's <origin rpy="r p y"/> is the fixed-axis (extrinsic) ZYX
    convention: R = Rz(y) * Ry(p) * Rx(r). Standard roll-pitch-yaw ->
    quaternion formula, same one ROS's tf.transformations uses."""
    cr, sr = math.cos(r / 2), math.sin(r / 2)
    cp, sp = math.cos(p / 2), math.sin(p / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy
    return (qx, qy, qz, qw)


def _parse_xyz(text: str | None) -> tuple[float, float, float]:
    if not text:
        return (0.0, 0.0, 0.0)
    x, y, z = (float(v) for v in text.split())
    return (x, y, z)


_Vec3 = tuple[float, float, float]
_Quat = tuple[float, float, float, float]


def _parse_origin(origin_el: ET.Element | None) -> tuple[_Vec3, _Quat]:
    if origin_el is None:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)
    position = _parse_xyz(origin_el.get("xyz"))
    r, p, y = _parse_xyz(origin_el.get("rpy"))
    return position, rpy_to_quat_xyzw(r, p, y)


def parse_joints(root: ET.Element) -> list[dict]:
    joints = []
    for joint_el in root.findall("joint"):
        name = joint_el.get("name")
        jtype = joint_el.get("type")
        parent = joint_el.find("parent").get("link")
        child = joint_el.find("child").get("link")
        position, orientation = _parse_origin(joint_el.find("origin"))

        axis_el = joint_el.find("axis")
        axis = _parse_xyz(axis_el.get("xyz")) if axis_el is not None else None

        limit_el = joint_el.find("limit")

        def _limit_attr(attr: str, el: ET.Element | None = limit_el) -> float | None:
            if el is None:
                return None
            value = el.get(attr)
            return float(value) if value else None

        lower = _limit_attr("lower")
        upper = _limit_attr("upper")
        velocity = _limit_attr("velocity")

        joints.append(
            {
                "name": name,
                "type": jtype,
                "parent_link": parent,
                "child_link": child,
                "origin_position_m": position,
                "origin_orientation_xyzw": orientation,
                "axis": axis,
                "lower_limit": lower,
                "upper_limit": upper,
                "velocity_limit": velocity,
            }
        )
    return joints


def parse_links_order(root: ET.Element, joints: list[dict]) -> list[str]:
    """Topological order from base_link, following joint parent->child, so
    RobotIR.links reads as a real kinematic chain rather than file order."""
    children_of: dict[str, list[str]] = {}
    for j in joints:
        children_of.setdefault(j["parent_link"], []).append(j["child_link"])

    order: list[str] = []
    stack = [BASE_LINK]
    while stack:
        link = stack.pop(0)
        if link in order:
            continue
        order.append(link)
        stack.extend(children_of.get(link, []))
    return order


def parse_visual_meshes(root: ET.Element) -> dict[str, list[dict]]:
    """Per-link list of real mesh parts (filename + local origin), for
    xr-web's ShadowRobot to render the actual multi-part SO-101 assembly
    instead of procedural placeholder geometry."""
    meshes_by_link: dict[str, list[dict]] = {}
    for link_el in root.findall("link"):
        link_name = link_el.get("name")
        entries = []
        for visual_el in link_el.findall("visual"):
            mesh_el = visual_el.find("geometry/mesh")
            if mesh_el is None:
                continue
            filename = mesh_el.get("filename")
            mesh_file = Path(filename).name  # "assets/x.stl" -> "x.stl"
            position, orientation = _parse_origin(visual_el.find("origin"))
            entries.append(
                {
                    "mesh": mesh_file,
                    "origin_position_m": position,
                    "origin_orientation_xyzw": orientation,
                }
            )
        if entries:
            meshes_by_link[link_name] = entries
    return meshes_by_link


def copy_urdf_and_meshes() -> None:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    meshes_dir = BUNDLE_DIR / "meshes"
    meshes_dir.mkdir(exist_ok=True)

    for stl in (SOURCE_DIR / "assets").glob("*.stl"):
        shutil.copy2(stl, meshes_dir / stl.name)
    n_meshes = len(list(meshes_dir.glob("*.stl")))
    print(f"copied {n_meshes} meshes -> {meshes_dir.relative_to(ARVR_ROOT)}")

    urdf_text = SOURCE_URDF.read_text()
    urdf_text = urdf_text.replace('filename="assets/', 'filename="meshes/')
    lines = urdf_text.splitlines(keepends=True)
    xml_decl, rest = lines[0], lines[1:]
    note = (
        "<!-- Vendored from github.com/TheRobotStudio/SO-ARM100 "
        "(Apache-2.0) via tools/make_real_so101_bundle.py.\n"
        "     See fixtures/robot/so101_real/NOTICE.md for provenance. -->\n"
    )
    dest = BUNDLE_DIR / "robot.urdf"
    dest.write_text(xml_decl + note + "".join(rest))
    print(f"wrote {dest.relative_to(ARVR_ROOT)}")


def main() -> None:
    root = ET.fromstring(SOURCE_URDF.read_text())
    joints = parse_joints(root)
    links = parse_links_order(root, joints)
    visual_meshes = parse_visual_meshes(root)

    copy_urdf_and_meshes()

    write_json(
        BUNDLE_DIR / "manifest.json",
        {
            "schema_version": "1.0",
            "robot_id": ROBOT_ID,
            "source": "fixture",
            "robot_ir": "robot_ir.json",
            "urdf": "robot.urdf",
            # No single flattened GLB -- shadowRobot.ts loads the real
            # per-link STL meshes directly via visual_meshes.json instead.
            # RobotManifest.visual_glb is a required str, so this points at
            # the file that's actually true rather than a fabricated path
            # nothing resolves.
            "visual_glb": "visual_meshes.json",
            "usd": None,
            "base_link": BASE_LINK,
            "end_effectors": [END_EFFECTOR_FRAME],
        },
    )

    write_json(
        BUNDLE_DIR / "robot_ir.json",
        {
            "schema_version": "1.0",
            "robot_id": ROBOT_ID,
            "base_link": BASE_LINK,
            "end_effector_frame": END_EFFECTOR_FRAME,
            "links": links,
            "joints": joints,
        },
    )

    arm_joint_names = [
        j["name"] for j in joints if j["type"] == "revolute" and j["name"] != GRIPPER_JOINT_NAME
    ]
    write_json(
        BUNDLE_DIR / "actuator.json",
        {
            "schema_version": "1.0",
            "joints": arm_joint_names,
            # Real jaw, not null -- see robot_ir.json's "gripper" joint
            # (revolute, drives moving_jaw_so101_v1_link).
            "gripper": GRIPPER_JOINT_NAME,
        },
    )

    write_json(BUNDLE_DIR / "visual_meshes.json", {"links": visual_meshes})

    print(f"arm joints: {arm_joint_names}")
    print(f"gripper joint: {GRIPPER_JOINT_NAME}")
    print(f"end effector frame: {END_EFFECTOR_FRAME}")


if __name__ == "__main__":
    main()
