"""§3: two implementations of one number, and what a disagreement means.

The distinction under test is the one that is easy to lose: a disagreement
between the CAD mass model and the compiled simulation model is a **pipeline
bug**, not a design finding. Reporting it as a failing criterion sends a design
agent off to lighten a chassis that was never the problem — and it will appear
to succeed, because thinning a wall moves both numbers.
"""

from __future__ import annotations

import pytest

from engine.crosscheck import (
    COM_TOLERANCE_M,
    MASS_TOLERANCE,
    cad_mass_and_com,
    against_simulation,
    simulated_mass_and_com,
)
from engine.evaluate import compute_mass_properties, evaluate
from engine.examples import simple_rover

mujoco = pytest.importorskip("mujoco")


def _model(ir):
    from engine.sim.mujoco_harness import compile_floating

    return compile_floating(ir)


def test_the_two_implementations_actually_agree_on_a_real_design():
    """The check is only worth anything if it is genuinely two paths.

    CAD integrates B-rep solids in OpenCascade and walks the kinematic tree;
    MuJoCo compiles URDF into MJCF, applies its own frame conventions, and
    reports the assembled bodies. They land on the same numbers to floating
    point, which is what makes a disagreement meaningful when one appears.
    """
    ir = simple_rover()
    mass_props = compute_mass_properties(ir)

    cad_mass, cad_com = cad_mass_and_com(ir, mass_props)
    sim_mass, sim_com = simulated_mass_and_com(_model(ir))

    assert cad_mass == pytest.approx(sim_mass, rel=1e-6)
    assert cad_com == pytest.approx(sim_com, abs=1e-6)
    assert against_simulation(ir, mass_props) == []


def test_a_mass_disagreement_is_filed_as_a_pipeline_bug_not_a_criterion():
    ir = simple_rover()
    mass_props = compute_mass_properties(ir)

    # Simulate the translation defect: a link's inertial dropped on the way to
    # MJCF, so the compiled model is light. Doctoring the CAD side is the only
    # way to stage it without breaking the exporter on purpose.
    heaviest = max(mass_props, key=lambda k: mass_props[k].mass)
    inflated = dict(mass_props)
    inflated[heaviest] = mass_props[heaviest].model_copy(
        update={"mass": mass_props[heaviest].mass * 1.5}
    )

    bugs = against_simulation(ir, inflated, _model(ir))
    assert bugs, "a 50% mass disagreement has to be reported"
    mass_bug = next(b for b in bugs if b.quantity == "total_mass")
    assert mass_bug.subsystem == "cad->mjcf"
    assert "not a design one" in mass_bug.detail
    assert "Do not lighten the design" in mass_bug.detail
    assert mass_bug.disagreement > MASS_TOLERANCE * mass_bug.measured_a


def test_a_com_disagreement_names_the_likely_cause():
    ir = simple_rover()
    mass_props = compute_mass_properties(ir)

    # A frame convention error: one link's CoM shifted 20 mm.
    shifted = dict(mass_props)
    link = "wheel_L"
    original = mass_props[link]
    shifted[link] = original.model_copy(
        update={"com": original.com.model_copy(update={"x": original.com.x + 0.5})}
    )

    bugs = against_simulation(ir, shifted, _model(ir))
    com_bug = next(b for b in bugs if b.quantity == "centre_of_mass")
    assert com_bug.measured_b > COM_TOLERANCE_M
    assert "frame convention" in com_bug.detail


def test_a_pipeline_bug_blocks_the_verdict_without_becoming_a_failing_criterion():
    """BLOCKED is the state `passed` cannot express: every criterion passed, and
    the harness that said so is known to be internally inconsistent."""
    from engine.crosscheck import PipelineBug
    from engine.evaluate import EvaluationReport

    report = EvaluationReport(
        design_id="x",
        design_name="x",
        results=[r for r in evaluate(simple_rover(), max_tier=0).results],
        tiers_run=[0],
        tiers_skipped=[],
        pipeline_bugs=[
            PipelineBug(
                subsystem="cad->mjcf", quantity="total_mass", detail="staged",
                measured_a=2.4, measured_b=1.9, tolerance=0.024, unit="kg",
            )
        ],
    )
    assert report.passed, "the criteria themselves are fine"
    assert report.verdict == "BLOCKED"
    assert not report.failures


def test_a_fixed_base_robot_is_not_cross_checked_for_mass():
    """MuJoCo welds a URDF root to the world and drops its mass, so the two
    totals are *supposed* to differ. Comparing anyway would file a bug on every
    bench arm forever; `sim_loads` already checks the welded case correctly."""
    ir = simple_rover()
    fixed = ir.model_copy(update={"base": "fixed"})
    assert against_simulation(fixed, compute_mass_properties(fixed)) == []
