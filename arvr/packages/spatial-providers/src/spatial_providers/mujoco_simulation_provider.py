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
        from ar_datapipe import IkSolver, MujocoReplay
        from ar_datapipe.robot_model import robot_model_from_bundle

        del asset_bundle  # not needed for Milestone 1's checks; kept in the
        # interface because spec section 31's SimulationProvider takes it --
        # a future collision check against the asset's own geometry, not
        # just the robot's self/environment contacts, is the natural use.

        model = robot_model_from_bundle(robot_bundle)
        replayer = MujocoReplay(model)
        # RobotTrajectoryFrame.q is populated in ArmRetargeter/IkSolver's
        # order (Pinocchio's kinematic joint order), NOT robot_ir.json's own
        # array order -- those only coincidentally matched for the old
        # hand-authored placeholder (deliberately written base-to-tip). The
        # real SO-101's robot_ir.json, parsed straight from the vendored
        # URDF, preserves the URDF's own (tip-to-base) joint order instead;
        # zipping that mismatched order against `frame.q` here silently
        # replayed the wrong q value under the wrong joint name. Read the
        # real order from the same solver ArmRetargeter used, not a second,
        # independently-derived list.
        joint_names = IkSolver(model).joint_names
        joint_limits = {j.name: j.velocity_limit for j in robot_bundle.robot_ir.joints}
        velocity_limits = np.array([joint_limits.get(name) or math.inf for name in joint_names])

        ik_ok = all(f.ik_status != "failed" for f in trajectory.frames)
        limits_ok = all(f.ik_status != "joint_limit" for f in trajectory.frames)

        # The real SO-101's own vendored collision meshes (adjacent motor
        # housings, mounting plates) touch by design at rest -- confirmed
        # directly, MuJoCo reports real, non-zero contacts in the neutral
        # pose alone (the placeholder's simple primitive collision shapes
        # never touched, so "ncon > 0" happened to mean "genuine collision"
        # for it by coincidence). Raw contact *count* isn't a reliable
        # signal either: the same resting pair can register as 4 or 9
        # contacts depending on exact mesh-triangle alignment at a given
        # joint angle, confirmed directly -- same handful of penetration
        # *depths* (e.g. -0.0264, -0.0224) repeated, not new, deeper ones.
        # Penetration depth is the physically meaningful quantity: baseline
        # it against the neutral pose, and only flag a *new* contact
        # noticeably deeper than anything already there at rest.
        COLLISION_DEPTH_MARGIN_M = 0.005

        def _max_penetration_m() -> float:
            ncon = replayer.data.ncon
            if ncon == 0:
                return 0.0
            return max(abs(float(replayer.data.contact[i].dist)) for i in range(ncon))

        neutral_q = tuple(0.0 for _ in joint_names)
        neutral_goal = trajectory.frames[0].end_effector_position_m
        replayer.replay_pose(joint_names, neutral_q, neutral_goal)
        baseline_max_penetration_m = _max_penetration_m()

        # One replay_pose() call per frame -- it already runs mj_forward,
        # so MuJoCo's contact state (data.ncon/data.contact) is current for
        # that frame right after the call, no separate collision pass needed.
        replay_results = []
        collision_ok = True
        for frame in trajectory.frames:
            replay = replayer.replay_pose(joint_names, frame.q, frame.end_effector_position_m)
            replay_results.append(replay)
            if _max_penetration_m() > baseline_max_penetration_m + COLLISION_DEPTH_MARGIN_M:
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
