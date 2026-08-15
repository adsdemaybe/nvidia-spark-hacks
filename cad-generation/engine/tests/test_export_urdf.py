"""RobotIR -> URDF, and the frame bookkeeping that makes it correct.

A URDF that parses is not a URDF that is right: masses in the wrong frame,
inertias about the wrong point, and welded wheels all produce valid XML.
"""

import xml.etree.ElementTree as ET

import pytest

from engine.evaluate import compute_mass_properties
from engine.examples import simple_rover
from engine.export.urdf import to_urdf
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


def _q(value: float, unit: str = "m") -> Quantity:
    return Quantity(value=value, unit=unit, provenance=Provenance(status="ASSUMED", source=""))


def _plate_link(link_id: str, pose: Pose | None = None) -> Link:
    return Link(
        id=link_id,
        geometry=GeometrySpec(
            generator="plate",
            params={"length": _q(0.2), "width": _q(0.1), "thickness": _q(0.01)},
            material=CatalogueParam(value="aluminum_6061", catalogue="materials"),
        ),
        pose=pose or Pose(),
    )


def _tree(urdf: str) -> ET.Element:
    return ET.fromstring(urdf)


def test_urdf_is_wellformed_and_names_every_link_and_joint():
    ir = simple_rover()
    root = _tree(to_urdf(ir))

    assert root.tag == "robot"
    assert root.get("name") == ir.name
    assert {el.get("name") for el in root.findall("link")} == {link.id for link in ir.links}
    assert {el.get("name") for el in root.findall("joint")} == {j.id for j in ir.joints}


def test_every_link_carries_a_computed_inertial():
    ir = simple_rover()
    props = compute_mass_properties(ir)
    root = _tree(to_urdf(ir))

    for element in root.findall("link"):
        inertial = element.find("inertial")
        assert inertial is not None, f"{element.get('name')} has no inertial"
        mass = float(inertial.find("mass").get("value"))
        assert mass == pytest.approx(props[element.get("name")].mass, rel=1e-9)

        inertia = inertial.find("inertia")
        # Present, non-zero, and not a placeholder — the failure mode here is a
        # tidy set of round numbers that simulates happily and means nothing.
        assert float(inertia.get("ixx")) > 0
        assert float(inertia.get("iyy")) > 0
        assert float(inertia.get("izz")) > 0


def test_inertial_origin_is_the_com_in_the_link_frame_not_the_geometry_frame():
    """A link's geometry sits at an offset inside its own frame. URDF wants the
    CoM in the *link* frame, so the offset has to be applied — skipping it puts
    every mass in the wrong place while still producing a valid file."""
    offset = 0.5
    ir = RobotIR(
        name="offset", root_link="a",
        links=[_plate_link("a", pose=Pose(position=Vec3(x=offset, y=0.0, z=0.0)))],
    )
    inertial = _tree(to_urdf(ir)).find("link").find("inertial")
    x = float(inertial.find("origin").get("xyz").split()[0])

    # The plate is corner-origin, so its CoM is half its length along X, and the
    # link pose then shifts the whole thing by `offset`.
    assert x == pytest.approx(offset + 0.1, rel=1e-9)


def test_collision_is_primitives_and_a_bracket_gets_one_per_arm():
    ir = RobotIR(
        name="l", root_link="b",
        links=[
            Link(
                id="b",
                geometry=GeometrySpec(
                    generator="bracket",
                    params={
                        "arm_a_length": _q(0.04), "arm_b_length": _q(0.03),
                        "thickness": _q(0.004), "width": _q(0.03),
                    },
                    material=CatalogueParam(value="aluminum_6061", catalogue="materials"),
                ),
            )
        ],
    )
    link_el = _tree(to_urdf(ir)).find("link")
    collisions = link_el.findall("collision")

    assert len(collisions) == 2, "an L-bracket is two boxes; one box fills the inside corner"
    for collision in collisions:
        assert collision.find("geometry/box") is not None
    assert link_el.find("collision/geometry/mesh") is None, "collision must never be a mesh"


def test_a_tube_collides_as_a_cylinder():
    ir = RobotIR(
        name="w", root_link="w",
        links=[
            Link(
                id="w",
                geometry=GeometrySpec(
                    generator="tube",
                    params={
                        "outer_diameter": _q(0.09), "inner_diameter": _q(0.06), "length": _q(0.03),
                    },
                    material=CatalogueParam(value="aluminum_6061", catalogue="materials"),
                ),
            )
        ],
    )
    cylinder = _tree(to_urdf(ir)).find("link/collision/geometry/cylinder")
    assert cylinder is not None
    assert float(cylinder.get("radius")) == pytest.approx(0.045)
    assert float(cylinder.get("length")) == pytest.approx(0.03)


def test_a_revolute_joint_without_limits_is_continuous_not_revolute():
    """URDF treats <limit> as mandatory on a revolute joint. Parsers that
    tolerate its absence default the range to zero, which welds the wheel
    solid — a robot that cannot drive, reported as one that will not."""
    ir = RobotIR(
        name="r", root_link="a",
        links=[_plate_link("a"), _plate_link("b")],
        joints=[Joint(id="spin", kind="revolute", parent="a", child="b",
                      axis=Vec3(x=0.0, y=1.0, z=0.0))],
    )
    joint = _tree(to_urdf(ir)).find("joint")
    assert joint.get("type") == "continuous"
    assert joint.find("limit") is None


def test_a_revolute_joint_with_limits_stays_revolute_and_keeps_them():
    limits = JointLimits(
        lower=_q(-1.0, "rad"), upper=_q(1.0, "rad"),
        effort=_q(2.0, "N*m"), velocity=_q(3.0, "rad/s"),
    )
    ir = RobotIR(
        name="r", root_link="a",
        links=[_plate_link("a"), _plate_link("b")],
        joints=[Joint(id="elbow", kind="revolute", parent="a", child="b", limits=limits)],
    )
    joint = _tree(to_urdf(ir)).find("joint")
    assert joint.get("type") == "revolute"
    assert float(joint.find("limit").get("upper")) == pytest.approx(1.0)


def test_a_link_with_no_collision_primitive_is_refused_not_approximated():
    """A substituted bounding box would be indistinguishable in the output from
    a collider the generator meant, and tier 2 would report contact results for
    a shape nobody authored."""
    from engine.geometry.registry import GeometryResult, _REGISTRY, register

    @register("colliderless_test_shape")
    def _colliderless(params, material):
        from engine.geometry.registry import _plate
        built = _plate(params, material)
        return GeometryResult(part=built.part, mass_properties=built.mass_properties)

    try:
        ir = RobotIR(
            name="nc", root_link="a",
            links=[
                Link(
                    id="a",
                    geometry=GeometrySpec(
                        generator="colliderless_test_shape",
                        params={"length": _q(0.2), "width": _q(0.1), "thickness": _q(0.01)},
                        material=CatalogueParam(value="aluminum_6061", catalogue="materials"),
                    ),
                )
            ],
        )
        with pytest.raises(ValueError, match="no collision primitive"):
            to_urdf(ir)
    finally:
        _REGISTRY.pop("colliderless_test_shape", None)
