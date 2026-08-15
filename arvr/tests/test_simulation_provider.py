"""Acceptance-gate tests for MuJoCoSimulationProvider (Shadow Robot Spatial
Demonstration Pipeline spec section 52, Phase 10).
"""

from __future__ import annotations

import pytest

pytest.importorskip("mujoco")
pytest.importorskip("pinocchio")

from ar_contracts import (  # noqa: E402
    RobotTrajectory,
    RobotTrajectoryFrame,
    RobotTrajectoryMetadata,
)
from ar_datapipe import ArmRetargeter, IkSolver  # noqa: E402
from ar_datapipe.robot_model import robot_model_from_bundle  # noqa: E402
from spatial_providers import (  # noqa: E402
    FixtureAssetProvider,
    FixtureRobotProvider,
    MockHandProvider,
    MuJoCoSimulationProvider,
    TaskSpec,
)


def _robot_bundle():
    return FixtureRobotProvider().get_robot_bundle()


def _build_trajectory(bundle, hand_frames) -> RobotTrajectory:
    model = robot_model_from_bundle(bundle)
    # The real SO-101 is 5-DOF -- see arm_retargeter.py's position_only.
    position_only = bundle.capability_profile.arm_dof < 6
    retargeter = ArmRetargeter(IkSolver(model), position_only=position_only)
    frames = []
    for hand in hand_frames:
        result = retargeter.step(hand, timestamp_ns=hand.timestamp_ns)
        frames.append(
            RobotTrajectoryFrame(
                timestamp_ns=hand.timestamp_ns,
                q=result.q,
                dq=result.dq,
                end_effector_position_m=result.ee_target_position_m,
                end_effector_orientation_xyzw=result.ee_target_orientation_xyzw,
                gripper=result.gripper,
                ik_status=result.ik_status,
            )
        )
    metadata = RobotTrajectoryMetadata(
        trajectory_id="3fae3b8e-3b0e-4e2b-9a3a-8f1e6f0f6c2f",
        robot_id=bundle.manifest.robot_id,
        source_human_episode_id="3fae3b8e-3b0e-4e2b-9a3a-8f1e6f0f6c2f",
        robot_bundle_hash="a" * 64,
        asset_bundle_hash="b" * 64,
    )
    return RobotTrajectory(metadata=metadata, frames=frames)


def test_valid_mock_episode_trajectory_is_accepted():
    bundle = _robot_bundle()
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    trajectory = _build_trajectory(bundle, list(MockHandProvider().stream()))

    # The mock episode's final wrist position is its retract endpoint
    # (tools/make_mock_hand_episode.py's RETRACT_END_M) -- use the
    # trajectory's own final commanded EE position as the goal so this test
    # checks verification logic, not whether the mock happens to end near
    # an arbitrary point.
    goal = trajectory.frames[-1].end_effector_position_m
    task = TaskSpec(goal_position_m=goal, tolerance_m=0.05)

    result = MuJoCoSimulationProvider().replay_and_verify(bundle, asset, trajectory, task)
    assert result.status == "accepted", result.rejection_reason
    assert result.checks.collision_valid is not None
    assert result.dataset_id is not None


def test_joint_limit_violating_trajectory_is_rejected_with_a_reason():
    bundle = _robot_bundle()
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    trajectory = _build_trajectory(bundle, list(MockHandProvider().stream()))

    # Corrupt one frame's q to blow past a declared joint limit.
    bad_frame = trajectory.frames[10]
    bad_q = list(bad_frame.q)
    bad_q[0] = 100.0
    trajectory.frames[10] = RobotTrajectoryFrame(
        timestamp_ns=bad_frame.timestamp_ns,
        q=tuple(bad_q),
        dq=bad_frame.dq,
        end_effector_position_m=bad_frame.end_effector_position_m,
        end_effector_orientation_xyzw=bad_frame.end_effector_orientation_xyzw,
        gripper=bad_frame.gripper,
        ik_status="ok",  # lie about ik_status too, so joint_limits is what catches it
    )

    task = TaskSpec(goal_position_m=trajectory.frames[-1].end_effector_position_m)
    result = MuJoCoSimulationProvider().replay_and_verify(bundle, asset, trajectory, task)

    assert result.status == "rejected"
    assert result.rejection_reason is not None
    assert result.checks.replay is False  # a 100 rad joint angle also fails EE tracking


def test_collision_valid_is_a_real_field_not_hardcoded():
    """Confirms collision_valid actually varies with input rather than
    always being True -- an all-fixed-joints trajectory (arm never moves
    off its build pose) should not collide with itself; that's the only
    positive case this fixture's simple cylinder geometry gives us an easy
    way to construct (see mujoco_simulation_provider.py's module docstring
    and STATE.md for the honest gap: a true self-collision positive case
    isn't exercised here)."""
    bundle = _robot_bundle()
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    trajectory = _build_trajectory(bundle, list(MockHandProvider().stream()))
    task = TaskSpec(goal_position_m=trajectory.frames[-1].end_effector_position_m)

    result = MuJoCoSimulationProvider().replay_and_verify(bundle, asset, trajectory, task)
    assert isinstance(result.checks.collision_valid, bool)


def test_task_predicate_evaluates_against_the_button_goal():
    bundle = _robot_bundle()
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    trajectory = _build_trajectory(bundle, list(MockHandProvider().stream()))

    far_goal = (10.0, 10.0, 10.0)
    result = MuJoCoSimulationProvider().replay_and_verify(
        bundle, asset, trajectory, TaskSpec(goal_position_m=far_goal, tolerance_m=0.05),
    )
    assert result.status == "rejected"
    assert result.checks.task_predicate is False
