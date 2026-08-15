"""MockHandProvider — deterministic prerecorded hand motion (spec section
52 Phase 4), so the pipeline is testable before any real hand-tracking
hardware is in the loop.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from ar_contracts import HandFrame

from .hand_provider import HandProvider

ARVR_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_EPISODE_PATH = (
    ARVR_ROOT / "fixtures" / "spatial-training" / "hand" / "mock_episode.jsonl"
)


class MockHandProvider(HandProvider):
    def __init__(self, episode_path: Path | None = None) -> None:
        self.episode_path = episode_path or DEFAULT_EPISODE_PATH

    def stream(self) -> Iterator[HandFrame]:
        with self.episode_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield HandFrame.model_validate(json.loads(line))
