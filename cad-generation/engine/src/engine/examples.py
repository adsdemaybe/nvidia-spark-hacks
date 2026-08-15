"""Illustrative example IRs — used by tests and by the `--dump-example` CLI
flag to produce a real ir.json to run `python -m engine.evaluate` against.
Not shipped designs: dimensions are round numbers chosen to exercise the
tier-0 criteria, not to be manufacturable.
"""

from __future__ import annotations

from engine.ir import (
    BoardSpec,
    CatalogueParam,
    Electronics,
    GeometrySpec,
    Harness,
    Joint,
    JointLimits,
    Link,
    MountPattern,
    Pose,
    Provenance,
    Quantity,
    Rail,
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
        # 0.078, not 0.080: the bracket's own geometry is offset 2mm down the
        # chassis (see the pose above), so an axle at the bracket's nominal
        # arm_b height left the wheel hanging 2mm clear of the arm holding it.
        # `link_attached` caught it; nothing before that criterion existed to.
        origin=Pose(position=Vec3(x=0.0, y=0.0, z=0.078)),
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


def _designed(value: float, unit: str, note: str) -> Quantity:
    """A requirement the robot side owns — a decision, not a measurement."""
    return Quantity(
        value=value,
        unit=unit,
        provenance=Provenance(status="INFERRED", source="example fixture", note=note),
    )


def powered_rover() -> RobotIR:
    """`simple_rover` with the §2 electronics subsystem filled in.

    The fixture that makes the electronics criteria testable, and the one that
    shows what the subsystem is *for*: the same chassis and the same motor, now
    with a rail whose sag the torque budget actually reads. The pack is a 3S
    LiPo with real internal resistance, so `joint_torque_budget` here computes
    against the voltage at the motor rather than the datasheet's stated
    condition — which is the entire §3 (v3) change, visible in one fixture.

    No board facts: `gate_status` is `NOT_RUN`, because `pcb-ai` has not designed
    this board. That is deliberate. It is what a robot looks like between the
    board spec being emitted and the run coming back, and every criterion that
    reads a measured fact has to behave sensibly in that state.
    """
    ir = simple_rover()

    rail = Rail(
        id="v_motor",
        voltage=_designed(11.1, "V", "3S LiPo nominal"),
        # The tier-0 energy pass computes the worst case; this is the budget the
        # robot side commits to and hands the board. 5 A against a single NEMA17
        # holding both phases at 1.7 A is a real 1.47x margin, not a round number.
        budget_current=_designed(5.0, "A", "worst-case actuator draw plus policy margin"),
    )

    board = BoardSpec(
        id="motor_carrier",
        purpose="motor-driver carrier",
        mounted_on="chassis",
        rails=["v_motor"],
        # The bay is 60 x 40 mm inside a 300 x 200 mm chassis — a plausible
        # pocket, and small enough that `board_fits_bay` can actually fail.
        max_outline=Vec3(x=60.0, y=40.0, z=0.0),
        max_component_height=_designed(12.0, "mm", "clearance under the chassis deck"),
        mount=MountPattern(
            hole_diameter=_designed(3.2, "mm", "M3 clearance"),
            positions=[
                Vec3(x=0.004, y=0.004, z=0.0),
                Vec3(x=0.056, y=0.004, z=0.0),
                Vec3(x=0.004, y=0.036, z=0.0),
                Vec3(x=0.056, y=0.036, z=0.0),
            ],
        ),
        keepouts=["swept volume of the left wheel, 60mm diameter about the axle"],
        connector_rules=["at_edge:J1:south"],
    )

    harness = Harness(
        id="J1_to_bracket_to_wheel",
        from_board="motor_carrier",
        to="bracket_to_wheel",
        rail="v_motor",
        length=_designed(0.25, "m", "estimated before the board exists; replaced at ingest"),
        # 22 AWG, 0.326 mm^2. Small enough that the drop is visible, which is the
        # point of having the criterion at all.
        conductor_area=_designed(3.26e-7, "m^2", "22 AWG stranded copper"),
    )

    return RobotIR(
        name="powered_rover",
        root_link=ir.root_link,
        links=ir.links,
        joints=ir.joints,
        electronics=Electronics(
            battery=CatalogueParam(
                kind="catalogue", value="lipo_3s_2200mah_25c", catalogue="batteries"
            ),
            rails=[rail],
            boards=[board],
            harnesses=[harness],
            joint_rail={"bracket_to_wheel": "v_motor"},
            mission_duty=0.3,
            mission_duration=_designed(1800.0, "s", "30 minute mission"),
        ),
    )
