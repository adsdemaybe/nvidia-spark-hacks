"""In-memory episode store.

Deliberately not a database — this is a hackathon-scope backend behind the
spec's Episodes API (section 36), single process. Swap for real persistence
if/when multiple backend instances need to share state; nothing in the
router code should need to change, only this module.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Literal

from ar_contracts import EpisodeSource, SpatialEvent, SpatialFrame, VerificationResult

EpisodeStatus = Literal["created", "uploaded", "accepted", "rejected"]


@dataclass
class EpisodeRecord:
    episode_id: str
    task_id: str
    source: EpisodeSource
    coordinate_frame: str
    status: EpisodeStatus = "created"
    frames: list[SpatialFrame] = field(default_factory=list)
    events: list[SpatialEvent] = field(default_factory=list)
    result: VerificationResult | None = None


class EpisodeStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._episodes: dict[str, EpisodeRecord] = {}

    def create(
        self, task_id: str, source: EpisodeSource, coordinate_frame: str
    ) -> EpisodeRecord:
        episode_id = str(uuid.uuid4())
        record = EpisodeRecord(
            episode_id=episode_id,
            task_id=task_id,
            source=source,
            coordinate_frame=coordinate_frame,
        )
        with self._lock:
            self._episodes[episode_id] = record
        return record

    def get(self, episode_id: str) -> EpisodeRecord | None:
        with self._lock:
            return self._episodes.get(episode_id)

    def update(self, record: EpisodeRecord) -> None:
        with self._lock:
            self._episodes[record.episode_id] = record
