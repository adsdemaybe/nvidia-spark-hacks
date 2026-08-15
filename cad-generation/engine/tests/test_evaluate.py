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
    assert report.tiers_skipped == [0, 1, 2]
    assert report.results == []


def test_evaluate_at_tier1_runs_torque_budget_and_reports_contact_sim_skipped():
    # simple_rover's only actuated joint is bracket_to_wheel (revolute).
    report = evaluate(simple_rover(), max_tier=1)
    assert report.tiers_run == [0, 1]
    # Tier 2 exists now, so bounding the run at tier 1 means contact simulation
    # did not happen — and the report has to say so rather than omit it.
    assert report.tiers_skipped == [2]
    assert any(r.name == "joint_torque_budget[bracket_to_wheel]" for r in report.results)


def test_compute_mass_properties_covers_every_link():
    ir = simple_rover()
    props = compute_mass_properties(ir)
    assert set(props) == {link.id for link in ir.links}
    assert all(mp.mass > 0 for mp in props.values())


# --- regressions ---


def test_an_evaluation_that_ran_nothing_is_not_a_pass():
    """`all([])` is True, so an empty result set used to report PASS.

    Silence is not consent: no criteria run means nothing was verified, which is
    the opposite of a design clearing the harness.
    """
    from engine.evaluate import EvaluationReport

    empty = EvaluationReport(
        design_id="x", design_name="nothing-ran", results=[], tiers_run=[], tiers_skipped=[0, 1]
    )
    assert not empty.passed
    assert empty.failures == []  # nothing failed either — there was simply no evidence


def test_cyclic_joint_graph_raises_rather_than_hanging():
    """A cycle used to spin link_frames forever, with no error and no progress."""
    import engine.kinematics as kinematics
    from engine.ir import Joint, RobotIR

    from tests.test_ir import _link

    # The IR validator rejects a cycle at construction, so build one past it to
    # prove the kinematics walk is independently safe.
    ir = RobotIR(
        name="cyc", root_link="a", links=[_link("a"), _link("b")],
        joints=[Joint(id="j1", kind="fixed", parent="a", child="b")],
    )
    object.__setattr__(
        ir, "joints",
        [*ir.joints, Joint(id="j2", kind="fixed", parent="b", child="a")],
    )
    with pytest.raises(ValueError, match="cycle"):
        kinematics.link_frames(ir)


def test_unimplemented_tiers_are_reported_skipped():
    """§3/§11 #5: a tier that didn't run is not a pass. Asking for a tier with
    no criteria registered used to report `skipped: []` — an unverified design
    presented as fully verified.

    Tier 2 (MuJoCo) is implemented now; tier 3 (Drake) is not, so tier 3 is
    what this guards. When Drake lands, this test should start failing and be
    updated — that failure is the point.
    """
    report = evaluate(simple_rover(), max_tier=3)
    assert report.tiers_run == [0, 1, 2]
    assert report.tiers_skipped == [3]


def test_requested_tiers_that_did_run_are_not_reported_skipped():
    report = evaluate(simple_rover(), max_tier=2)
    assert report.tiers_run == [0, 1, 2]
    # A tier that ran must never also appear as skipped, whatever else is.
    assert not set(report.tiers_run) & set(report.tiers_skipped)
