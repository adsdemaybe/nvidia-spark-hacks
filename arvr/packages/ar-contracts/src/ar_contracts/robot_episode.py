"""RobotEpisode — Shadow Robot Spatial Demonstration Pipeline spec sections
35, 67.

The embodiment-specific derived training record: one HumanEpisode compiled
onto one robot, verified in simulation. Every field spec section 67 asks a
verified episode to preserve is here, so training data stays reproducible —
which HumanEpisode it came from, which exact RobotBundle/AssetBundle it was
verified against (by content hash, not just id), which retargeter/simulator
version produced it.

Only ever constructed with `success=True` after a real `VerificationResult`
with `status="accepted"` — spec section 34: "a failed demonstration never
silently enters training data." `ar_datapipe.spatial_pipeline.run_spatial_episode`
enforces that by construction (it only calls the LeRobot exporter on accept).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .common import SCHEMA_VERSION, FrozenModel, SchemaVersion, TimestampNs
from .robot_trajectory import RobotTrajectory
from .verification_result import VerificationResult

Simulator = Literal["mujoco", "isaac"]


class RobotEpisodeMetadata(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    robot_id: str
    robot_bundle_hash: str
    source_human_episode_id: str
    human_episode_hash: str
    task_id: str
    asset_ids: tuple[str, ...]
    asset_bundle_hash: str
    simulator: Simulator
    retargeter_version: str
    task_version: str
    created_at_ns: TimestampNs
    success: bool
    dataset_id: str | None = None


@dataclass
class RobotEpisode:
    metadata: RobotEpisodeMetadata
    trajectory: RobotTrajectory
    verification: VerificationResult
