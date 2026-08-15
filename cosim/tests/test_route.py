"""Failure routing — the part that stops the three-way loop thrashing.

The interesting cases are the two that look identical from outside: a joint that is too
slow because the board cannot deliver more current, and a joint that is too slow because
the mechanics needs more torque. Sending either to the wrong side wastes a round.
"""

from __future__ import annotations

import math

import pytest

from cosim.gate import DRV8833_AS_BUILT, DriverLimits, TaskGoal, evaluate
from cosim.route import route
from cosim.rollout import Frame, RolloutResult


def make(*, current: float, peak: float, angle_end: float, duty: float = 1.0,
         diverged: bool = False, n: int = 200) -> RolloutResult:
    r = RolloutResult(diverged=diverged, divergence_reason="synthetic" if diverged else "")
    for i in range(n):
        r.frames.append(
            Frame(
                seq=i, t=i * 0.001, duty=duty, omega_shaft_rad_s=50.0,
                current_a=current, current_peak_a=peak, torque_nm=current * 0.0055,
                joint_angle_rad=angle_end * (i + 1) / n, joint_vel_rad_s=0.0,
            )
        )
    r.peak_current_a = peak
    r.simulated_s = n * 0.001
    r.wall_s = 0.01
    return r


GOAL = TaskGoal(joint_angle_rad=math.radians(90), deadline_s=1.0)

# A thermally comfortable driver, for the cases that test the *task* discriminator.
#
# DRV8833_AS_BUILT is 190 °C/W — the figure measured from rover-motor-driver's actual
# copper — and at any current high enough to saturate it, the thermal check fires first
# and routes to the board for heat before the task discriminator is ever reached. That is
# correct behaviour and a true statement about that package, but it makes it useless for
# testing the "slow joint, whose fault" logic. So these use a driver with real headroom,
# which is what a board carrying this current would have to use anyway.
COOL = DriverLimits(
    name="a driver with a thermal pad",
    continuous_current_a=2.0,
    peak_current_a=2.0,
    r_ds_on_ohm=0.1,
    thermal_c_per_w=40.0,
)


def test_slow_joint_with_a_saturated_driver_goes_to_the_board():
    """No mechanical change helps while the electronics is already at its limit."""
    r = make(current=1.8, peak=1.9, angle_end=math.radians(20))
    v = evaluate(r, goal=GOAL, limits=COOL)
    decision = route(v, r, COOL)
    assert decision.side == "pcb"
    assert "saturated" in decision.reason
    assert "mechanics will not help" in decision.suggestion


def test_slow_joint_with_a_loafing_driver_goes_to_the_mechanics():
    """Same symptom, opposite cause — and the current is what tells them apart."""
    r = make(current=0.3, peak=0.5, angle_end=math.radians(20))
    v = evaluate(r, goal=GOAL, limits=COOL)
    decision = route(v, r, COOL)
    assert decision.side == "cad"
    assert "current to spare" in decision.reason
    assert "Widening a trace would change nothing" in decision.suggestion


def test_the_middle_is_reported_as_ambiguous_rather_than_guessed():
    """A part-loaded driver is genuinely both-ish, and saying so beats oscillating."""
    r = make(current=1.2, peak=1.3, angle_end=math.radians(20))
    v = evaluate(r, goal=GOAL, limits=COOL)
    decision = route(v, r, COOL)
    assert decision.side == "both"
    assert "ambiguous" in decision.reason


def test_over_rating_goes_to_the_board_whatever_else_failed():
    r = make(current=0.5, peak=3.0, angle_end=math.radians(20))
    v = evaluate(r, goal=GOAL, limits=COOL)
    decision = route(v, r, COOL)
    assert decision.side == "pcb"
    assert "over its rating" in decision.reason
    # The fix is the silicon, not the copper — a distinction worth stating, because
    # "current too high" reads like a trace problem and is not.
    assert "not the trace" in decision.suggestion


def test_thermal_failure_is_a_board_change_not_a_motor_change():
    r = make(current=1.4, peak=1.5, angle_end=math.radians(90), duty=1.0)
    v = evaluate(r, goal=GOAL, limits=DRV8833_AS_BUILT)
    decision = route(v, r, DRV8833_AS_BUILT)
    assert decision.side == "pcb"
    assert "heat" in decision.reason
    assert "thermal pad" in decision.suggestion


def test_divergence_belongs_to_neither_side():
    """Sending a solver failure to a designer has them chase a fault that is not there."""
    r = make(current=0.5, peak=0.6, angle_end=math.radians(20), diverged=True)
    v = evaluate(r, goal=GOAL, limits=DRV8833_AS_BUILT)
    decision = route(v, r, DRV8833_AS_BUILT)
    assert decision.side == "simulation"
    assert "shorten the control period" in decision.suggestion


def test_a_passing_rollout_routes_nowhere():
    r = make(current=0.4, peak=0.6, angle_end=math.radians(90))
    v = evaluate(r, goal=GOAL, limits=DRV8833_AS_BUILT, motor_provenance="CONFIRMED")
    assert v.passed
    assert route(v, r, DRV8833_AS_BUILT).side == "none"


def test_a_better_driver_moves_the_same_failure_to_the_other_side():
    """The routing follows the headroom, so replacing the driver changes the owner.

    This is what makes the loop converge rather than ping-pong: after F1 fits a bigger
    driver, the same slow joint becomes F2's problem, and it is F2's turn to move.
    """
    r = make(current=1.8, peak=1.9, angle_end=math.radians(20))
    small = COOL
    big = DriverLimits(
        name="a driver with real headroom", continuous_current_a=5.0, peak_current_a=6.0,
        r_ds_on_ohm=0.1, thermal_c_per_w=40.0,
    )
    assert route(evaluate(r, goal=GOAL, limits=small), r, small).side == "pcb"
    assert route(evaluate(r, goal=GOAL, limits=big), r, big).side == "cad"
