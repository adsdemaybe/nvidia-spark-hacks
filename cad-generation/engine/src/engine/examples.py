"""Illustrative example IRs — used by tests and by the `--dump-example` CLI
flag to produce a real ir.json to run `python -m engine.evaluate` against.
Not shipped designs: dimensions are round numbers chosen to exercise the
tier-0 criteria, not to be manufacturable.
"""

from __future__ import annotations

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


def _assumed(value: float, unit: str, note: str) -> Quantity:
    return Quantity(value=value, unit=unit, provenance=Provenance(status="ASSUMED", source="example fixture", note=note))


def simple_rover() -> RobotIR:
    """chassis (plate, root) -- fixed --> bracket_L (bracket) -- revolute --> wheel_L (tube)."""
    chassis = Link(
        id="chassis",
        geometry=GeometrySpec(
            generator="plate",
            params={
                "length": _assumed(0.30, "m", "chassis footprint"),
                "width": _assumed(0.20, "m", "chassis footprint"),
                "thickness": _assumed(0.01, "m", "chassis footprint"),
            },
            material=CatalogueParam(kind="catalogue", value="aluminum_6061", catalogue="materials"),
        ),
    )

    bracket = Link(
        id="bracket_L",
        geometry=GeometrySpec(
            generator="bracket",
            params={
                "arm_a_length": _assumed(0.05, "m", "wheel mount bracket"),
                "arm_b_length": _assumed(0.08, "m", "wheel mount bracket"),
                "thickness": _assumed(0.005, "m", "wheel mount bracket"),
                "width": _assumed(0.03, "m", "wheel mount bracket"),
            },
            material=CatalogueParam(kind="catalogue", value="aluminum_6061", catalogue="materials"),
        ),
        # Embeds 2mm into the chassis top face — a stand-in for a fastener/boss
        # engagement depth, so mount_fits sees genuine overlap rather than two
        # faces merely touching (ratio 0, a false FAIL for a bolted joint).
        pose=Pose(position=Vec3(x=0.0, y=0.0, z=-0.002)),
    )

    wheel = Link(
        id="wheel_L",
        geometry=GeometrySpec(
            generator="tube",
            params={
                "outer_diameter": _assumed(0.06, "m", "wheel"),
                "inner_diameter": _assumed(0.05, "m", "wheel hub bore"),
                "length": _assumed(0.02, "m", "wheel width"),
            },
            material=CatalogueParam(kind="catalogue", value="pla", catalogue="materials"),
        ),
    )

    chassis_to_bracket = Joint(
        id="chassis_to_bracket",
        kind="fixed",
        parent="chassis",
        child="bracket_L",
        # Mounted on the chassis top face (z=0.01m), off-center in Y — most
        # real chassis carry asymmetric equipment, and a perfectly symmetric
        # fixture makes static_margin scale-invariant (see engine/tests).
        origin=Pose(position=Vec3(x=0.10, y=0.03, z=0.01)),
    )

    bracket_to_wheel = Joint(
        id="bracket_to_wheel",
        kind="revolute",
        parent="bracket_L",
        child="wheel_L",
        origin=Pose(position=Vec3(x=0.0, y=0.0, z=0.08)),
        axis=Vec3(x=0.0, y=1.0, z=0.0),
        limits=JointLimits(
            lower=_assumed(-3.1416, "rad", "one full rotation range"),
            upper=_assumed(3.1416, "rad", "one full rotation range"),
            effort=_assumed(1.0, "N*m", "placeholder torque budget"),
            velocity=_assumed(10.0, "rad/s", "placeholder speed budget"),
        ),
        actuator=CatalogueParam(kind="catalogue", value="nema17_direct", catalogue="stepper_motors"),
    )

    return RobotIR(
        name="simple_rover",
        root_link="chassis",
        links=[chassis, bracket, wheel],
        joints=[chassis_to_bracket, bracket_to_wheel],
    )
