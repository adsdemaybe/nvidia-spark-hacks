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
    HumanEpisodeEvent,
    HumanEpisodeMetadata,
    InteractableAsset,
    InteractionPhase,
    ObjectState,
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


def test_hand_frame_accepts_webcam_source_device():
    frame = HandFrame.model_validate(_hand_frame_payload(source_device="webcam"))
    assert frame.source_device == "webcam"


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


# ---------------------------------------------------------------------------
# Round 10's additive contract extensions (ball sorting).
#
# These exist to prove the additions are additive. Every one of them is a
# widened Literal or a new optional field, so the gate they have to pass is
# not "the new thing works" but "nothing that validated before stopped
# validating".
# ---------------------------------------------------------------------------


def test_original_human_event_vocabulary_still_validates():
    for event_type in ("pinch", "release", "contact", "task_start", "task_finish"):
        event = HumanEpisodeEvent.model_validate(
            {"type": event_type, "timestamp_ns": 1_700_000_000_000_000_000}
        )
        assert event.type == event_type
        # The new fields are optional and stay absent for hand-only events.
        assert event.object_id is None
        assert event.container_id is None


def test_sorting_events_carry_the_object_and_container_they_concern():
    event = HumanEpisodeEvent.model_validate(
        {
            "type": "ball_enter_basket",
            "timestamp_ns": 1_700_000_000_000_000_000,
            "object_id": "red_ball_0",
            "container_id": "red_basket",
        }
    )
    assert event.object_id == "red_ball_0"
    assert event.container_id == "red_basket"
    assert HumanEpisodeEvent.model_validate_json(event.model_dump_json()) == event


def test_unknown_human_event_type_is_still_rejected():
    with pytest.raises(ValidationError):
        HumanEpisodeEvent.model_validate({"type": "teleported", "timestamp_ns": 1})


def test_object_state_without_a_timestamp_still_validates():
    state = ObjectState.model_validate({"id": "bin", "position_m": [0.6, -0.7, 0.0]})
    assert state.timestamp_ns is None
    assert state.orientation_xyzw == (0.0, 0.0, 0.0, 1.0)


def test_object_state_carries_a_timestamp_when_recorded_as_a_time_series():
    state = ObjectState.model_validate(
        {
            "id": "red_ball_0",
            "position_m": [0.19, 0.07, 0.17],
            "timestamp_ns": 1_700_000_000_000_000_000,
        }
    )
    assert state.timestamp_ns == 1_700_000_000_000_000_000
    assert ObjectState.model_validate_json(state.model_dump_json()) == state


def test_original_interaction_phases_still_validate():
    for phase_type in ("approach", "contact", "press", "pull", "retract", "grasp", "release"):
        phase = InteractionPhase.model_validate({"type": phase_type})
        assert phase.type == phase_type
        assert phase.timestamp_ns is None


def test_pick_and_place_phases_validate():
    for phase_type in ("lift", "transport", "place"):
        phase = InteractionPhase.model_validate(
            {"type": phase_type, "timestamp_ns": 1_700_000_000_000_000_000}
        )
        assert phase.type == phase_type


def test_unknown_interaction_phase_is_still_rejected():
    with pytest.raises(ValidationError):
        InteractionPhase.model_validate({"type": "juggle"})
