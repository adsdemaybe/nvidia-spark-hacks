"""Data contract for Cosmos/VSS semantic annotations.

`LlmSemanticPayload` is the subset of fields a model response is expected to
fill in. `SemanticEpisode` extends it with provenance fields the analyzer
attaches after parsing (source, backend, model, episode_id, ...). Keeping
them as a subclass means the cross-field validators below (duplicate object
ids, sorted timeline, dangling references) apply identically whether we are
validating a raw model payload or a finished episode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

ObjectRole = Literal[
    "manipulated_object",
    "target",
    "container",
    "surface",
    "obstacle",
    "tool",
    "other",
]

PhaseName = Literal[
    "idle",
    "approach",
    "pregrasp",
    "grasp",
    "lift",
    "transport",
    "rotate",
    "place",
    "release",
    "retract",
    "other",
]

RelationName = Literal[
    "left_of",
    "right_of",
    "above",
    "below",
    "inside",
    "on",
    "near",
    "touching",
    "held_by_hand",
    "other",
]

SourceType = Literal["file", "url", "rtsp"]

# "mock" is a first-class backend (see analyzer.MockAnalyzer) so offline/CI
# runs and demos-without-a-GPU can still produce a schema-valid, provenance
# tagged artifact instead of lying about which backend produced it.
BackendName = Literal["vss", "cosmos", "mock"]


def new_semantic_id() -> str:
    return f"sem_{uuid4().hex[:12]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ImageCoordinate(BaseModel):
    """A 2D pixel-space observation. Never a substitute for a metric 3D pose."""

    frame_s: float
    x_px: float
    y_px: float
    coordinate_system: Literal["image_2d"] = "image_2d"


class SemanticObject(BaseModel):
    id: str
    label: str
    role: ObjectRole
    attributes: dict[str, str] = Field(default_factory=dict)
    image_coordinates: list[ImageCoordinate] = Field(default_factory=list)


class TemporalPhase(BaseModel):
    start_s: float
    end_s: float
    phase: PhaseName
    description: str
    object_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ordering(self) -> "TemporalPhase":
        if self.end_s < self.start_s:
            raise ValueError(f"end_s ({self.end_s}) must be >= start_s ({self.start_s})")
        return self


class SpatialRelation(BaseModel):
    time_s: float | None = None
    subject_id: str
    relation: RelationName
    object_id: str


class LlmSemanticPayload(BaseModel):
    """The model-controlled portion of a `SemanticEpisode`."""

    task_type: str
    instruction: str
    summary: str

    objects: list[SemanticObject]
    timeline: list[TemporalPhase]
    spatial_relations: list[SpatialRelation] = Field(default_factory=list)

    success_condition: str | None = None
    ambiguity_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_object_ids_unique(self) -> "LlmSemanticPayload":
        ids = [o.id for o in self.objects]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate object id(s): {dupes}")
        return self

    @model_validator(mode="after")
    def _check_timeline_sorted(self) -> "LlmSemanticPayload":
        starts = [p.start_s for p in self.timeline]
        if starts != sorted(starts):
            raise ValueError("timeline phases must be sorted by start_s")
        return self

    @model_validator(mode="after")
    def _check_referenced_object_ids(self) -> "LlmSemanticPayload":
        known = {o.id for o in self.objects}
        unknown: set[str] = set()
        for phase in self.timeline:
            unknown |= {oid for oid in phase.object_ids if oid not in known}
        for rel in self.spatial_relations:
            if rel.subject_id not in known:
                unknown.add(rel.subject_id)
            if rel.object_id not in known:
                unknown.add(rel.object_id)
        if unknown:
            raise ValueError(f"reference(s) to unknown object id(s): {sorted(unknown)}")
        return self


class SemanticEpisode(LlmSemanticPayload):
    schema_version: str = "1.0"
    semantic_id: str = Field(default_factory=new_semantic_id)
    episode_id: str | None = None
    source_name: str
    source_type: SourceType
    backend: BackendName
    model: str

    video_duration_s: float | None = None
    created_at: str = Field(default_factory=utc_now_iso)
