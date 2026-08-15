import pytest

from engine.evaluate import evaluate
from engine.ir import CatalogueParam, GeometrySpec, Joint, JointLimits, Link, Provenance, Quantity, RobotIR


def _q(value: float, unit: str) -> Quantity:
    return Quantity(value=value, unit=unit, provenance=Provenance(status="ASSUMED", source=""))


def _pendulum(arm_length: float, motor_key: str) -> RobotIR:
    """base (plate, root) -- revolute (axis Y) --> arm (plate).

    The arm's local geometry origin is at the joint pivot and its plate
    solid extends along local +X, so its CoM sits at (arm_length/2, 0, 0)
    relative to the joint — a textbook horizontal-arm torque = m*g*r case.
    """
    base = Link(
        id="base",
        geometry=GeometrySpec(
            generator="plate",
            params={"length": _q(0.05, "m"), "width": _q(0.05, "m"), "thickness": _q(0.01, "m")},
            material=CatalogueParam(value="aluminum_6061", catalogue="materials"),
        ),
    )
    arm = Link(
        id="arm",
        geometry=GeometrySpec(
            generator="plate",
            params={"length": _q(arm_length, "m"), "width": _q(0.02, "m"), "thickness": _q(0.005, "m")},
            material=CatalogueParam(value="aluminum_6061", catalogue="materials"),
        ),
    )
    shoulder = Joint(
        id="shoulder",
        kind="revolute",
        parent="base",
        child="arm",
        axis={"x": 0.0, "y": 1.0, "z": 0.0},
        limits=JointLimits(
            lower=_q(0.0, "rad"),
            upper=_q(0.0, "rad"),
            effort=_q(1.0, "N*m"),
            velocity=_q(1.0, "rad/s"),
        ),
        actuator=CatalogueParam(value=motor_key, catalogue="stepper_motors"),
    )
    return RobotIR(name="pendulum", root_link="base", links=[base, arm], joints=[shoulder])


def test_tier1_not_run_by_default():
    report = evaluate(_pendulum(0.2, "nema17_direct"))
    assert report.tiers_run == [0]
    assert not any(r.name.startswith("joint_torque_budget") for r in report.results)


def test_light_short_arm_passes_on_small_motor():
    report = evaluate(_pendulum(0.2, "nema17_direct"), max_tier=1)
    torque = next(r for r in report.results if r.name == "joint_torque_budget[shoulder]")
    # arm: 0.2 x 0.02 x 0.005 m aluminum plate, r=0.1m -> required ~= 0.053 N*m
    assert torque.detail.startswith("required=0.05")
    assert torque.passed
    assert torque.magnitude == pytest.approx(1.0 - 0.0530 / 0.45, abs=0.01)


def test_heavy_long_arm_fails_on_small_motor_but_passes_geared():
    small = evaluate(_pendulum(1.0, "nema17_direct"), max_tier=1)
    small_torque = next(r for r in small.results if r.name == "joint_torque_budget[shoulder]")
    assert not small_torque.passed

    geared = evaluate(_pendulum(1.0, "nema17_planetary_13.73"), max_tier=1)
    geared_torque = next(r for r in geared.results if r.name == "joint_torque_budget[shoulder]")
    assert geared_torque.passed


def test_fixed_and_unactuated_joints_produce_no_torque_result():
    ir = _pendulum(0.2, "nema17_direct")
    ir.joints[0].kind = "fixed"
    ir.joints[0].limits = None
    ir.joints[0].actuator = None
    report = evaluate(ir, max_tier=1)
    assert not any(r.name.startswith("joint_torque_budget") for r in report.results)
