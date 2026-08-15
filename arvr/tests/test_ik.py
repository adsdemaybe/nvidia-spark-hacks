"""Acceptance gate for ar_sim's inverse kinematics — spec sections 13D, 62.

This is what turns a live waypoint into robot joint targets for the Twin
simulation (see ar-sim/README.md for how this differs from
ar_datapipe.retarget). The gate in 62 is unambiguous: IK succeeds, joint
limits hold, nothing is NaN, and an unreachable pose is reported rather
than approximated.

Ported from Andrew's independent `arxr-sim` package during the arvr/arxr
consolidation (see ../STATE.md); only import paths changed.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("mujoco")

from ar_sim.ik import IKResult, solve_ik  # noqa: E402
from ar_sim.twin import MujocoTwinSource  # noqa: E402

CUBE = (0.30, 0.10, 0.78)


@pytest.fixture
def sim() -> MujocoTwinSource:
    return MujocoTwinSource()


def test_reaches_a_point_on_the_table(sim: MujocoTwinSource):
    result = solve_ik(sim, CUBE)

    assert result.ok
    assert result.position_error_m < 0.01


def test_reports_the_joint_solution_it_found(sim: MujocoTwinSource):
    result = solve_ik(sim, CUBE)

    assert len(result.joint_positions) == sim.joint_count
    assert all(math.isfinite(q) for q in result.joint_positions)


def test_applying_the_solution_puts_the_tool_where_it_was_asked(sim: MujocoTwinSource):
    """The number the solver reports has to survive being written back into the
    model, or it is bookkeeping rather than a solution."""
    result = solve_ik(sim, CUBE)
    sim.set_joint_positions(result.joint_positions)

    reached = sim.end_effector_position()

    assert math.dist(reached, CUBE) < 0.01


def test_an_unreachable_pose_fails_rather_than_approximating(sim: MujocoTwinSource):
    far = (5.0, 5.0, 3.0)

    result = solve_ik(sim, far)

    assert not result.ok
    assert result.position_error_m > 0.5


def test_respects_joint_limits(sim: MujocoTwinSource):
    result = solve_ik(sim, CUBE)

    lower, upper = sim.joint_limits()
    for q, lo, hi in zip(result.joint_positions, lower, upper, strict=True):
        assert lo - 1e-6 <= q <= hi + 1e-6


def test_is_deterministic(sim: MujocoTwinSource):
    a = solve_ik(sim, CUBE)
    b = solve_ik(MujocoTwinSource(), CUBE)

    assert a.joint_positions == b.joint_positions


def test_never_returns_nan(sim: MujocoTwinSource):
    """A degenerate target (straight up, on the singular axis) must not produce
    NaN joints -- spec section 62 lists that explicitly."""
    result: IKResult = solve_ik(sim, (-0.5, 0.0, 1.4))

    assert all(math.isfinite(q) for q in result.joint_positions)
    assert math.isfinite(result.position_error_m)
