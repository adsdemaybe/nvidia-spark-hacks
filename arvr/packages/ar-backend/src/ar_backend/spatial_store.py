"""In-memory HumanEpisode store — sibling to store.py's EpisodeStore, same
single-process/no-database scope (spec section 21).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Literal

from ar_contracts import HandFrame, HumanEpisodeEvent, ObjectState, RobotEpisode

HumanEpisodeStatus = Literal["created", "uploaded", "accepted", "rejected"]


@dataclass
class HumanEpisodeRecord:
    episode_id: str
    task_id: str
    asset_id: str
    hand_provider: str
    status: HumanEpisodeStatus = "created"
    hand_frames: list[HandFrame] = field(default_factory=list)
    object_states: list[ObjectState] = field(default_factory=list)
    events: list[HumanEpisodeEvent] = field(default_factory=list)
    result: RobotEpisode | None = None
    # What `/export-human` produced for this episode, as a plain dict of the
    # route's response fields. A dict rather than the ar_datapipe result type
    # so this store keeps depending on nothing but ar_contracts, and rather
    # than the route's response model because that module imports this one.
    #
    # Remembered at all so a retried POST returns the first export instead of
    # appending the same demonstration to the dataset a second time -- a
    # duplicate is invisible on disk and silently doubles that episode's
    # weight in training.
    human_export: dict | None = None


class HumanEpisodeStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._episodes: dict[str, HumanEpisodeRecord] = {}

    def create(self, task_id: str, asset_id: str, hand_provider: str) -> HumanEpisodeRecord:
        episode_id = str(uuid.uuid4())
        record = HumanEpisodeRecord(
            episode_id=episode_id,
            task_id=task_id,
            asset_id=asset_id,
            hand_provider=hand_provider,
        )
        with self._lock:
            self._episodes[episode_id] = record
        return record

    def get(self, episode_id: str) -> HumanEpisodeRecord | None:
        with self._lock:
            return self._episodes.get(episode_id)

    def update(self, record: HumanEpisodeRecord) -> None:
        with self._lock:
            self._episodes[record.episode_id] = record
