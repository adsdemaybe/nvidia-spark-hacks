"""FollowSession — session control for FOLLOW mode, spec sections 22-24.

Ported/adapted from Andrew's independent `arxr-core` implementation during
the arvr/arxr consolidation (see STATE.md) — his design was cleaner than
not having one at all: only FOLLOWING produces a target; everything else
(not started, paused, stopped) produces None and the caller sends nothing.
That is what makes STOP immediate (spec section 64): there is no queue to
drain and no last-good target to keep republishing.
"""

from __future__ import annotations

from enum import Enum

from .common import Pose, Target
from .follow import compute_follow_target
from .follow_state import FollowState


class FollowSessionMode(Enum):
    """Internal session state — distinct from `FollowState.state`'s wire-level
    `FollowMode` Literal (which has no "idle": a session never emits a
    FollowState while idle, see `update()` below)."""

    IDLE = "idle"
    FOLLOWING = "following"
    PAUSED = "paused"
    STOPPED = "stopped"


DEFAULT_FOLLOW_DISTANCE_M = 1.5


class FollowSession:
    def __init__(self, follow_distance_m: float = DEFAULT_FOLLOW_DISTANCE_M) -> None:
        self.follow_distance_m = follow_distance_m
        self.mode = FollowSessionMode.IDLE

    def start(self) -> None:
        self.mode = FollowSessionMode.FOLLOWING

    def pause(self) -> None:
        if self.mode is FollowSessionMode.FOLLOWING:
            self.mode = FollowSessionMode.PAUSED

    def resume(self) -> None:
        if self.mode is FollowSessionMode.PAUSED:
            self.mode = FollowSessionMode.FOLLOWING

    def stop(self) -> None:
        """Halts target generation. Unlike pause, this needs an explicit
        start() to undo — resuming a stopped follow must be deliberate."""
        self.mode = FollowSessionMode.STOPPED

    def update(self, human: Pose, timestamp_ns: int) -> FollowState | None:
        if self.mode is not FollowSessionMode.FOLLOWING:
            return None

        target = compute_follow_target(
            human.position_m, human.orientation_xyzw, self.follow_distance_m
        )
        return FollowState(
            timestamp_ns=timestamp_ns,
            human_pose=human,
            desired_follow_distance_m=self.follow_distance_m,
            follow_target=Target(position_m=target),
            state=self.mode.value,
        )
