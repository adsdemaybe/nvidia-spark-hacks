"""Mechanics — checked against physics that can be worked out by hand.

A simulator that runs is not a simulator that is right. Each of these has an expected
answer derivable on paper, so a wrong sign or a misplaced gear ratio fails rather than
producing a plausible number.
"""

from __future__ import annotations

import math

import pytest

from cosim.mjcf import Mechanics, to_mjcf
from cosim.robot import Joint, Link, RobotSpec, from_dict, one_joint_arm


def test_spec_validates_and_names_every_problem():
    bad = RobotSpec(
        name="bad",
        links=[Link(name="a", mass_kg=0.0)],
        joints=[Joint(name="j", parent="nope", child="also_nope", axis=(0, 0, 0))],
    )
    problems = bad.validate()
    # All of them, not just the first: a spec fixed one error at a time takes as many
    # round trips as it has mistakes.
    assert any("mass must be positive" in p for p in problems)
    assert any("parent" in p for p in problems)
    assert any("child" in p for p in problems)
    assert any("axis is zero" in p for p in problems)


def test_missing_mesh_and_inertia_degrade_and_are_recorded():
    spec = from_dict(
        {"name": "r", "links": [{"name": "l1", "mass_kg": 0.2, "size_m": [0.1, 0.05, 0.02]}]}
    )
    link = spec.link("l1")
    assert link.inertia_kgm2 is not None, "physics needs an inertia even when CAD has none"
    # Uniform box: Ix = m(y^2+z^2)/12
    ix_expected = 0.2 * (0.05**2 + 0.02**2) / 12
    assert link.inertia_kgm2[0] == pytest.approx(ix_expected, rel=1e-9)
    subjects = {(a.subject, a.field_name) for a in spec.assumptions}
    assert ("l1", "inertia_kgm2") in subjects
    assert ("l1", "mesh") in subjects, "a substituted geometry must be declared"


def test_mjcf_is_valid_and_loads():
    spec = one_joint_arm()
    xml = to_mjcf(spec)
    assert "<mujoco" in xml and 'type="hinge"' in xml
    mech = Mechanics(spec, xml=xml)
    assert mech.time == 0.0


def test_constant_torque_accelerates_the_joint():
    """τ = Jα. With gravity off and a known inertia, the angle after t is predictable."""
    spec = one_joint_arm(gear_ratio=1.0)
    spec.gravity = (0.0, 0.0, 0.0)
    spec.joints[0].damping = 0.0
    mech = Mechanics(spec)

    torque = 0.002
    for _ in range(1000):  # 1 s at the 1 ms default timestep
        mech.apply_motor_torque("M1", torque)
        mech.step()

    angle, vel = mech.joint_state("j1")
    assert vel > 0, "a positive torque must spin the joint positively"
    # θ = ½αt² and ω = αt, so θ should be about half of ω·t. Loose because MuJoCo's
    # inertia includes the link's own box, which this does not recompute by hand.
    assert angle == pytest.approx(0.5 * vel * 1.0, rel=0.05)


def test_gear_ratio_multiplies_shaft_torque():
    """A gearbox is torque gain. Ten times the ratio, ten times the joint acceleration."""

    def spin(ratio: float) -> float:
        spec = one_joint_arm(gear_ratio=ratio)
        spec.gravity = (0.0, 0.0, 0.0)
        spec.joints[0].damping = 0.0
        mech = Mechanics(spec)
        for _ in range(500):
            mech.apply_motor_torque("M1", 0.0005)
            mech.step()
        return mech.joint_state("j1")[1]

    slow = spin(1.0)
    fast = spin(10.0)
    assert fast == pytest.approx(slow * 10.0, rel=0.02)


def test_shaft_speed_is_joint_speed_times_ratio():
    """The direction of the ratio matters: the joint turns slowly, the motor turns fast.

    Getting this backwards would silently scale back-EMF by ratio², and the rollout
    would still look smooth.
    """
    spec = one_joint_arm(gear_ratio=50.0)
    spec.gravity = (0.0, 0.0, 0.0)
    spec.joints[0].damping = 0.0
    mech = Mechanics(spec)
    for _ in range(200):
        mech.apply_motor_torque("M1", 0.0005)
        mech.step()

    _, joint_vel = mech.joint_state("j1")
    assert mech.motor_shaft_speed("M1") == pytest.approx(joint_vel * 50.0, rel=1e-9)
    assert abs(mech.motor_shaft_speed("M1")) > abs(joint_vel)


def test_gravity_pulls_an_unpowered_arm_down():
    """No torque, arm out horizontally: it must fall, and about the right axis."""
    spec = one_joint_arm()
    mech = Mechanics(spec)
    for _ in range(300):
        mech.step()
    angle, vel = mech.joint_state("j1")
    assert abs(angle) > 1e-3, "an unpowered arm under gravity should move"
    assert abs(vel) > 1e-3
