"""MuJoCoSimulationProvider — the independent-development verifier (spec
section 32). Composes ar_datapipe's existing `MujocoReplay`/`RobotModel`
unmodified -- `ar_datapipe/verify.py` itself is not touched by this
package; this is a wrapper behind the `SimulationProvider` interface, not a
fork.

Deferred import (see the try/except in __init__): MuJoCo is Linux-only, so
`import spatial_providers` must keep succeeding on Windows even though
constructing this specific provider there will raise.
"""

from __future__ import annotations

import math

import numpy as np
from ar_contracts import (
    InteractableAsset,
    RobotBundle,
    RobotTrajectory,
    VerificationChecks,
    VerificationResult,
)

from .simulation_provider import SimulationProvider, TaskSpec


class MuJoCoSimulationProvider(SimulationProvider):
    def __init__(self) -> None:
        try:
            import ar_datapipe  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "ar_datapipe (and its MuJoCo dependency) is not importable "
                "on this platform (Linux only). spatial_providers itself "
                "imports fine everywhere; only this provider needs it."
            ) from exc

    def replay_and_verify(
        self,
        robot_bundle: RobotBundle,
        asset_bundle: InteractableAsset,
        trajectory: RobotTrajectory,
        task: TaskSpec,
    ) -> VerificationResult:
        from ar_datapipe import MujocoReplay, RobotModel

        del asset_bundle  # not needed for Milestone 1's checks; kept in the
        # interface because spec section 31's SimulationProvider takes it --
        # a future collision check against the asset's own geometry, not
        # just the robot's self/environment contacts, is the natural use.

        model = RobotModel(
            urdf_path=robot_bundle.urdf_path,
            end_effector_frame=robot_bundle.manifest.end_effectors[0],
        )
        replayer = MujocoReplay(model)
        articulated_joints = [j for j in robot_bundle.robot_ir.joints if j.type != "fixed"]
        joint_names = tuple(j.name for j in articulated_joints)
        velocity_limits = np.array([j.velocity_limit or math.inf for j in articulated_joints])

        ik_ok = all(f.ik_status != "failed" for f in trajectory.frames)
        limits_ok = all(f.ik_status != "joint_limit" for f in trajectory.frames)

        # One replay_pose() call per frame -- it already runs mj_forward,
        # so MuJoCo's contact state (data.ncon) is current for that frame
        # right after the call, no separate collision pass needed.
        replay_results = []
        collision_ok = True
        for frame in trajectory.frames:
            replay = replayer.replay_pose(joint_names, frame.q, frame.end_effector_position_m)
            replay_results.append(replay)
            if replayer.data.ncon > 0:
                collision_ok = False

        replay_ok = all(r.within_tolerance for r in replay_results)
        max_tracking_error = max((r.tracking_error_m for r in replay_results), default=0.0)

        velocity_ok = all(
            bool(np.all(np.abs(np.array(f.dq)) <= velocity_limits + 1e-9))
            for f in trajectory.frames
        )

        goal = np.array(task.goal_position_m)
        final_position = (
            np.array(replay_results[-1].achieved_position_m) if replay_results else goal
        )
        task_error = float(np.linalg.norm(final_position - goal))
        task_ok = task_error <= task.tolerance_m

        checks = VerificationChecks(
            ik=ik_ok,
            joint_limits=limits_ok,
            velocity=velocity_ok,
            replay=replay_ok,
            task_predicate=task_ok,
            collision_valid=collision_ok,
        )

        all_ok = ik_ok and limits_ok and velocity_ok and replay_ok and task_ok and collision_ok
        if all_ok:
            # A provisional id, not the canonical LeRobot dataset location --
            # this provider only verifies, it doesn't export (spec section
            # 31's interface has no dataset_root). VerificationResult
            # requires a non-null dataset_id on accept, so this satisfies
            # that; ar_datapipe.spatial_pipeline's orchestrator overwrites
            # RobotEpisodeMetadata.dataset_id with the real export path
            # once export actually runs.
            return VerificationResult(
                episode_id=trajectory.metadata.trajectory_id,
                status="accepted",
                checks=checks,
                tracking_error_m=max_tracking_error,
                task_success=True,
                dataset_id=trajectory.metadata.trajectory_id,
            )

        failed = [
            name
            for name, ok in (
                ("ik", ik_ok),
                ("joint_limits", limits_ok),
                ("velocity", velocity_ok),
                ("replay", replay_ok),
                ("collision", collision_ok),
                (
                    f"task_predicate (error {task_error:.4f}m > tol {task.tolerance_m}m)",
                    task_ok,
                ),
            )
            if not ok
        ]
        return VerificationResult(
            episode_id=trajectory.metadata.trajectory_id,
            status="rejected",
            checks=checks,
            tracking_error_m=max_tracking_error,
            task_success=False,
            rejection_reason=f"failed checks: {', '.join(failed)}",
        )
