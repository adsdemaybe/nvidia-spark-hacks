"""Ties normalize -> retarget -> verify -> export into one call — spec
section 42 (TEACH Pipeline) and the Phase 5/6 acceptance gates (sections
62-63). This is the thing `SpatialEpisode` + its recorded `SpatialFrame`s
turn into: a `VerificationResult`, with a LeRobot-compatible export as a
side effect only on acceptance.

Rejected demonstrations always carry a measurable reason (rule 85.10) — the
reason string here always names which concrete check(s) failed, never a
vague "verification failed".
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from ar_contracts import (
    PositionM,
    SpatialEpisode,
    SpatialFrame,
    VerificationChecks,
    VerificationResult,
)

from .export import export_episode
from .retarget import IkSolver, RetargetResult
from .robot_model import DEFAULT_MODEL, RobotModel
from .verify import MujocoReplay, ReplayResult

ROBOT_TYPE = "struct_test_arm"  # matches fixtures/robot/test_arm.urdf <robot name=...>
DEFAULT_GOAL_TOLERANCE_M = 0.05


def _velocity_ok(
    frames: list[SpatialFrame],
    retargets: list[RetargetResult],
    velocity_limits: np.ndarray,
) -> bool:
    """spec section 62: "velocity limits valid... no unexplained
    discontinuity". A joint solution that jumps between two consecutive
    demo frames faster than the robot's declared velocity limit allows is
    exactly that discontinuity — whether it came from a genuinely fast
    human motion or an IK solver landing on a different local solution,
    the robot cannot reproduce it, so it must not pass silently."""
    for prev, curr, prev_r, curr_r in zip(
        frames, frames[1:], retargets, retargets[1:], strict=False
    ):
        dt = (curr.timestamp_ns - prev.timestamp_ns) / 1e9
        if dt <= 0:
            continue
        raw_delta = np.array(curr_r.joint_positions) - np.array(prev_r.joint_positions)
        # retarget.py wraps each solution into (-pi, pi]; a legitimate small
        # motion that crosses that boundary (e.g. 3.10 -> -3.10) would
        # otherwise look like a ~6.2 rad jump. Wrap the delta itself so
        # crossing the seam isn't mistaken for a discontinuity.
        wrapped_delta = np.abs((raw_delta + np.pi) % (2 * np.pi) - np.pi)
        if np.any(wrapped_delta / dt > velocity_limits):
            return False
    return True


def _frame_index_of_last_event(
    episode: SpatialEpisode, frames: list[SpatialFrame], event_type: str
) -> int:
    matching = [e for e in episode.events if e.type == event_type]
    if not matching:
        return len(frames) - 1
    target_ts = matching[-1].timestamp_ns
    # frames are recorded in timestamp order (spec: timestamp_ns monotonic);
    # pick the first frame at/after the event, matching "the pose the arm
    # was commanded to when RELEASE fired", not before it.
    for i, frame in enumerate(frames):
        if frame.timestamp_ns >= target_ts:
            return i
    return len(frames) - 1


def run_episode(
    episode: SpatialEpisode,
    frames: list[SpatialFrame],
    *,
    goal_position_m: PositionM,
    dataset_root: Path,
    goal_tolerance_m: float = DEFAULT_GOAL_TOLERANCE_M,
    robot: RobotModel = DEFAULT_MODEL,
) -> VerificationResult:
    if episode.coordinate_frame != "struct_world":
        raise ValueError(
            f"normalize: episode.coordinate_frame must be struct_world, "
            f"got {episode.coordinate_frame!r} (device adapters must convert "
            f"before frames reach the datapipe, spec section 5)"
        )
    if not frames:
        raise ValueError("episode has no frames")

    solver = IkSolver(robot)
    replayer = MujocoReplay(robot)

    retargets: list[RetargetResult] = []
    replays: list[ReplayResult] = []
    q_warm_start = None
    for frame in frames:
        result = solver.solve(frame.position_m, frame.orientation_xyzw, q_init=q_warm_start)
        retargets.append(result)
        q_warm_start = np.array(result.joint_positions)

        replay = replayer.replay_pose(
            result.joint_names, result.joint_positions, frame.position_m
        )
        replays.append(replay)

    ik_ok = all(r.converged for r in retargets)
    limits_ok = all(r.within_limits for r in retargets)
    velocity_ok = _velocity_ok(frames, retargets, solver.velocity_limits)
    replay_ok = all(r.within_tolerance for r in replays)
    max_tracking_error = max((r.tracking_error_m for r in replays), default=0.0)

    release_idx = _frame_index_of_last_event(episode, frames, "RELEASE")
    achieved_at_release = replays[release_idx].achieved_position_m
    task_error = float(
        np.linalg.norm(np.array(achieved_at_release) - np.array(goal_position_m))
    )
    task_ok = task_error <= goal_tolerance_m

    checks = VerificationChecks(
        ik=ik_ok,
        joint_limits=limits_ok,
        velocity=velocity_ok,
        replay=replay_ok,
        task_predicate=task_ok,
    )

    if ik_ok and limits_ok and velocity_ok and replay_ok and task_ok:
        timestamps_s = [(f.timestamp_ns - frames[0].timestamp_ns) / 1e9 for f in frames]
        if len(frames) > 1:
            avg_dt = (timestamps_s[-1] - timestamps_s[0]) / (len(frames) - 1)
            fps = round(1.0 / avg_dt, 2)
        else:
            fps = 0.0
        dataset_dir = dataset_root / episode.task_id
        dataset_id = export_episode(
            dataset_dir,
            episode_index=0,
            task=episode.task_id,
            robot_type=ROBOT_TYPE,
            fps=fps,
            timestamps_s=timestamps_s,
            actions=[r.joint_positions for r in retargets],
            gripper=[f.gripper for f in frames],
        )
        return VerificationResult(
            episode_id=episode.episode_id,
            status="accepted",
            checks=checks,
            tracking_error_m=max_tracking_error,
            task_success=True,
            dataset_id=dataset_id,
        )

    failed = [
        name
        for name, ok in (
            ("ik", ik_ok),
            ("joint_limits", limits_ok),
            ("velocity", velocity_ok),
            ("replay", replay_ok),
            (f"task_predicate (error {task_error:.4f}m > tol {goal_tolerance_m}m)", task_ok),
        )
        if not ok
    ]
    return VerificationResult(
        episode_id=episode.episode_id,
        status="rejected",
        checks=checks,
        tracking_error_m=max_tracking_error,
        task_success=False,
        rejection_reason=f"failed checks: {', '.join(failed)}",
    )
