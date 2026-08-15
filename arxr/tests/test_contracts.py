"""Acceptance gate for the frozen spatial contracts (STRUCT_2.md 60).

The example payloads in this file are transcribed verbatim from STRUCT_2.md
29-34. They are the contract. If a change here is needed, the spec changes
first -- see CLAUDE.md > Conventions.
"""
from __future__ import annotations

import pytest
from arxr.core.schemas import (
    CorrectionEvent,
    SceneManifest,
    SpatialEpisode,
    SpatialFrame,
    TwinState,
)
from pydantic import ValidationError

# STRUCT_2.md 29
SPATIAL_FRAME_EXAMPLE = {
    "schema_version": "1.0",
    "timestamp_ns": 1700000000000000000,
    "source": {"device_type": "phone", "input_type": "tracked_controller"},
    "frame": "struct_world",
    "position_m": [0.31, 0.18, 0.42],
    "orientation_xyzw": [0.02, 0.71, 0.03, 0.70],
    "gripper": 1.0,
}


def test_spec_example_validates_as_spatial_frame():
    frame = SpatialFrame.model_validate(SPATIAL_FRAME_EXAMPLE)

    assert frame.timestamp_ns == 1700000000000000000
    assert frame.frame == "struct_world"
    assert frame.position_m == (0.31, 0.18, 0.42)
    assert frame.gripper == 1.0


def test_unknown_schema_version_is_rejected():
    payload = SPATIAL_FRAME_EXAMPLE | {"schema_version": "2.0"}

    with pytest.raises(ValidationError):
        SpatialFrame.model_validate(payload)


def test_non_unit_quaternion_is_rejected():
    """A quaternion that is not unit-norm is not a rotation. Letting one through
    silently corrupts every pose downstream of it (STRUCT_2.md 62)."""
    payload = SPATIAL_FRAME_EXAMPLE | {"orientation_xyzw": [1.0, 1.0, 0.0, 0.0]}

    with pytest.raises(ValidationError, match="unit"):
        SpatialFrame.model_validate(payload)


def test_nan_position_is_rejected():
    payload = SPATIAL_FRAME_EXAMPLE | {"position_m": [0.0, float("nan"), 0.0]}

    with pytest.raises(ValidationError, match="finite"):
        SpatialFrame.model_validate(payload)


def test_orientation_is_normalized_on_ingest():
    """Downstream IK and retargeting should never have to re-normalize. The spec
    examples are printed to 2 decimals (norm 0.9977), so this is not academic."""
    frame = SpatialFrame.model_validate(SPATIAL_FRAME_EXAMPLE)

    norm = sum(v * v for v in frame.orientation_xyzw) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-12)


def test_slightly_denormalized_quaternion_is_accepted():
    """Float error off the wire is normal; only genuinely wrong values fail."""
    payload = SPATIAL_FRAME_EXAMPLE | {"orientation_xyzw": [0.0, 0.0, 0.0, 1.0 - 1e-7]}

    assert SpatialFrame.model_validate(payload).orientation_xyzw[3] == pytest.approx(1.0)


# STRUCT_2.md 30
SPATIAL_EPISODE_EXAMPLE = {
    "schema_version": "1.0",
    "episode_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    "task_id": "cube_to_bin",
    "source": {"device_type": "phone"},
    "coordinate_frame": "struct_world",
    "frames_artifact": "episode.parquet",
    "events": [{"type": "GRAB", "timestamp_ns": 1700000000000000000}],
}


def test_spec_example_validates_as_spatial_episode():
    episode = SpatialEpisode.model_validate(SPATIAL_EPISODE_EXAMPLE)

    assert episode.task_id == "cube_to_bin"
    assert episode.events[0].type == "GRAB"


def test_episode_events_must_be_ordered_in_time():
    """A recorder that emits events out of order has lost frames or raced its
    own buffer; the episode is not trustworthy training data (STRUCT_2.md 61)."""
    payload = SPATIAL_EPISODE_EXAMPLE | {
        "events": [
            {"type": "GRAB", "timestamp_ns": 1700000000000000000},
            {"type": "RELEASE", "timestamp_ns": 1699999999999999999},
        ]
    }

    with pytest.raises(ValidationError, match="monotonic"):
        SpatialEpisode.model_validate(payload)


# STRUCT_2.md 31
TWIN_STATE_EXAMPLE = {
    "schema_version": "1.0",
    "timestamp_ns": 1700000000000000000,
    "scene_id": "demo_room",
    "robot": {"id": "robot_01", "joint_positions": [0.1, 0.5, -0.2, 0.4, 0.1, 0.0]},
    "objects": [
        {
            "id": "cube_01",
            "position_m": [0.3, 0.1, 0.7],
            "orientation_xyzw": [0, 0, 0, 1],
        }
    ],
    "task": {"id": "cube_to_bin", "status": "running"},
}


def test_spec_example_validates_as_twin_state():
    state = TwinState.model_validate(TWIN_STATE_EXAMPLE)

    assert state.robot.joint_positions == (0.1, 0.5, -0.2, 0.4, 0.1, 0.0)
    assert state.objects[0].id == "cube_01"
    assert state.task.status == "running"


def test_twin_state_rejects_non_finite_joint_positions():
    """A NaN joint arriving from the sim would silently teleport the AR robot."""
    payload = TWIN_STATE_EXAMPLE | {
        "robot": {"id": "robot_01", "joint_positions": [0.1, float("nan")]}
    }

    with pytest.raises(ValidationError, match="finite"):
        TwinState.model_validate(payload)


# STRUCT_2.md 33
CORRECTION_EVENT_EXAMPLE = {
    "schema_version": "1.0",
    "task_id": "cube_to_bin",
    "timestamp_ns": 1700000000000000000,
    "original_target": {"position_m": [0.4, 0.2, 0.5]},
    "corrected_target": {"position_m": [0.45, 0.25, 0.58]},
    "reason": "collision_avoidance",
}


def test_spec_example_validates_as_correction_event():
    event = CorrectionEvent.model_validate(CORRECTION_EVENT_EXAMPLE)

    assert event.original_target.position_m == (0.4, 0.2, 0.5)
    assert event.corrected_target.position_m == (0.45, 0.25, 0.58)
    assert event.reason == "collision_avoidance"


def test_correction_keeps_both_targets():
    """The whole value of a correction is the pair (STRUCT_2.md 70). Dropping
    the original leaves supervision data with nothing to learn from."""
    payload = dict(CORRECTION_EVENT_EXAMPLE)
    del payload["original_target"]

    with pytest.raises(ValidationError):
        CorrectionEvent.model_validate(payload)


# STRUCT_2.md 34
SCENE_MANIFEST_EXAMPLE = {
    "schema_version": "1.0",
    "scene_id": "demo_room",
    "canonical_usd": "artifacts/demo_room/scene.usd",
    "visual_assets": [
        {"id": "table", "glb": "artifacts/table.glb"},
        {"id": "robot", "glb": "artifacts/robot.glb"},
    ],
}


def test_spec_example_validates_as_scene_manifest():
    manifest = SceneManifest.model_validate(SCENE_MANIFEST_EXAMPLE)

    assert manifest.canonical_usd == "artifacts/demo_room/scene.usd"
    assert [a.id for a in manifest.visual_assets] == ["table", "robot"]


def test_scene_manifest_asset_lookup_by_id():
    """Clients resolve assets by id, not by list position."""
    manifest = SceneManifest.model_validate(SCENE_MANIFEST_EXAMPLE)

    assert manifest.asset("robot").glb == "artifacts/robot.glb"
    assert manifest.asset("nonexistent") is None
