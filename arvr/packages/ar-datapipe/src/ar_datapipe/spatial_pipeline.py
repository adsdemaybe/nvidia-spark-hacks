"""Spatial pipeline orchestrator — Shadow Robot Spatial Demonstration
Pipeline spec section 52, Phase 11. Ties HumanEpisode -> InteractionIR ->
ArmRetargeter -> SimulationProvider -> RobotEpisode -> LeRobot into one
call, parallel to (and never calling or modifying) `pipeline.run_episode`,
the old TEACH pipeline's entry point.

A rejected VerificationResult never triggers export (spec section 34:
"a failed demonstration never silently enters training data") -- the
returned RobotEpisode still carries the full VerificationResult either way,
so the caller always knows *why*, per rule 85.10.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from ar_contracts import (
    HumanEpisode,
    InteractableAsset,
    Pose,
    RobotBundle,
    RobotEpisode,
    RobotEpisodeMetadata,
    RobotTrajectory,
    RobotTrajectoryFrame,
    RobotTrajectoryMetadata,
    SimulationProvider,
    TaskSpec,
)

from .arm_retargeter import ArmRetargeter
from .export import export_robot_episode
from .interaction_ir import derive_interaction_ir
from .retarget import IkSolver
from .robot_model import RobotModel

ROBOT_TYPE = "fixture_so101"
RETARGETER_VERSION = "arm_retargeter@1"
TASK_VERSION = "1"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _human_episode_hash(human_episode: HumanEpisode) -> str:
    payload = [f.model_dump(mode="json") for f in human_episode.hand_frames]
    return _sha256_hex(json.dumps(payload, sort_keys=True).encode())


def run_spatial_episode(
    human_episode: HumanEpisode,
    robot_bundle: RobotBundle,
    asset_bundle: InteractableAsset,
    asset_world_pose: Pose,
    task: TaskSpec,
    *,
    dataset_root: Path,
    simulation_provider: SimulationProvider,
) -> RobotEpisode:
    if not human_episode.hand_frames:
        raise ValueError("human_episode has no hand frames")

    # Computed as part of the real pipeline (proving the Phase 9 derivation
    # actually wires in, not just unit-tested in isolation) but not yet
    # consumed by retargeting itself -- ArmRetargeter operates directly on
    # each frame's world-space wrist pose (spec section 8's 1:1 mapping),
    # not on interaction_ir's object-relative data. Wiring interaction_ir
    # into retargeting is future work (relevant once a second robot
    # embodiment needs the same demo compiled differently); flagged in
    # STATE.md, not silently skipped.
    derive_interaction_ir(human_episode, asset_bundle, asset_world_pose)

    model = RobotModel(
        urdf_path=robot_bundle.urdf_path,
        end_effector_frame=robot_bundle.manifest.end_effectors[0],
    )
    retargeter = ArmRetargeter(IkSolver(model))

    frames: list[RobotTrajectoryFrame] = []
    for hand in human_episode.hand_frames:
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

    trajectory_id = str(uuid.uuid4())
    robot_bundle_hash = _sha256_hex(robot_bundle.urdf_path.read_bytes())
    asset_bundle_hash = _sha256_hex(asset_bundle.model_dump_json().encode())
    human_episode_id = human_episode.metadata.episode_id

    trajectory = RobotTrajectory(
        metadata=RobotTrajectoryMetadata(
            trajectory_id=trajectory_id,
            robot_id=robot_bundle.manifest.robot_id,
            source_human_episode_id=human_episode_id,
            robot_bundle_hash=robot_bundle_hash,
            asset_bundle_hash=asset_bundle_hash,
        ),
        frames=frames,
    )

    verification = simulation_provider.replay_and_verify(
        robot_bundle, asset_bundle, trajectory, task
    )

    # dataset_id isn't known until export actually runs (below), which only
    # happens on acceptance -- build metadata with it unset first (export
    # itself doesn't read dataset_id, only the provenance fields), then
    # attach the real export path afterward via model_copy (RobotEpisodeMetadata
    # is frozen). verification.dataset_id (set by the SimulationProvider,
    # required non-null on accept by VerificationResult's own contract) is
    # a provisional id satisfying that invariant -- it is NOT the canonical
    # LeRobot dataset location; this metadata.dataset_id is.
    metadata = RobotEpisodeMetadata(
        robot_id=robot_bundle.manifest.robot_id,
        robot_bundle_hash=robot_bundle_hash,
        source_human_episode_id=human_episode_id,
        human_episode_hash=_human_episode_hash(human_episode),
        task_id=human_episode.metadata.task_id,
        asset_ids=(asset_bundle.asset_id,),
        asset_bundle_hash=asset_bundle_hash,
        simulator="mujoco",
        retargeter_version=RETARGETER_VERSION,
        task_version=TASK_VERSION,
        created_at_ns=time.time_ns(),
        success=verification.status == "accepted",
        dataset_id=None,
    )

    if verification.status == "accepted":
        timestamps_s = [
            (f.timestamp_ns - frames[0].timestamp_ns) / 1e9 for f in frames
        ]
        if len(frames) > 1:
            avg_dt = (timestamps_s[-1] - timestamps_s[0]) / (len(frames) - 1)
            fps = round(1.0 / avg_dt, 2) if avg_dt > 0 else 0.0
        else:
            fps = 0.0
        dataset_dir = dataset_root / human_episode.metadata.task_id
        real_dataset_id = export_robot_episode(
            dataset_dir,
            episode_index=0,
            task=human_episode.metadata.task_id,
            robot_type=ROBOT_TYPE,
            fps=fps,
            timestamps_s=timestamps_s,
            actions=[f.q for f in frames],
            gripper=[f.gripper for f in frames],
            metadata=metadata,
        )
        metadata = metadata.model_copy(update={"dataset_id": real_dataset_id})

    return RobotEpisode(metadata=metadata, trajectory=trajectory, verification=verification)
