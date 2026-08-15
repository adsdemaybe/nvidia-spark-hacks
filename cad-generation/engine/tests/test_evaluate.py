import pytest

from engine.evaluate import compute_mass_properties, evaluate
from engine.examples import simple_rover
from engine.ir import Provenance, Quantity


def test_evaluate_runs_tier0_only_by_default():
    ir = simple_rover()
    report = evaluate(ir)
    assert report.tiers_run == [0]
    names = {r.name for r in report.results}
    assert "static_margin" in names
    assert "mount_fits[chassis_to_bracket]" in names
    # bracket_to_wheel is revolute, not fixed — mount_fits must not fire for it
    assert "mount_fits[bracket_to_wheel]" not in names


def test_simple_rover_passes_tier0():
    report = evaluate(simple_rover())
    assert report.passed, report.failures


def test_static_margin_fails_when_com_pushed_off_the_edge():
    ir = simple_rover()
    chassis = ir.link("chassis")
    # Shrink the chassis footprint drastically so the wheel/bracket assembly's
    # mass pulls the overall CoM outside the support polygon.
    chassis.geometry.params["length"] = Quantity(
        value=0.02, unit="m", provenance=Provenance(status="ASSUMED", source="")
    )
    chassis.geometry.params["width"] = Quantity(
        value=0.02, unit="m", provenance=Provenance(status="ASSUMED", source="")
    )
    report = evaluate(ir)
    margin = next(r for r in report.results if r.name == "static_margin")
    assert not margin.passed
    assert margin.magnitude < 0.10


def test_mount_fits_fails_when_bracket_moved_off_chassis():
    ir = simple_rover()
    joint = next(j for j in ir.joints if j.id == "chassis_to_bracket")
    joint.origin.position.x = 5.0  # move the bracket far away from the chassis
    report = evaluate(ir)
    fit = next(r for r in report.results if r.name == "mount_fits[chassis_to_bracket]")
    assert not fit.passed
    assert fit.magnitude == pytest.approx(0.0)


def test_evaluate_reports_skipped_tiers_when_bounded_below_registry():
    report = evaluate(simple_rover(), max_tier=-1)
    assert report.tiers_run == []
    assert report.tiers_skipped == [0, 1]
    assert report.results == []


def test_evaluate_at_tier1_runs_torque_budget_and_reports_no_skips():
    # simple_rover's only actuated joint is bracket_to_wheel (revolute).
    report = evaluate(simple_rover(), max_tier=1)
    assert report.tiers_run == [0, 1]
    assert report.tiers_skipped == []
    assert any(r.name == "joint_torque_budget[bracket_to_wheel]" for r in report.results)


def test_compute_mass_properties_covers_every_link():
    ir = simple_rover()
    props = compute_mass_properties(ir)
    assert set(props) == {link.id for link in ir.links}
    assert all(mp.mass > 0 for mp in props.values())
