"""RobotIR -> URDF.

URDF is the format every downstream engine in §1 can read: MuJoCo compiles it
directly, Pinocchio parses it, and Isaac ingests it. It is therefore the bridge
tier 2 crosses, and the reason this module exists at all.

Three things it gets right on purpose, because the prototype got each of them
wrong first:

- **Inertials are real.** Mass, centre of mass and a full inertia tensor, all
  computed from the B-rep (`engine.mass_properties`), never estimated. A URDF
  with plausible-looking round-number inertias is the single easiest way to get
  a simulation that runs, looks fine, and is wrong.
- **Collision is primitives.** Declared by the geometry generator, never a hull
  over the solid — see `geometry.registry.CollisionShape`.
- **Frames are kept distinct.** A link's *frame* is where its parent joint puts
  it; a link's *geometry* sits at a further offset inside that frame. URDF
  expresses the second as the `<origin>` of each element, and conflating the two
  puts every mass in the wrong place while still producing a valid file.

Units are SI throughout — the IR's units — because URDF is metres and kilograms
and no conversion is needed. The mm boundary is inside the geometry registry
and does not reach here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.dom import minidom

import numpy as np

from engine.geometry.registry import CollisionShape, build as build_geometry
from engine.ir import Link, Pose, RobotIR, Vec3
from engine.kinematics import pose_to_matrix
from engine.mass_properties import MassProperties


def _fmt(values) -> str:
    return " ".join(f"{float(v):.9g}" for v in values)


def _origin_element(parent: ET.Element, xyz, rpy) -> None:
    ET.SubElement(parent, "origin", xyz=_fmt(xyz), rpy=_fmt(rpy))


def _pose_applied(pose: Pose, point: Vec3) -> np.ndarray:
    """`point`, expressed in the link's geometry frame, moved into the link frame."""
    transform = pose_to_matrix(pose)
    return transform[:3, :3] @ np.array(point.as_tuple(), dtype=float) + transform[:3, 3]


def _inertial(link_el: ET.Element, link: Link, mp: MassProperties) -> None:
    inertial = ET.SubElement(link_el, "inertial")
    # The tensor is about the CoM and expressed in the *geometry* frame's axes,
    # so the origin carries both: the CoM position in the link frame, and the
    # geometry frame's own rotation. URDF then interprets the six components in
    # that frame, which means the tensor never has to be rotated by hand — the
    # step where a similarity transform is easiest to get subtly wrong.
    _origin_element(inertial, _pose_applied(link.pose, mp.com), link.pose.rotation.as_tuple())
    ET.SubElement(inertial, "mass", value=f"{mp.mass:.9g}")
    i = mp.inertia
    ET.SubElement(
        inertial,
        "inertia",
        ixx=f"{i.ixx:.9g}",
        ixy=f"{i.ixy:.9g}",
        ixz=f"{i.ixz:.9g}",
        iyy=f"{i.iyy:.9g}",
        iyz=f"{i.iyz:.9g}",
        izz=f"{i.izz:.9g}",
    )


def _shape_geometry(parent: ET.Element, shape: CollisionShape) -> None:
    geometry = ET.SubElement(parent, "geometry")
    if shape.kind == "box":
        ET.SubElement(geometry, "box", size=_fmt(shape.size))
    elif shape.kind == "cylinder":
        radius, length = shape.size
        ET.SubElement(geometry, "cylinder", radius=f"{radius:.9g}", length=f"{length:.9g}")
    else:
        raise ValueError(f"cannot express collision primitive {shape.kind!r} in URDF")


def _shape_elements(link_el: ET.Element, link: Link, shapes) -> None:
    for index, shape in enumerate(shapes):
        for tag in ("visual", "collision"):
            element = ET.SubElement(link_el, tag, name=f"{link.id}_{tag}_{index}")
            _origin_element(
                element, _pose_applied(link.pose, shape.origin), link.pose.rotation.as_tuple()
            )
            _shape_geometry(element, shape)


def _joint_type(joint) -> str:
    if joint.kind != "revolute":
        return joint.kind
    # A revolute joint with no positional bounds is a wheel, not an arm joint,
    # and URDF spells that "continuous". Emitting "revolute" instead makes the
    # bounds binding: MuJoCo enforces the range, and a wheel authored +/-pi
    # stops after half a turn — which is a robot that drives 34 mm and welds,
    # while every static criterion still passes.
    return "revolute" if (joint.limits is not None and joint.limits.bounded) else "continuous"


def to_urdf(ir: RobotIR) -> str:
    """Serialise `ir` as a URDF document. Pure: no filesystem access."""
    robot = ET.Element("robot", name=ir.name)

    missing: list[str] = []
    for link in ir.links:
        built = build_geometry(link.geometry)
        link_el = ET.SubElement(robot, "link", name=link.id)
        _inertial(link_el, link, built.mass_properties)
        if not built.collision:
            missing.append(f"{link.id} (generator {link.geometry.generator!r})")
            continue
        _shape_elements(link_el, link, built.collision)

    if missing:
        # Refused rather than approximated. A bounding box substituted here would
        # be indistinguishable in the output from a collider the generator meant,
        # and tier 2 would report contact results for a shape nobody authored.
        raise ValueError(
            "these links declare no collision primitive, so no contact simulation "
            "can be exported for them: " + ", ".join(missing)
        )

    for joint in ir.joints:
        joint_el = ET.SubElement(robot, "joint", name=joint.id, type=_joint_type(joint))
        _origin_element(joint_el, joint.origin.position.as_tuple(), joint.origin.rotation.as_tuple())
        ET.SubElement(joint_el, "parent", link=joint.parent)
        ET.SubElement(joint_el, "child", link=joint.child)
        if joint.kind != "fixed":
            ET.SubElement(joint_el, "axis", xyz=_fmt(joint.axis.as_tuple()))
        if joint.limits is not None:
            # effort and velocity apply to a continuous joint too — URDF keeps
            # them on <limit> and only lower/upper are omitted when unbounded.
            attrs = {
                "effort": f"{joint.limits.effort.value:.9g}",
                "velocity": f"{joint.limits.velocity.value:.9g}",
            }
            if joint.limits.bounded:
                attrs["lower"] = f"{joint.limits.lower.value:.9g}"
                attrs["upper"] = f"{joint.limits.upper.value:.9g}"
            ET.SubElement(joint_el, "limit", **attrs)

    raw = ET.tostring(robot, encoding="unicode")
    return minidom.parseString(raw).toprettyxml(indent="  ")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="python -m engine.export.urdf")
    parser.add_argument("ir_path", help="path to a RobotIR JSON file")
    parser.add_argument("-o", "--out", default=None, help="output .urdf path (default: stdout)")
    args = parser.parse_args(argv)

    ir = RobotIR.model_validate(json.loads(Path(args.ir_path).read_text(encoding="utf-8")))
    urdf = to_urdf(ir)
    if args.out:
        Path(args.out).write_text(urdf, encoding="utf-8")
        print(f"{ir.name}: {len(ir.links)} links, {len(ir.joints)} joints -> {args.out}")
    else:
        print(urdf)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
