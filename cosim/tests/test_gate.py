"""The gate, tested by making it fail.

A gate nobody has watched reject something is a gate nobody should trust — the checklist
says so, and M1's three bugs all produced plausible output that a pass-only test would
have waved through. So each check here is exercised in the direction that matters.
"""

from __future__ import annotations

import math

import pytest

from cosim.gate import DRV8833_AS_BUILT, DriverLimits, TaskGoal, evaluate
from cosim.rollout import Frame, RolloutResult


def make(
    n: int = 100,
    *,
    angle_end: float = math.radians(90),
    vel_end: float = 0.0,
    current: float = 0.5,
    peak: float | None = None,
    duty: float = 1.0,
    diverged: bool = False,
) -> RolloutResult:
    """A synthetic rollout, so each check can be driven independently."""
    r = RolloutResult(diverged=diverged, divergence_reason="synthetic" if diverged else "")
    for i in range(n):
        frac = (i + 1) / n
        r.frames.append(
            Frame(
                seq=i,
                t=i * 0.001,
                duty=duty,
                omega_shaft_rad_s=100.0,
                current_a=current,
                current_peak_a=peak if peak is not None else current,
                torque_nm=current * 0.0055,
                joint_angle_rad=angle_end * frac,
                joint_vel_rad_s=vel_end if i == n - 1 else 1.0,
            )
        )
    r.peak_current_a = peak if peak is not None else current
    r.simulated_s = n * 0.001
    r.wall_s = 0.01
    return r


def test_a_good_rollout_passes():
    v = evaluate(
        make(current=0.4, peak=0.8),
        goal=TaskGoal(joint_angle_rad=math.radians(90), deadline_s=1.0),
        motor_provenance="CONFIRMED",
    )
    assert v.passed, v.summary()
    assert all(c.passed for c in v.checks)


def test_divergence_is_a_simulation_failure_and_stops_there():
    """A diverged rollout must not be graded on its trajectory — it does not have one."""
    v = evaluate(make(diverged=True), goal=TaskGoal(joint_angle_rad=math.radians(90)))
    assert not v.passed
    assert len(v.checks) == 1, "nothing downstream of divergence should be reported"
    assert "simulation failure, not a design failure" in v.checks[0].detail


def test_a_joint_that_never_arrives_fails():
    v = evaluate(
        make(angle_end=math.radians(20)),
        goal=TaskGoal(joint_angle_rad=math.radians(90), tolerance_rad=math.radians(5)),
    )
    task = next(c for c in v.checks if c.name == "task")
    assert not task.passed
    assert "never reached" in task.detail


def test_passing_through_the_target_is_not_arriving_at_it():
    """The joint hits 90° mid-flight and keeps going. That is not doing the task."""
    r = make(angle_end=math.radians(180), vel_end=6.0)
    v = evaluate(r, goal=TaskGoal(joint_angle_rad=math.radians(90), must_settle=True))
    task = next(c for c in v.checks if c.name == "task")
    assert not task.passed
    assert "did not stay" in task.detail


def test_missing_the_deadline_fails_even_though_it_arrives():
    r = make(n=2000, angle_end=math.radians(90))
    v = evaluate(r, goal=TaskGoal(joint_angle_rad=math.radians(90), deadline_s=0.2))
    task = next(c for c in v.checks if c.name == "task")
    assert not task.passed
    assert "deadline" in task.detail


def test_peak_current_over_the_rating_fails():
    """Average current inside the rating does not make the worst instant safe."""
    v = evaluate(make(current=0.3, peak=3.5))
    survival = next(c for c in v.checks if c.name == "electrical survival")
    assert not survival.passed
    assert "would be damaged" in survival.detail


def test_thermal_uses_the_duty_cycle_not_the_peak():
    """The same driver passes at a realistic duty and fails at continuous full current.

    This is the check that distinguishes a driver which is genuinely too small from one
    that is only too small if you assume the robot never stops.
    """
    gentle = evaluate(make(current=0.46, duty=0.6))
    hard = evaluate(make(current=1.4, duty=1.0))
    t_gentle = next(c for c in gentle.checks if c.name.startswith("thermal"))
    t_hard = next(c for c in hard.checks if c.name.startswith("thermal"))
    assert t_gentle.passed, t_gentle.detail
    assert not t_hard.passed, t_hard.detail
    assert "°C junction" in t_hard.detail


def test_assumed_constants_are_reported_but_do_not_block():
    """An assumed constant makes a number un-quotable, not wrong."""
    v = evaluate(make(current=0.4), motor_provenance="ASSUMED")
    prov = next(c for c in v.checks if c.name == "provenance")
    assert not prov.passed
    assert not prov.blocking
    assert v.passed, "provenance must not block an otherwise sound rollout"
    assert any("no motor has been chosen" in a for a in v.assumptions)


def test_a_better_package_changes_the_verdict():
    """The thermal check is about the board as built, not about the part number.

    The same current through the same silicon passes with a thermal pad and fails
    without one, which is the finding rover-motor-driver produced for real.
    """
    hot = DRV8833_AS_BUILT
    with_pad = DriverLimits(
        name="DRV8833 in HTSSOP-16 with PowerPAD and vias",
        continuous_current_a=1.5,
        peak_current_a=2.0,
        r_ds_on_ohm=0.72,
        thermal_c_per_w=41.0,
    )
    rollout = make(current=1.0, duty=1.0)
    assert not next(c for c in evaluate(rollout, limits=hot).checks if c.name.startswith("thermal")).passed
    assert next(c for c in evaluate(rollout, limits=with_pad).checks if c.name.startswith("thermal")).passed
