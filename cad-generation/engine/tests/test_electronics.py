"""§2's electronics subsystem, §3's actuator-at-voltage model, §9's criteria."""

from __future__ import annotations

import pytest

from engine.catalogue import resolve
from engine.electrical import (
    available_torque,
    energy_budget,
    harness_resistance,
    motor_worst_case_current,
    rail_operating_point,
)
from engine.evaluate import evaluate
from engine.examples import powered_rover, simple_rover
from engine.ir import (
    BoardSpec,
    CatalogueParam,
    Electronics,
    Harness,
    Provenance,
    Quantity,
    Rail,
    RobotIR,
    Vec3,
    worst_provenance,
)


def _q(v: float, unit: str, status: str = "ASSUMED") -> Quantity:
    return Quantity(value=v, unit=unit, provenance=Provenance(status=status, source="test" if status != "ASSUMED" else "", note=""))


def _results(ir: RobotIR, max_tier: int = 1) -> dict:
    return {r.name: r for r in evaluate(ir, max_tier=max_tier).results}


# --- schema -------------------------------------------------------------


def test_a_board_on_an_unknown_rail_is_refused():
    with pytest.raises(ValueError) as exc:
        Electronics(
            rails=[],
            boards=[
                BoardSpec(
                    id="b",
                    purpose="p",
                    mounted_on="chassis",
                    rails=["nope"],
                    max_outline=Vec3(x=10, y=10, z=0),
                    max_component_height=_q(5, "mm"),
                )
            ],
        )
    assert "unknown rail" in str(exc.value)


def test_a_board_mounted_on_a_link_that_does_not_exist_is_refused():
    # The revision failure: a link renamed, a board left pointing at the old
    # name, and its measured mass silently landing nowhere.
    ir = powered_rover()
    data = ir.model_dump()
    data["electronics"]["boards"][0]["mounted_on"] = "chassis_v2"
    with pytest.raises(ValueError) as exc:
        RobotIR.model_validate(data)
    assert "mounted on unknown link" in str(exc.value)


def test_a_harness_to_nowhere_is_refused():
    ir = powered_rover()
    data = ir.model_dump()
    data["electronics"]["harnesses"][0]["to"] = "a_joint_that_does_not_exist"
    with pytest.raises(ValueError) as exc:
        RobotIR.model_validate(data)
    assert "neither a joint nor a board" in str(exc.value)


def test_electronics_is_optional_and_its_absence_is_reported_not_passed():
    # §12 #5, one level down: a robot nobody powered has not passed its power
    # checks. Without `unmodelled` the report is indistinguishable from one
    # where every electronics criterion passed.
    report = evaluate(simple_rover(), max_tier=0)
    assert simple_rover().electronics is None
    assert any("electronics" in note for note in report.unmodelled)
    assert not any(r.name.startswith("rail_margin") for r in report.results)


# --- the §3 actuator model ----------------------------------------------


def test_the_voltage_scaling_model_reproduces_an_independent_catalogue_entry():
    """The strongest available check on the linear torque-speed model.

    The catalogue holds the *same* Feetech servo family at two voltages, sourced
    from two different vendor pages: 30 kgf*cm at 12 V and 19 kgf*cm at 7.4 V.
    Scaling the 12 V entry down to 7.4 V with the model should land on the 7.4 V
    entry — and it does, within 3%. Neither number was fitted to the other.
    """
    twelve = resolve("servos", "feetech_sts3215_12v")
    seven_four = resolve("servos", "feetech_st3215_7v4")

    predicted, _, _ = available_torque(twelve, voltage_v=7.4, speed_rad_s=0.0)
    measured = seven_four.stall_torque.value

    assert predicted == pytest.approx(measured, rel=0.05), (
        f"model says {predicted:.4f} N*m at 7.4 V, the catalogue's own 7.4 V entry "
        f"says {measured:.4f} N*m"
    )


def test_torque_falls_with_speed():
    servo = resolve("servos", "feetech_sts3215_12v")
    at_rest, _, _ = available_torque(servo, voltage_v=12.0, speed_rad_s=0.0)
    at_speed, _, _ = available_torque(servo, voltage_v=12.0, speed_rad_s=3.0)
    assert at_speed < at_rest
    assert at_speed > 0


def test_a_motor_that_cannot_be_scaled_says_so_and_drops_to_assumed():
    # A stepper's holding torque does not scale linearly with supply voltage, so
    # the model refuses to pretend. The provenance is the signal.
    stepper = resolve("stepper_motors", "nema17_direct")
    torque, provenance, notes = available_torque(stepper, voltage_v=6.0)
    assert torque == pytest.approx(stepper.stall_torque.value)
    assert provenance == "ASSUMED"
    assert "could not be scaled" in notes[0]


def test_a_bipolar_stepper_is_charged_for_both_phases():
    stepper = resolve("stepper_motors", "nema17_direct")
    amps, status, note = motor_worst_case_current(stepper)
    assert amps == pytest.approx(stepper.rated_current.value * 2)
    assert status == "INFERRED"
    assert "both phases" in note or "2 phases" in note or "x2 phases" in note


def test_harness_resistance_counts_both_conductors():
    ir = powered_rover()
    harness = ir.electronics.harnesses[0]
    r = harness_resistance(harness)
    # 22 AWG copper, 250 mm one-way: rho*L/A = 1.724e-8*0.25/3.26e-7 = 13.2 mohm
    # one-way, so 26.4 mohm round trip. Counting one leg is exactly half, and
    # that is the error this asserts against.
    assert r == pytest.approx(0.0264, rel=0.02)


def test_the_rail_sags_under_load_and_the_torque_budget_sees_it():
    ir = powered_rover()
    op = rail_operating_point(ir, "v_motor")
    assert op.current_a > 0
    assert op.voltage_at_load_v < op.nominal_v
    assert op.pack_sag_v > 0
    assert op.harness_drop_v > 0

    detail = _results(ir)["joint_torque_budget[bracket_to_wheel]"].detail
    assert f"{op.voltage_at_load_v:.2f}V" in detail


def test_a_verdict_is_worth_its_weakest_input():
    assert worst_provenance("MEASURED", "CONFIRMED", "ASSUMED") == "ASSUMED"
    assert worst_provenance("MEASURED", "CONFIRMED") == "CONFIRMED"
    # The pack's internal resistance is ASSUMED, so anything computed through
    # the rail is ASSUMED however confirmed the motor's datasheet is.
    assert _results(powered_rover())["rail_margin[v_motor]"].provenance == "ASSUMED"


# --- §9 criteria --------------------------------------------------------


def test_rail_margin_responds_to_a_motor_swap():
    """§9's actual demand, as a test.

    "Coverage perturbation reaches through the boundary: perturbing motor choice
    must move `rail_margin`, or the electronics subsystem is BLIND and the
    integration is decorative."
    """
    base = powered_rover()
    before = _results(base, max_tier=0)["rail_margin[v_motor]"].magnitude

    data = base.model_dump()
    joint = next(j for j in data["joints"] if j["id"] == "bracket_to_wheel")
    joint["actuator"] = {
        "kind": "catalogue",
        "value": "dynamixel_xm430_w350",
        "catalogue": "servos",
    }
    after = _results(RobotIR.model_validate(data), max_tier=0)["rail_margin[v_motor]"].magnitude

    assert after != pytest.approx(before), (
        "changing the actuator did not move rail_margin — the electronics "
        "subsystem is BLIND and the integration is decorative (§9)"
    )


def test_a_motor_with_no_rail_is_an_erc_failure_at_the_robot_level():
    ir = powered_rover()
    data = ir.model_dump()
    data["electronics"]["joint_rail"] = {}
    result = _results(RobotIR.model_validate(data), max_tier=0)["electronics_erc"]
    assert not result.passed
    assert "bracket_to_wheel" in result.detail


def test_an_empty_rail_is_not_infinite_margin():
    # The most flattering possible reading of missing data, refused.
    ir = powered_rover()
    data = ir.model_dump()
    data["electronics"]["joint_rail"] = {}
    result = _results(RobotIR.model_validate(data), max_tier=0)["rail_margin[v_motor]"]
    assert not result.passed
    assert result.magnitude == 0.0


def test_an_undesigned_board_fails_the_gate_criterion():
    # §12 #5 again: NOT_RUN is not a pass.
    result = _results(powered_rover(), max_tier=0)["board_gate_passed"]
    assert not result.passed
    assert "never designed" in result.detail


def test_board_fits_bay_only_fires_once_a_board_has_been_routed():
    # Comparing an envelope against itself would pass by construction — the
    # decorative-integration failure in its purest form.
    assert "board_fits_bay[motor_carrier]" not in _results(powered_rover(), max_tier=0)


def test_energy_catches_the_great_robot_four_minute_battery_class():
    ir = powered_rover()
    data = ir.model_dump()
    # Same robot, a pack that cannot deliver: swap to the low-C Li-ion and drive
    # the duty cycle to continuous.
    data["electronics"]["battery"]["value"] = "liion_18650_3s2p_7ah"
    data["electronics"]["mission_duty"] = 1.0
    data["electronics"]["mission_duration"]["value"] = 36000.0  # 10 hours
    result = _results(RobotIR.model_validate(data), max_tier=0)["energy_runtime"]
    assert not result.passed
    assert result.magnitude < 1.0


def test_peak_draw_is_checked_against_the_c_rating_not_just_capacity():
    ir = powered_rover()
    data = ir.model_dump()
    data["electronics"]["battery"]["value"] = "liion_18650_3s2p_7ah"
    result = _results(RobotIR.model_validate(data), max_tier=0)["peak_draw_within_c_rating"]
    # 6.8 Ah at 2.94C is 20 A; one NEMA17 at 3.4 A passes. The criterion exists
    # for the four-motor case, and what matters here is that it is measuring the
    # right ratio, which the magnitude shows.
    budget = energy_budget(RobotIR.model_validate(data))
    assert result.magnitude == pytest.approx(budget.pack_peak_current_a / budget.peak_current_a)


def test_thermal_budget_is_assumed_however_measured_the_dissipation_is():
    ir = powered_rover()
    data = ir.model_dump()
    board = data["electronics"]["boards"][0]
    board["measured_dissipation"] = {
        "value": 2.5,
        "unit": "W",
        "provenance": {"status": "MEASURED", "source": "runs/x", "note": ""},
    }
    result = _results(RobotIR.model_validate(data), max_tier=0)[
        "board_thermal_budget[motor_carrier]"
    ]
    # The convection coefficient and the allowed rise are both ASSUMED, so the
    # verdict is ASSUMED. A MEASURED input does not launder an assumed model.
    assert result.provenance == "ASSUMED"
    assert "ASSUMED" in result.detail
