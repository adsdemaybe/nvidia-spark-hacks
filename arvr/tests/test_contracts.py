"""Acceptance-gate tests for the frozen spatial contracts (spec section 60).

    all schemas versioned                     -> test_schema_version_*
    units documented                          -> docs/CONTRACTS.md (not code-checkable)
    coordinate frames documented              -> docs/CONTRACTS.md
    example payloads validate                 -> test_*_matches_spec_example
    phone and backend share same definitions  -> both consume ar_contracts, by construction
    unknown schema versions rejected          -> test_unknown_schema_version_rejected
"""

from __future__ import annotations

import json
import math

import pytest
from ar_contracts import (
    CorrectionEvent,
    FollowState,
    SceneManifest,
    SpatialEpisode,
    SpatialFrame,
    TwinState,
    VerificationResult,
    compute_follow_target,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# spec section 60 — schema validation, versioning, example payloads
# ---------------------------------------------------------------------------


def test_spatial_frame_matches_spec_example():
    payload = {
        "schema_version": "1.0",
        "timestamp_ns": 1700000000000000000,
        "source": {"device_type": "phone", "input_type": "tracked_controller"},
        "frame": "struct_world",
        "position_m": [0.31, 0.18, 0.42],
        "orientation_xyzw": [0.02, 0.71, 0.03, 0.70],
        "gripper": 1.0,
    }
    frame = SpatialFrame.model_validate(payload)
    assert frame.position_m == (0.31, 0.18, 0.42)
    assert json.loads(frame.model_dump_json())["gripper"] == 1.0


def test_spatial_episode_matches_spec_example():
    payload = {
        "schema_version": "1.0",
        "episode_id": "3fae3b8e-3b0e-4e2b-9a3a-8f1e6f0f6c2f",
        "task_id": "cube_to_bin",
        "source": {"device_type": "phone"},
        "coordinate_frame": "struct_world",
        "frames_artifact": "episode.parquet",
        "events": [{"type": "GRAB", "timestamp_ns": 1700000000000000000}],
    }
    episode = SpatialEpisode.model_validate(payload)
    assert episode.events[0].type == "GRAB"


def test_twin_state_matches_spec_example():
    payload = {
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
    state = TwinState.model_validate(payload)
    assert state.task.status == "running"


def test_follow_state_matches_spec_example():
    payload = {
        "schema_version": "1.0",
        "timestamp_ns": 1700000000000000000,
        "human_pose": {"position_m": [1.0, 2.0, 0.0], "orientation_xyzw": [0, 0, 0, 1]},
        "desired_follow_distance_m": 1.5,
        "follow_target": {"position_m": [0.0, 2.0, 0.0]},
    }
    follow = FollowState.model_validate(payload)
    assert follow.desired_follow_distance_m == 1.5


def test_correction_event_matches_spec_example():
    payload = {
        "schema_version": "1.0",
        "task_id": "cube_to_bin",
        "timestamp_ns": 1700000000000000000,
        "original_target": {"position_m": [0.4, 0.2, 0.5]},
        "corrected_target": {"position_m": [0.45, 0.25, 0.58]},
        "reason": "collision_avoidance",
    }
    correction = CorrectionEvent.model_validate(payload)
    assert correction.reason == "collision_avoidance"


def test_scene_manifest_matches_spec_example():
    payload = {
        "schema_version": "1.0",
        "scene_id": "demo_room",
        "canonical_usd": "artifacts/demo_room/scene.usd",
        "visual_assets": [
            {"id": "table", "glb": "artifacts/table.glb"},
            {"id": "robot", "glb": "artifacts/robot.glb"},
        ],
    }
    manifest = SceneManifest.model_validate(payload)
    assert manifest.visual_assets[1].id == "robot"


_SPATIAL_FRAME_BASE = {
    "timestamp_ns": 0,
    "source": {"device_type": "phone"},
    "frame": "struct_world",
    "position_m": [0, 0, 0],
    "orientation_xyzw": [0, 0, 0, 1],
}
_TWIN_STATE_BASE = {
    "timestamp_ns": 0,
    "scene_id": "s",
    "robot": {"id": "r", "joint_positions": []},
}


@pytest.mark.parametrize(
    "model,payload",
    [
        (SpatialFrame, _SPATIAL_FRAME_BASE),
        (TwinState, _TWIN_STATE_BASE),
    ],
)
def test_unknown_schema_version_rejected(model, payload):
    payload = {**payload, "schema_version": "2.0"}
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_extra_fields_rejected():
    payload = {
        "timestamp_ns": 0,
        "scene_id": "s",
        "robot": {"id": "r", "joint_positions": []},
        "unexpected_field": True,
    }
    with pytest.raises(ValidationError):
        TwinState.model_validate(payload)


# ---------------------------------------------------------------------------
# spec section 61 — invalid quaternion rejection, timestamp ordering
# ---------------------------------------------------------------------------


def test_non_unit_quaternion_rejected():
    with pytest.raises(ValidationError):
        SpatialFrame.model_validate(
            {
                "timestamp_ns": 0,
                "source": {"device_type": "phone"},
                "frame": "struct_world",
                "position_m": [0, 0, 0],
                "orientation_xyzw": [1, 1, 1, 1],  # norm = 2, not 1
            }
        )


def test_nan_position_rejected():
    with pytest.raises(ValidationError):
        SpatialFrame.model_validate(
            {
                "timestamp_ns": 0,
                "source": {"device_type": "phone"},
                "frame": "struct_world",
                "position_m": [math.nan, 0, 0],
                "orientation_xyzw": [0, 0, 0, 1],
            }
        )


def test_negative_timestamp_rejected():
    with pytest.raises(ValidationError):
        SpatialFrame.model_validate(
            {
                "timestamp_ns": -1,
                "source": {"device_type": "phone"},
                "frame": "struct_world",
                "position_m": [0, 0, 0],
                "orientation_xyzw": [0, 0, 0, 1],
            }
        )


def test_gripper_out_of_range_rejected():
    with pytest.raises(ValidationError):
        SpatialFrame.model_validate(
            {
                "timestamp_ns": 0,
                "source": {"device_type": "phone"},
                "frame": "struct_world",
                "position_m": [0, 0, 0],
                "orientation_xyzw": [0, 0, 0, 1],
                "gripper": 1.5,
            }
        )


def test_episode_events_must_be_time_ordered():
    with pytest.raises(ValidationError):
        SpatialEpisode.model_validate(
            {
                "episode_id": "3fae3b8e-3b0e-4e2b-9a3a-8f1e6f0f6c2f",
                "task_id": "cube_to_bin",
                "source": {"device_type": "phone"},
                "coordinate_frame": "struct_world",
                "frames_artifact": "episode.parquet",
                "events": [
                    {"type": "RELEASE", "timestamp_ns": 200},
                    {"type": "GRAB", "timestamp_ns": 100},
                ],
            }
        )


def test_episode_id_must_be_uuid():
    with pytest.raises(ValidationError):
        SpatialEpisode.model_validate(
            {
                "episode_id": "not-a-uuid",
                "task_id": "cube_to_bin",
                "source": {"device_type": "phone"},
                "coordinate_frame": "struct_world",
                "frames_artifact": "episode.parquet",
            }
        )


def test_frozen_model_is_immutable():
    frame = SpatialFrame.model_validate(
        {
            "timestamp_ns": 0,
            "source": {"device_type": "phone"},
            "frame": "struct_world",
            "position_m": [0, 0, 0],
            "orientation_xyzw": [0, 0, 0, 1],
        }
    )
    with pytest.raises(ValidationError):
        frame.timestamp_ns = 1


# ---------------------------------------------------------------------------
# spec section 63 — rejected demonstrations carry a measurable reason
# ---------------------------------------------------------------------------


def test_rejected_verification_requires_reason():
    with pytest.raises(ValidationError):
        VerificationResult.model_validate(
            {
                "episode_id": "3fae3b8e-3b0e-4e2b-9a3a-8f1e6f0f6c2f",
                "status": "rejected",
                "checks": {
                    "ik": True,
                    "joint_limits": True,
                    "replay": False,
                    "task_predicate": False,
                },
            }
        )


def test_accepted_verification_requires_dataset_id():
    with pytest.raises(ValidationError):
        VerificationResult.model_validate(
            {
                "episode_id": "3fae3b8e-3b0e-4e2b-9a3a-8f1e6f0f6c2f",
                "status": "accepted",
                "checks": {
                    "ik": True,
                    "joint_limits": True,
                    "replay": True,
                    "task_predicate": True,
                },
            }
        )


def test_rejected_verification_with_reason_is_valid():
    result = VerificationResult.model_validate(
        {
            "episode_id": "3fae3b8e-3b0e-4e2b-9a3a-8f1e6f0f6c2f",
            "status": "rejected",
            "checks": {
                "ik": True,
                "joint_limits": True,
                "replay": False,
                "task_predicate": False,
            },
            "rejection_reason": "replay tracking error 0.09m exceeds 0.03m threshold",
        }
    )
    assert result.rejection_reason is not None


# ---------------------------------------------------------------------------
# spec section 64 — follow-target calculation
# ---------------------------------------------------------------------------


def test_follow_target_directly_behind_facing_forward():
    # Human at origin, identity orientation (facing local +X, the forward
    # convention documented in follow.py), 1.5m follow distance.
    target = compute_follow_target((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.5)
    assert target == pytest.approx((-1.5, 0.0, 0.0))


def test_follow_target_updates_continuously_with_position():
    a = compute_follow_target((1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.5)
    b = compute_follow_target((2.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.5)
    assert a != b
    assert all(math.isfinite(c) for c in a + b)


def test_follow_target_rejects_non_positive_distance():
    with pytest.raises(ValueError):
        compute_follow_target((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 0.0)


def test_follow_target_rejects_non_finite_input():
    with pytest.raises(ValueError):
        compute_follow_target((math.nan, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.5)
