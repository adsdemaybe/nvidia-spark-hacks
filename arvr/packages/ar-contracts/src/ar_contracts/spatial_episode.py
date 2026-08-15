"""SpatialEpisode — spec section 30.

Metadata for one recorded demonstration. High-rate pose data lives in the
referenced Parquet artifact (spec section 35); this model only carries the
metadata and discrete events (GRAB/RELEASE/...), matching the TEACH mode
controls in spec section 17.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import field_validator

from .common import (
    SCHEMA_VERSION,
    CoordinateFrame,
    DeviceType,
    FrozenModel,
    SchemaVersion,
    TimestampNs,
)

EventType = Literal["CALIBRATE", "START", "GRAB", "RELEASE", "FINISH", "CANCEL"]


class SpatialEvent(FrozenModel):
    type: EventType
    timestamp_ns: TimestampNs


class EpisodeSource(FrozenModel):
    device_type: DeviceType


class SpatialEpisode(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    episode_id: str
    task_id: str
    source: EpisodeSource
    coordinate_frame: CoordinateFrame
    frames_artifact: str
    events: tuple[SpatialEvent, ...] = ()

    @field_validator("episode_id")
    @classmethod
    def _valid_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError as exc:
            raise ValueError("episode_id must be a valid UUID string") from exc
        return v

    @field_validator("events")
    @classmethod
    def _monotonic(cls, v: tuple[SpatialEvent, ...]) -> tuple[SpatialEvent, ...]:
        timestamps = [e.timestamp_ns for e in v]
        if timestamps != sorted(timestamps):
            raise ValueError("events must be ordered by non-decreasing timestamp_ns")
        return v
