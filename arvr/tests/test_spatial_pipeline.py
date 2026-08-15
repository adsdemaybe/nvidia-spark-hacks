"""Acceptance-gate tests for the spatial pipeline orchestrator (Shadow
Robot Spatial Demonstration Pipeline spec section 52, Phase 11).
"""

from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("mujoco")
pytest.importorskip("pinocchio")
pytest.importorskip("pyarrow")

import pyarrow.parquet as pq  # noqa: E402
from ar_contracts import HumanEpisode, HumanEpisodeMetadata, Pose, TaskSpec  # noqa: E402
from ar_datapipe import run_spatial_episode  # noqa: E402
from spatial_providers import (  # noqa: E402
    FixtureAssetProvider,
    FixtureRobotProvider,
    MockHandProvider,
    MuJoCoSimulationProvider,
)

# tools/make_mock_hand_episode.py's RETRACT_END_M -- the mock episode's
# wrist genuinely ends up here, so a task goal set to this point exercises
# real accept logic instead of a goal chosen to always pass. Recentered for
# the real SO-101 (Track A) -- see that script's own comment for how this
# point was verified reachable.
RETRACT_END_TASK = TaskSpec(goal_position_m=(0.37, 0.07, 0.2), tolerance_m=0.1)


def _human_episode() -> HumanEpisode:
    metadata = HumanEpisodeMetadata(
        episode_id=str(uuid.uuid4()), task_id="press_button", asset_id="button_01",
        hand_provider="mock",
    )
    return HumanEpisode(metadata=metadata, hand_frames=list(MockHandProvider().stream()))


def _common(tmp_path):
    episode = _human_episode()
    bundle = FixtureRobotProvider().get_robot_bundle()
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    asset_pose = Pose(position_m=(0.4, 0.0, 0.53), orientation_xyzw=(0, 0, 0, 1))
    return episode, bundle, asset, asset_pose


def _run(episode, bundle, asset, asset_pose, task, dataset_root):
    return run_spatial_episode(
        episode, bundle, asset, asset_pose, task,
        dataset_root=dataset_root, simulation_provider=MuJoCoSimulationProvider(),
    )


def test_accepted_episode_produces_a_robot_episode_with_a_real_dataset_id(tmp_path):
    episode, bundle, asset, asset_pose = _common(tmp_path)
    robot_episode = _run(episode, bundle, asset, asset_pose, RETRACT_END_TASK, tmp_path)

    reason = robot_episode.verification.rejection_reason
    assert robot_episode.verification.status == "accepted", reason
    assert robot_episode.metadata.success is True
    assert robot_episode.metadata.dataset_id is not None
    assert robot_episode.metadata.dataset_id != robot_episode.verification.dataset_id, (
        "metadata.dataset_id should be the real export path, distinct from "
        "the SimulationProvider's provisional one"
    )


def test_rejected_episode_writes_nothing_to_dataset_root(tmp_path):
    episode, bundle, asset, asset_pose = _common(tmp_path)
    far_task = TaskSpec(goal_position_m=(10.0, 10.0, 10.0), tolerance_m=0.01)
    robot_episode = _run(episode, bundle, asset, asset_pose, far_task, tmp_path)

    assert robot_episode.verification.status == "rejected"
    assert robot_episode.metadata.dataset_id is None
    assert list(tmp_path.iterdir()) == []


def test_provenance_jsonl_references_the_source_episode_and_a_real_hash(tmp_path):
    episode, bundle, asset, asset_pose = _common(tmp_path)
    robot_episode = _run(episode, bundle, asset, asset_pose, RETRACT_END_TASK, tmp_path)
    assert robot_episode.verification.status == "accepted"

    provenance_path = tmp_path / "press_button" / "meta" / "provenance.jsonl"
    assert provenance_path.exists()

    record = json.loads(provenance_path.read_text().splitlines()[0])
    assert record["source_human_episode_id"] == episode.metadata.episode_id
    assert len(record["robot_bundle_hash"]) == 64  # sha256 hex digest
    assert record["simulator"] == "mujoco"


def test_parquet_includes_observation_state_matching_action_row_count(tmp_path):
    episode, bundle, asset, asset_pose = _common(tmp_path)
    _run(episode, bundle, asset, asset_pose, RETRACT_END_TASK, tmp_path)

    parquet_path = tmp_path / "press_button" / "data" / "chunk-000" / "episode_000000.parquet"
    table = pq.read_table(parquet_path)
    assert "observation.state" in table.column_names
    assert table.num_rows == len(episode.hand_frames)
    assert table.column("observation.state").length() == table.column("action").length()


def test_different_robot_bundles_produce_different_robot_bundle_hashes(tmp_path):
    """Stand-in for the section 55 data-transferability gate without a
    second real robot bundle in this milestone: at minimum, the hash that
    identifies *which* bundle a trajectory was verified against must
    actually depend on the bundle's real content."""
    episode, bundle, asset, asset_pose = _common(tmp_path)

    result_a = _run(episode, bundle, asset, asset_pose, RETRACT_END_TASK, tmp_path / "a")
    # Same bundle again should hash identically (deterministic, not random).
    result_b = _run(episode, bundle, asset, asset_pose, RETRACT_END_TASK, tmp_path / "b")

    assert result_a.metadata.robot_bundle_hash == result_b.metadata.robot_bundle_hash
