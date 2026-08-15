"""Acceptance-gate tests for the Shadow Robot Spatial Demonstration Pipeline's
new contracts (spec section 52 Phase 1). Mirrors test_contracts.py's pattern:
round-trip, frozen/immutable, extra="forbid", unknown-field/value rejection.
"""

from __future__ import annotations

import json
import uuid

import pytest
from ar_contracts import (
    AssetPart,
    HandFrame,
    HumanEpisodeMetadata,
    InteractableAsset,
    RobotEpisodeMetadata,
    VerificationChecks,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# HandFrame
# ---------------------------------------------------------------------------


def _hand_frame_payload(**overrides):
    payload = {
        "timestamp_ns": 1_700_000_000_000_000_000,
        "source_device": "mock",
        "hand": "right",
        "joints": {
            "wrist": {
                "position_m": [0.3, 0.1, 0.5],
                "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            "thumb-tip": {
                "position_m": [0.31, 0.1, 0.52],
                "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "confidence": 0.9,
            },
        },
    }
    payload.update(overrides)
    return payload


def test_hand_frame_round_trips():
    frame = HandFrame.model_validate(_hand_frame_payload())
    again = HandFrame.model_validate_json(frame.model_dump_json())
    assert again == frame
    assert again.joints["wrist"].position_m == (0.3, 0.1, 0.5)
    assert again.joints["thumb-tip"].confidence == 0.9


def test_hand_frame_rejects_unknown_joint_name():
    wrist = _hand_frame_payload()["joints"]["wrist"]
    with pytest.raises(ValidationError):
        HandFrame.model_validate(_hand_frame_payload(joints={"not-a-real-joint": wrist}))


def test_hand_frame_is_frozen():
    frame = HandFrame.model_validate(_hand_frame_payload())
    with pytest.raises(ValidationError):
        frame.hand = "left"


def test_hand_frame_rejects_extra_fields():
    with pytest.raises(ValidationError):
        HandFrame.model_validate(_hand_frame_payload(unexpected_field=True))


def test_hand_joint_confidence_out_of_range_rejected():
    payload = _hand_frame_payload()
    payload["joints"]["wrist"]["confidence"] = 1.5
    with pytest.raises(ValidationError):
        HandFrame.model_validate(payload)


# ---------------------------------------------------------------------------
# VerificationChecks.collision_valid (extends an existing frozen contract)
# ---------------------------------------------------------------------------


def test_verification_checks_validates_without_collision_valid():
    """The old TEACH pipeline's call site never sets this field -- it must
    keep validating unchanged after this addition."""
    checks = VerificationChecks(
        ik=True, joint_limits=True, velocity=True, replay=True, task_predicate=True,
    )
    assert checks.collision_valid is None


def test_verification_checks_validates_with_collision_valid():
    checks = VerificationChecks(
        ik=True, joint_limits=True, velocity=True, replay=True, task_predicate=True,
        collision_valid=False,
    )
    assert checks.collision_valid is False
    assert json.loads(checks.model_dump_json())["collision_valid"] is False


# ---------------------------------------------------------------------------
# InteractableAsset
# ---------------------------------------------------------------------------


def test_interactable_asset_matches_button_example():
    payload = {
        "asset_id": "button_01",
        "parts": {
            "button": {
                "interaction": "press",
                "local_origin_m": [0.0, 0.0, 0.02],
                "axis": [0.0, 0.0, -1.0],
                "travel_m": 0.006,
            }
        },
    }
    asset = InteractableAsset.model_validate(payload)
    assert asset.parts["button"].interaction == "press"
    assert asset.parts["button"].travel_m == 0.006


def test_asset_part_allows_grasp_with_no_travel():
    part = AssetPart.model_validate({"interaction": "grasp"})
    assert part.travel_m is None


# ---------------------------------------------------------------------------
# HumanEpisodeMetadata
# ---------------------------------------------------------------------------


def test_human_episode_metadata_round_trips():
    payload = {
        "episode_id": str(uuid.uuid4()),
        "task_id": "press_button",
        "asset_id": "button_01",
        "hand_provider": "mock",
    }
    metadata = HumanEpisodeMetadata.model_validate(payload)
    assert metadata.coordinate_frame == "struct_world"
    assert metadata.status == "recorded"


def test_human_episode_metadata_rejects_non_uuid_episode_id():
    with pytest.raises(ValidationError):
        HumanEpisodeMetadata.model_validate(
            {
                "episode_id": "not-a-uuid",
                "task_id": "press_button",
                "asset_id": "button_01",
                "hand_provider": "mock",
            }
        )


# ---------------------------------------------------------------------------
# RobotEpisodeMetadata -- full provenance round-trip (spec section 67)
# ---------------------------------------------------------------------------


def test_robot_episode_metadata_round_trips_with_full_provenance():
    payload = {
        "robot_id": "fixture_so101",
        "robot_bundle_hash": "a" * 64,
        "source_human_episode_id": str(uuid.uuid4()),
        "human_episode_hash": "b" * 64,
        "task_id": "press_button",
        "asset_ids": ["button_01"],
        "asset_bundle_hash": "c" * 64,
        "simulator": "mujoco",
        "retargeter_version": "arm_retargeter@1",
        "task_version": "1",
        "created_at_ns": 1_700_000_000_000_000_000,
        "success": True,
        "dataset_id": "ds_001",
    }
    metadata = RobotEpisodeMetadata.model_validate(payload)
    again = RobotEpisodeMetadata.model_validate_json(metadata.model_dump_json())
    assert again == metadata
    assert again.asset_ids == ("button_01",)
