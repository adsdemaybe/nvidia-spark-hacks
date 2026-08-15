"""SpatialEpisode -- one recorded human demonstration.

Metadata is JSON; the high-rate pose stream lives beside it as a Parquet
artifact (STRUCT_2.md 35). The episode must survive network loss, so the client
writes frames locally and uploads the finished artifact rather than streaming
thousands of HTTP requests (STRUCT_2.md 19, 36).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .frames import CoordinateFrame, FrameSource

SCHEMA_VERSION = "1.0"

EventType = Literal["GRAB", "RELEASE", "START", "FINISH", "CANCEL"]


class EpisodeEvent(BaseModel):
    """A discrete thing the human did, timestamped against the same clock as the
    pose stream so it can be aligned with a frame."""

    type: EventType
    timestamp_ns: int


class SpatialEpisode(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    episode_id: str
    task_id: str
    source: FrameSource
    coordinate_frame: CoordinateFrame = "struct_world"
    frames_artifact: str
    events: list[EpisodeEvent] = Field(default_factory=list)

    @field_validator("events")
    @classmethod
    def _events_monotonic(cls, events: list[EpisodeEvent]) -> list[EpisodeEvent]:
        for earlier, later in zip(events, events[1:], strict=False):
            if later.timestamp_ns < earlier.timestamp_ns:
                raise ValueError(
                    "episode events must be monotonic in timestamp_ns; "
                    f"{later.type}@{later.timestamp_ns} precedes "
                    f"{earlier.type}@{earlier.timestamp_ns}"
                )
        return events
