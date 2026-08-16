from __future__ import annotations

import pytest
from pydantic import ValidationError

from cosmos_vss.schemas import LlmSemanticPayload, SemanticEpisode, TemporalPhase


def _base_payload(**overrides) -> dict:
    payload = {
        "task_type": "pick_and_place",
        "instruction": "Move the bottle.",
        "summary": "summary",
        "objects": [
            {"id": "obj_1", "label": "bottle", "role": "manipulated_object"},
            {"id": "obj_2", "label": "basket", "role": "target"},
        ],
        "timeline": [
            {"start_s": 0.0, "end_s": 1.0, "phase": "approach", "description": "approach", "object_ids": ["obj_1"]},
        ],
        "spatial_relations": [],
        "success_condition": "bottle in basket",
        "ambiguity_notes": [],
    }
    payload.update(overrides)
    return payload


def test_valid_payload_parses():
    payload = LlmSemanticPayload.model_validate(_base_payload())
    assert payload.task_type == "pick_and_place"


def test_phase_end_before_start_rejected():
    with pytest.raises(ValidationError):
        TemporalPhase(start_s=2.0, end_s=1.0, phase="grasp", description="x", object_ids=[])


def test_duplicate_object_ids_rejected():
    payload = _base_payload(
        objects=[
            {"id": "obj_1", "label": "bottle", "role": "manipulated_object"},
            {"id": "obj_1", "label": "basket", "role": "target"},
        ]
    )
    with pytest.raises(ValidationError, match="duplicate object id"):
        LlmSemanticPayload.model_validate(payload)


def test_relationship_reference_to_unknown_object_rejected():
    payload = _base_payload(
        spatial_relations=[{"subject_id": "obj_1", "relation": "inside", "object_id": "nonexistent"}]
    )
    with pytest.raises(ValidationError, match="unknown object id"):
        LlmSemanticPayload.model_validate(payload)


def test_timeline_object_id_reference_to_unknown_object_rejected():
    payload = _base_payload(
        timeline=[
            {"start_s": 0.0, "end_s": 1.0, "phase": "approach", "description": "x", "object_ids": ["ghost"]},
        ]
    )
    with pytest.raises(ValidationError, match="unknown object id"):
        LlmSemanticPayload.model_validate(payload)


def test_unsorted_timeline_rejected():
    payload = _base_payload(
        timeline=[
            {"start_s": 5.0, "end_s": 6.0, "phase": "release", "description": "x", "object_ids": []},
            {"start_s": 0.0, "end_s": 1.0, "phase": "approach", "description": "x", "object_ids": []},
        ]
    )
    with pytest.raises(ValidationError, match="sorted"):
        LlmSemanticPayload.model_validate(payload)


def test_malformed_source_type_rejected():
    payload = _base_payload()
    payload.update(
        source_name="demo.mp4",
        source_type="ftp",  # not one of file/url/rtsp
        backend="vss",
        model="cosmos-reason2",
    )
    with pytest.raises(ValidationError):
        SemanticEpisode.model_validate(payload)


def test_valid_semantic_episode_round_trips():
    payload = _base_payload()
    payload.update(
        source_name="demo.mp4",
        source_type="file",
        backend="vss",
        model="cosmos-reason2",
        episode_id="episode_001",
    )
    episode = SemanticEpisode.model_validate(payload)
    assert episode.semantic_id.startswith("sem_")
    assert episode.episode_id == "episode_001"

    dumped = episode.model_dump_json()
    reloaded = SemanticEpisode.model_validate_json(dumped)
    assert reloaded == episode
