"""§9: coverage perturbation has to reach through the electronics boundary.

    "Coverage perturbation reaches through the boundary: perturbing motor choice
    must move `rail_margin`, or the electronics subsystem is BLIND and the
    integration is decorative."

Both findings these tests pin were found by running the tool, not by reading the
code — which is the argument for having it.
"""

from __future__ import annotations

import math

import pytest

from engine.coverage import analyze_catalogue_coverage, analyze_coverage
from engine.electrical import energy_budget
from engine.evaluate import evaluate
from engine.examples import powered_rover, simple_rover
from engine.ir import RobotIR


def _with_servo() -> RobotIR:
    data = powered_rover().model_dump()
    joint = next(j for j in data["joints"] if j["id"] == "bracket_to_wheel")
    joint["actuator"] = {
        "kind": "catalogue",
        "value": "dynamixel_xm430_w350",
        "catalogue": "servos",
    }
    return RobotIR.model_validate(data)


def test_perturbing_motor_choice_moves_rail_margin():
    """§9's stated test of whether the integration is real."""
    coverage = analyze_catalogue_coverage(powered_rover(), max_tier=0)
    joint = next(c for c in coverage if c.subject == "joint:bracket_to_wheel")

    assert not joint.blind
    assert joint.responses["rail_margin[v_motor]"] > 0.01, (
        "changing the motor did not move rail_margin — the electronics subsystem "
        "is BLIND and the integration is decorative (§9)"
    )


def test_the_swap_searches_across_actuator_catalogues_not_within_one():
    """The blindness the narrow search was hiding.

    All three `stepper_motors` entries share a 1.7 A rated current, so swapping
    among them moves no electrical criterion at all. Searching only the joint's
    own catalogue reported `rail_margin` as BLIND when it is not — a false
    finding, which is worse than a missing one because somebody acts on it.
    """
    coverage = analyze_catalogue_coverage(powered_rover(), max_tier=0)
    tried = next(c for c in coverage if c.subject == "joint:bracket_to_wheel").alternatives_tried

    assert any(t.startswith("servos/") for t in tried)
    assert any(t.startswith("stepper_motors/") for t in tried)


def test_the_electronics_subsystems_own_variables_are_perturbed():
    coverage = {(c.link_id, c.param_name) for c in analyze_coverage(powered_rover(), max_tier=0)}
    assert ("rail:v_motor", "budget_current") in coverage
    assert ("harness:J1_to_bracket_to_wheel", "length") in coverage
    assert ("harness:J1_to_bracket_to_wheel", "conductor_area") in coverage


def test_a_robot_with_no_electronics_gains_no_extra_variables():
    coverage = analyze_coverage(simple_rover(), max_tier=0)
    assert all(":" not in c.link_id for c in coverage)


def test_rail_voltage_is_no_longer_blind():
    """The finding that produced `actuator_voltage_in_range`.

    Coverage reported the rail voltage as BLIND: a +/-10% perturbation moved no
    criterion at all, because nothing checked that the rail could actually run
    the motor bolted to it. §9: "BLIND = ... harness bug, needs a new criterion,
    not more search."
    """
    coverage = analyze_coverage(_with_servo(), max_tier=0)
    voltage = next(
        c for c in coverage if c.link_id == "rail:v_motor" and c.param_name == "voltage"
    )
    assert not voltage.blind
    assert voltage.responses["actuator_voltage_in_range[bracket_to_wheel]"] > 0.01


def test_a_motor_with_no_stated_voltage_window_is_reported_not_passed():
    # The stepper states no voltage_min/max, so the rail cannot be checked
    # against it. That is a gap in the catalogue, and §12 #5 says a check that
    # did not run is not a pass.
    results = {r.name: r for r in evaluate(powered_rover(), max_tier=0).results}
    result = results["actuator_voltage_in_range[bracket_to_wheel]"]
    assert not result.passed
    assert "source the operating window" in result.detail


def test_a_brownout_is_caught_by_the_voltage_window():
    data = _with_servo().model_dump()
    # A rail that sags below the XM430's 10.0 V minimum.
    data["electronics"]["rails"][0]["voltage"]["value"] = 9.0
    results = {r.name: r for r in evaluate(RobotIR.model_validate(data), max_tier=0).results}
    result = results["actuator_voltage_in_range[bracket_to_wheel]"]
    assert not result.passed
    assert result.magnitude < 0.0


def test_an_unpriceable_robot_reports_unknown_runtime_not_unlimited():
    """`inf` lied twice at once: it made the criterion pass, and it made coverage
    report infinite sensitivity to a motor swap."""
    data = powered_rover().model_dump()
    joint = next(j for j in data["joints"] if j["id"] == "bracket_to_wheel")
    # A servo the catalogue states neither stall nor rated current for.
    joint["actuator"] = {"kind": "catalogue", "value": "feetech_st3215_7v4", "catalogue": "servos"}
    ir = RobotIR.model_validate(data)

    budget = energy_budget(ir)
    assert budget.runtime_s == 0.0
    assert math.isfinite(budget.runtime_s)
    assert any("not unlimited" in n for n in budget.notes)

    result = {r.name: r for r in evaluate(ir, max_tier=0).results}["energy_runtime"]
    assert not result.passed


def test_every_coverage_response_is_a_finite_number():
    # A coverage matrix with an `inf` in it has one cell that dominates every
    # other and no reader can act on.
    for c in analyze_coverage(powered_rover(), max_tier=0):
        assert all(math.isfinite(v) for v in c.responses.values()), c
    for c in analyze_catalogue_coverage(powered_rover(), max_tier=0):
        assert all(math.isfinite(v) for v in c.responses.values()), c
