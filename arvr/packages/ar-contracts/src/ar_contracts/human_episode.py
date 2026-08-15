"""HumanEpisode — Shadow Robot Spatial Demonstration Pipeline spec section 21.

The core invariant of this whole pipeline (spec section 6): never destroy
raw human data. A HumanEpisode is captured and stored *before* any
robot-specific conversion happens, and it is never mutated afterward —
`derive_interaction_ir()` (ar_datapipe/interaction_ir.py) and every
retargeting step read from it, none of them touch it. This is what makes one
recorded demonstration transferable to multiple robot embodiments later
(spec section 36).

`HumanEpisode` itself is a plain dataclass, not frozen pydantic — it holds
potentially-large `list[HandFrame]`/`list[ObjectState]` sequences captured
frame-by-frame during a live demo, not a single validated wire payload.
`HumanEpisodeMetadata` and `HumanEpisodeEvent` (the parts that really are
small, wire-shaped records) stay frozen pydantic as usual.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import field_validator

from .common import SCHEMA_VERSION, CoordinateFrame, FrozenModel, SchemaVersion, TimestampNs
from .hand_frame import HandFrame, HandSourceDevice
from .twin_state import ObjectState

HumanEpisodeStatus = Literal["recorded", "normalized", "retargeted", "verified"]

# ADDITIVE CONTRACT CHANGE (needs team sign-off, see arvr/STATE.md Round 10).
#
# The first five values are the original vocabulary and are unchanged, so
# every episode already recorded still validates and every existing producer
# and consumer keeps working. The six after them are what the ball-sorting
# task actually does, and there is no honest way to express them in the old
# set: mapping "the red ball landed in the blue basket" onto "contact" would
# throw away the one fact the episode exists to record. A task predicate that
# cannot be reconstructed from the event stream is not training data.
HumanEventType = Literal[
    "pinch",
    "release",
    "contact",
    "task_start",
    "task_finish",
    # Ball-sorting (Quest teleop spec sections 17, 27).
    "grasp_start",
    "grasp_end",
    "ball_enter_basket",
    "wrong_basket",
    "sort_complete",
    "tracking_lost",
]


class HumanEpisodeEvent(FrozenModel):
    type: HumanEventType
    timestamp_ns: TimestampNs
    # Which object and container the event concerns. Optional because the
    # original five event types are about the hand, not about a named object
    # -- a "pinch" has nothing to name. A sorting event is meaningless
    # without them: "ball_enter_basket" has to say which ball and which
    # basket or the predicate cannot be rebuilt from the record.
    object_id: str | None = None
    container_id: str | None = None


class HumanEpisodeMetadata(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    episode_id: str
    task_id: str
    asset_id: str
    coordinate_frame: CoordinateFrame = "struct_world"
    hand_provider: HandSourceDevice
    status: HumanEpisodeStatus = "recorded"

    @field_validator("episode_id")
    @classmethod
    def _valid_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError as exc:
            raise ValueError("episode_id must be a valid UUID string") from exc
        return v


@dataclass
class HumanEpisode:
    metadata: HumanEpisodeMetadata
    hand_frames: list[HandFrame] = field(default_factory=list)
    object_states: list[ObjectState] = field(default_factory=list)
    events: list[HumanEpisodeEvent] = field(default_factory=list)
