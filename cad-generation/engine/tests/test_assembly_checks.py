"""Every check here breaks a working design in exactly one way and asserts that
one criterion fires. A criterion that only ever returns PASS is indistinguishable
from no criterion — which is precisely how `four_wheel_rover_30cm` revision 0
shipped detached wheels past a green harness.
"""

import math

import pytest

from engine.evaluate import evaluate
from engine.examples import simple_rover
from engine.ir import Pose, Vec3


def _result(report, prefix):
    return [r for r in report.results if r.name.startswith(prefix)]


def _wheeled_rover():
    """simple_rover with a wheel that reaches the ground and turns about its own
    axis, so the wheel criteria (which only fire on ground-contacting links) are
    in scope and should pass.

    The axle is lowered by moving the *joint origin*, not the link pose: the
    joint origin carries the pivot with it, while a link pose moves only the
    geometry and leaves the pivot where it was — which is an orbit, and is
    exactly what `wheel_rolls_in_place` exists to catch.
    """
    ir = simple_rover()
    wheel_radius = ir.link("wheel_L").geometry.params["outer_diameter"].value / 2
    joint = next(j for j in ir.joints if j.id == "bracket_to_wheel")
    joint.origin = Pose(position=Vec3(x=0.0, y=0.0, z=wheel_radius - 0.01))
    # Tube axis (local Z) onto world Y, which is what the joint spins about.
    ir.link("wheel_L").pose = Pose(rotation=Vec3(x=-math.pi / 2, y=0.0, z=0.0))
    # And it turns without end. `simple_rover` declares +/-3.1416 rad with the
    # note "one full rotation range", which is the anti-pattern `JointLimits`
    # documents — the wheel welds solid half a turn in. Left there, this fixture
    # would be a ground-contact wheel that cannot drive, which is not the thing
    # the wheel criteria are meant to be tested against.
    joint.limits.lower = joint.limits.upper = None
    return ir


def test_attached_design_passes_link_attached():
    ir = simple_rover()
    checks = _result(evaluate(ir), "link_attached[")
    # One per *moving* joint. A fixed joint's two links are the same rigid body,
    # so asking whether they touch asks whether a body touches itself —
    # `mount_fits` judges those, as volumetric overlap.
    assert len(checks) == len([j for j in ir.joints if j.kind != "fixed"])
    assert all(c.passed for c in checks), [c.detail for c in checks if not c.passed]


def test_detached_child_fails_link_attached():
    """The rover-revision-0 defect: a wheel 90mm behind everything it claims to
    be joined to."""
    ir = simple_rover()
    ir.link("wheel_L").pose = Pose(position=Vec3(x=0.0, y=0.0, z=0.09))

    failures = [c for c in _result(evaluate(ir), "link_attached[") if not c.passed]
    assert [c.name for c in failures] == ["link_attached[bracket_to_wheel]"]
    assert "touches nothing" in failures[0].detail
    assert failures[0].magnitude < 0


def test_link_attached_covers_revolute_joints_that_mount_fits_ignores():
    """mount_fits deliberately inspects fixed joints only, so before this
    criterion a revolute child could float free and nothing looked."""
    ir = simple_rover()
    ir.link("wheel_L").pose = Pose(position=Vec3(x=0.0, y=0.0, z=0.09))
    report = evaluate(ir)

    assert all(c.passed for c in _result(report, "mount_fits["))
    assert not _result(report, "link_attached[bracket_to_wheel]")[0].passed


def test_wheel_criteria_ignore_links_that_do_not_touch_the_ground():
    """An arm swinging through a wide arc is doing its job. simple_rover's wheel
    sits well above the ground, so neither wheel criterion should fire on it."""
    report = evaluate(simple_rover())
    assert _result(report, "wheel_rolls_in_place[") == []
    assert _result(report, "wheel_axis_aligned[") == []


def test_ground_contacting_wheel_on_its_own_axis_passes():
    report = evaluate(_wheeled_rover())
    checks = _result(report, "wheel_rolls_in_place[") + _result(report, "wheel_axis_aligned[")
    assert checks, "the wheel should be in scope once it reaches the ground"
    assert all(c.passed for c in checks), [c.detail for c in checks if not c.passed]


def test_wheel_whose_axis_fights_its_joint_fails_axis_aligned():
    """Revision 0's second defect: round about X, driven about Y — it tumbles
    like a tossed coin. Nothing about its mass, position or footprint changes,
    so only an axis comparison can see it."""
    ir = _wheeled_rover()
    ir.link("wheel_L").pose = Pose(
        position=Vec3(x=0.0, y=0.0, z=-0.11), rotation=Vec3(x=0.0, y=math.pi / 2, z=0.0)
    )

    check = _result(evaluate(ir), "wheel_axis_aligned[")[0]
    assert not check.passed
    assert check.magnitude == pytest.approx(-1.0, abs=1e-6)  # a full 90 degrees
    assert "tumble" in check.detail


def test_wheel_pivoting_off_its_own_axis_fails_rolls_in_place():
    """Revision 0's third defect: the joint pivoted at the chassis origin, so
    driving it swung the wheel in an orbit rather than turning it."""
    ir = _wheeled_rover()
    joint = next(j for j in ir.joints if j.id == "bracket_to_wheel")
    # Push the pivot 120mm away and pull the geometry back by the same amount:
    # the wheel ends up exactly where it was, spinning about a point it is
    # nowhere near. Nothing static changes, so only this criterion can see it.
    joint.origin = Pose(position=Vec3(x=0.12, y=0.0, z=joint.origin.position.z))
    ir.link("wheel_L").pose = Pose(
        position=Vec3(x=-0.12, y=0.0, z=0.0), rotation=Vec3(x=-math.pi / 2, y=0.0, z=0.0)
    )

    check = _result(evaluate(ir), "wheel_rolls_in_place[")[0]
    assert not check.passed
    assert "orbits" in check.detail


def test_rolls_in_place_is_not_fooled_by_a_rotating_bounding_box():
    """A cylinder turned about its own axis is unmoved, but the box drawn round
    its corners grows by root-2 — which failed a good wheel at 18.6mm until this
    criterion stopped measuring boxes."""
    ir = _wheeled_rover()
    check = _result(evaluate(ir), "wheel_rolls_in_place[")[0]
    assert check.magnitude == pytest.approx(0.0, abs=1e-9)
    assert check.passed


def test_both_wheel_criteria_are_tier_zero():
    """They inform the agent's next revision, so they have to run on every
    candidate, not behind a tier the loop never reaches."""
    report = evaluate(_wheeled_rover(), max_tier=0)
    assert _result(report, "wheel_rolls_in_place[")
    assert _result(report, "wheel_axis_aligned[")
    assert _result(report, "link_attached[")


# --- joint_can_move -----------------------------------------------------


def test_a_continuous_wheel_is_not_asked_about_limits():
    """No limits declared means no limit to hit, which is what the IR's
    `lower is None` already says. The criterion has nothing to measure and
    should stay silent rather than invent a pass."""
    assert _result(evaluate(_wheeled_rover()), "joint_can_move[") == []


def test_a_bounded_drive_wheel_fails_at_tier_0():
    """The recorded failure, caught by subtraction instead of by MuJoCo. The
    rover's drive joints carried +/-pi noted 'continuous rotation', passed every
    static criterion, and drove 34mm before welding solid against the limit."""
    ir = _wheeled_rover()
    joint = next(j for j in ir.joints if j.id == "bracket_to_wheel")
    q = joint.limits.effort.model_copy(update={"value": math.pi, "unit": "rad"})
    joint.limits.upper = q
    joint.limits.lower = q.model_copy(update={"value": -math.pi})

    checks = _result(evaluate(ir), "joint_can_move[")
    assert len(checks) == 1 and not checks[0].passed
    assert "must turn without end" in checks[0].detail
    # The magnitude reports the arc the bound leaves, and says it is optimistic
    # rather than claiming to predict what tier 2 will measure.
    assert checks[0].unit == "arc_ratio" and checks[0].magnitude > 0


def test_an_arm_joint_may_be_bounded_but_not_frozen():
    """A bounded arm joint is normal; one bounded to nothing is a fixed joint
    that costs an actuator. The wheel rule must not fire on it either way —
    simple_rover's wheel link sits above the ground, so it is an arm here."""
    from engine.ir import Quantity

    ir = simple_rover()
    joint = next(j for j in ir.joints if j.id == "bracket_to_wheel")
    prov = joint.limits.effort.provenance

    joint.limits.lower = Quantity(value=-0.4, unit="rad", provenance=prov)
    joint.limits.upper = Quantity(value=0.4, unit="rad", provenance=prov)
    passing = _result(evaluate(ir), "joint_can_move[")
    assert len(passing) == 1 and passing[0].passed

    joint.limits.lower = Quantity(value=-0.01, unit="rad", provenance=prov)
    joint.limits.upper = Quantity(value=0.01, unit="rad", provenance=prov)
    frozen = _result(evaluate(ir), "joint_can_move[")
    assert len(frozen) == 1 and not frozen[0].passed
    assert "cannot articulate" in frozen[0].detail
