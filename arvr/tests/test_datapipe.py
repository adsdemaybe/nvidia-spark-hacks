"""Acceptance-gate tests for ar_datapipe — spec sections 62 (Retarget) and
63 (Verification). Uses the placeholder test arm (fixtures/robot/) since no
real robot URDF exists yet — see that directory's README.

Run only where Pinocchio/MuJoCo actually import (they don't have Windows
wheels for one transitive dep as of writing; verified in WSL/Linux — see
STATE.md). Skips cleanly elsewhere instead of failing collection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pinocchio")
pytest.importorskip("mujoco")

from ar_contracts import SpatialEpisode, SpatialFrame  # noqa: E402
from ar_datapipe import IkSolver, MujocoReplay, run_episode  # noqa: E402
from ar_datapipe.verify import TRACKING_ERROR_TOL_M  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ar-xr"


def _load_fixture_episode() -> tuple[SpatialEpisode, list[SpatialFrame]]:
    episode = SpatialEpisode.model_validate(
        json.loads((FIXTURES_DIR / "sample_episode.json").read_text())
    )
    frames = [
        SpatialFrame.model_validate(json.loads(line))
        for line in (FIXTURES_DIR / "sample_episode.jsonl").read_text().splitlines()
    ]
    return episode, frames


# ---------------------------------------------------------------------------
# spec section 62 — retarget: IK succeeds, joint limits valid, finite, no NaN
# ---------------------------------------------------------------------------


def test_ik_converges_for_a_reachable_pose():
    solver = IkSolver()
    result = solver.solve((0.5, 0.1, 0.4), (0.0, 0.0, 0.0, 1.0))
    assert result.converged
    assert result.within_limits
    assert all(x == x for x in result.joint_positions)  # no NaN


def test_ik_reports_unreachable_target_as_not_converged():
    solver = IkSolver()
    # Max reach is ~0.95m — this is unambiguously outside the workspace.
    result = solver.solve((5.0, 5.0, 5.0), (0.0, 0.0, 0.0, 1.0))
    assert not result.converged


def test_ik_rejects_non_finite_target():
    solver = IkSolver()
    with pytest.raises(ValueError):
        solver.solve((float("nan"), 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def test_ik_solution_stays_within_declared_joint_limits_when_converged():
    solver = IkSolver()
    result = solver.solve((0.3, -0.2, 0.5), (0.0, 0.0, 0.0, 1.0))
    assert result.converged
    for value, lo, hi in zip(
        result.joint_positions, solver.lower_limits, solver.upper_limits, strict=True
    ):
        assert lo <= value <= hi


# ---------------------------------------------------------------------------
# spec section 63 — verification: MuJoCo replay cross-checks Pinocchio's IK
# ---------------------------------------------------------------------------


def test_mujoco_replay_agrees_with_pinocchio_ik_within_tolerance():
    solver = IkSolver()
    replay = MujocoReplay()
    target = (0.45, -0.1, 0.5)
    result = solver.solve(target, (0.0, 0.0, 0.0, 1.0))
    assert result.converged

    replayed = replay.replay_pose(result.joint_names, result.joint_positions, target)
    assert replayed.tracking_error_m < TRACKING_ERROR_TOL_M
    assert replayed.within_tolerance


def test_replay_rejects_non_finite_joint_value():
    replay = MujocoReplay()
    solver = IkSolver()
    with pytest.raises(ValueError):
        replay.replay_pose(
            solver.joint_names,
            (float("nan"),) * len(solver.joint_names),
            (0.0, 0.0, 0.0),
        )


# ---------------------------------------------------------------------------
# full pipeline (spec section 42 TEACH pipeline / section 63 acceptance gate)
# ---------------------------------------------------------------------------


def test_pipeline_rejects_episode_not_in_struct_world():
    episode, frames = _load_fixture_episode()
    bad_episode = episode.model_copy(update={"coordinate_frame": "device_frame"})
    with pytest.raises(ValueError, match="struct_world"):
        run_episode(
            bad_episode, frames, goal_position_m=(0.6, 0.3, 0.55), dataset_root=Path("/tmp")
        )


def test_pipeline_accepts_the_fixture_teach_episode(tmp_path):
    episode, frames = _load_fixture_episode()
    result = run_episode(
        episode,
        frames,
        goal_position_m=(0.60, 0.30, 0.55),  # matches the fixture's RELEASE waypoint
        dataset_root=tmp_path,
    )
    assert result.status == "accepted"
    assert result.checks.ik
    assert result.checks.joint_limits
    assert result.checks.replay
    assert result.checks.task_predicate
    assert result.dataset_id is not None
    assert result.tracking_error_m < TRACKING_ERROR_TOL_M

    # LeRobot-v3-shaped artifacts actually landed on disk (spec section 35).
    dataset_dir = tmp_path / "cube_to_bin"
    assert (dataset_dir / "meta" / "info.json").exists()
    assert (dataset_dir / "meta" / "episodes.jsonl").exists()
    assert (dataset_dir / "data" / "chunk-000" / "episode_000000.parquet").exists()


def test_pipeline_rejects_with_measurable_reason_when_goal_is_wrong():
    """spec section 63: rejected demos must return a measurable reason."""
    episode, frames = _load_fixture_episode()
    result = run_episode(
        episode,
        frames,
        # A goal far from where the demo actually released the object —
        # IK/limits/replay all still pass, only task_predicate should fail.
        goal_position_m=(5.0, 5.0, 5.0),
        dataset_root=Path("/tmp/should_not_be_written"),
    )
    assert result.status == "rejected"
    assert result.checks.ik
    assert result.checks.joint_limits
    assert result.checks.replay
    assert not result.checks.task_predicate
    assert result.rejection_reason is not None
    assert "task_predicate" in result.rejection_reason
    assert result.dataset_id is None
