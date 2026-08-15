"""Acceptance-gate tests for Interaction IR derivation (Shadow Robot Spatial
Demonstration Pipeline spec section 52, Phase 9). Pure numpy -- no
pytest.importorskip needed.
"""

from __future__ import annotations

import copy
import math

import pytest
from ar_contracts import HumanEpisode, HumanEpisodeEvent, HumanEpisodeMetadata, Pose
from ar_datapipe import derive_interaction_ir, object_relative_pose
from spatial_providers import FixtureAssetProvider, MockHandProvider


def _human_episode(with_contact_event: bool = True) -> HumanEpisode:
    metadata = HumanEpisodeMetadata(
        episode_id="3fae3b8e-3b0e-4e2b-9a3a-8f1e6f0f6c2f",
        task_id="press_button",
        asset_id="button_01",
        hand_provider="mock",
    )
    hand_frames = list(MockHandProvider().stream())
    events = (
        [HumanEpisodeEvent(type="contact", timestamp_ns=hand_frames[45].timestamp_ns)]
        if with_contact_event
        else []
    )
    return HumanEpisode(metadata=metadata, hand_frames=hand_frames, events=events)


# ---------------------------------------------------------------------------
# object_relative_pose -- known-fixture cross-check
# ---------------------------------------------------------------------------


def test_object_relative_pose_pure_translation():
    world_object = Pose(position_m=(1.0, 0.0, 0.0), orientation_xyzw=(0, 0, 0, 1))
    world_hand = Pose(position_m=(1.5, 0.0, 0.0), orientation_xyzw=(0, 0, 0, 1))
    position, orientation = object_relative_pose(world_object, world_hand)
    assert list(position) == pytest.approx([0.5, 0.0, 0.0])
    assert list(orientation) == pytest.approx([0, 0, 0, 1])


def test_object_relative_pose_90_degree_object_rotation():
    # Object rotated +90 deg about Z (struct convention). A hand 1m along
    # world +X from the object should read as 1m along the object's local
    # -Y (since the object's local +X now points along world +Y).
    half = math.sqrt(0.5)
    world_object = Pose(position_m=(0.0, 0.0, 0.0), orientation_xyzw=(0, 0, half, half))
    world_hand = Pose(position_m=(1.0, 0.0, 0.0), orientation_xyzw=(0, 0, 0, 1))
    position, _orientation = object_relative_pose(world_object, world_hand)
    assert list(position) == pytest.approx([0.0, -1.0, 0.0], abs=1e-9)


# ---------------------------------------------------------------------------
# derive_interaction_ir -- phase shape + never mutates the source episode
# ---------------------------------------------------------------------------


def test_mock_episode_produces_expected_phase_shape():
    episode = _human_episode(with_contact_event=True)
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    asset_pose = Pose(position_m=(0.4, 0.0, 0.53), orientation_xyzw=(0, 0, 0, 1))

    ir = derive_interaction_ir(episode, asset, asset_pose)

    phase_types = [p.type for p in ir.phases]
    assert phase_types == ["approach", "contact", "press", "retract"]
    assert ir.task_id == "press_button"
    assert ir.asset_id == "button_01"
    assert ir.reference_frame == "button_01.button"

    press = next(p for p in ir.phases if p.type == "press")
    assert press.distance_m == 0.006
    assert press.axis == (0.0, 0.0, -1.0)


def test_no_contact_event_omits_contact_phase():
    episode = _human_episode(with_contact_event=False)
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    asset_pose = Pose(position_m=(0.4, 0.0, 0.53), orientation_xyzw=(0, 0, 0, 1))

    ir = derive_interaction_ir(episode, asset, asset_pose)
    assert "contact" not in [p.type for p in ir.phases]


def test_derivation_never_mutates_the_source_human_episode():
    episode = _human_episode(with_contact_event=True)
    before = copy.deepcopy(episode)
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    asset_pose = Pose(position_m=(0.4, 0.0, 0.53), orientation_xyzw=(0, 0, 0, 1))

    derive_interaction_ir(episode, asset, asset_pose)

    assert episode.hand_frames == before.hand_frames
    assert episode.events == before.events
    assert episode.metadata == before.metadata
