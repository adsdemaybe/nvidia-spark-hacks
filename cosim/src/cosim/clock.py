"""The barrier — simulation time, owned in one place.

Without this the loop free-runs: the electrical side races ahead publishing torque for
period 40 while the mechanics is still on period 12, each reading whatever happened to
be latest. The result looks like a simulation and is not one, because the two halves are
no longer talking about the same instant.

So one participant owns time. Everyone else waits to be told a period has started, does
its work, and reports done. Nobody advances until everybody has.

**A stalled participant halts the run with a named timeout.** The tempting alternative —
carry on with the previous value — is how a co-simulation produces a smooth, plausible,
entirely fictional trajectory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from cosim.bus import Publisher, Subscriber
from cosim.schema import TOPIC_CONTROL, Control


@dataclass
class PeriodStats:
    seq: int
    wall_s: float
    waits: dict[str, float] = field(default_factory=dict)


class Clock:
    """Owns simulation time and enforces the barrier.

    `dt` is the control period: how long the electrical side simulates and how long the
    mechanical side is allowed to advance before they reconcile.
    """

    def __init__(
        self,
        dt_s: float,
        publisher: Publisher,
        *,
        timeout_s: float = 10.0,
    ):
        if dt_s <= 0:
            raise ValueError("dt_s must be positive")
        self.dt_s = dt_s
        self.seq = 0
        self.t = 0.0
        self.timeout_s = timeout_s
        self._pub = publisher
        self.history: list[PeriodStats] = []

    def announce(self, cmd: str = "step", reason: str = "") -> None:
        """Tell every participant a period has started."""
        self._pub.publish(
            TOPIC_CONTROL,
            Control(t=self.t, seq=self.seq, cmd=cmd, reason=reason),  # type: ignore[arg-type]
        )

    def gather(self, waits: dict[str, Subscriber]) -> dict[str, object]:
        """Block until every named participant has published for the current period.

        `waits` maps a topic to the subscriber that should be listening for it. Returns
        the messages, keyed by topic.

        Timeouts name the participant, not just the fact of a timeout: "timed out" sends
        somebody reading three logs; "mechanics did not publish motor/state for seq 12"
        sends them to one.
        """
        started = time.monotonic()
        out: dict[str, object] = {}
        per_topic: dict[str, float] = {}
        for topic, sub in waits.items():
            t0 = time.monotonic()
            try:
                out[topic] = sub.await_seq(topic, self.seq, timeout_s=self.timeout_s)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"period {self.seq}: nothing published on {topic} within "
                    f"{self.timeout_s}s — {exc}"
                ) from None
            per_topic[topic] = time.monotonic() - t0
        self.history.append(
            PeriodStats(seq=self.seq, wall_s=time.monotonic() - started, waits=per_topic)
        )
        return out

    def advance(self) -> None:
        """Move to the next period. Only the clock does this."""
        self.seq += 1
        self.t += self.dt_s

    def stop(self, reason: str) -> None:
        self.announce(cmd="stop", reason=reason)

    # ── reporting ────────────────────────────────────────────────────────────────

    @property
    def simulated_s(self) -> float:
        return self.t

    @property
    def wall_s(self) -> float:
        return sum(p.wall_s for p in self.history)

    def summary(self) -> str:
        if not self.history:
            return "clock: no periods run"
        wall = self.wall_s
        rtf = (self.simulated_s / wall) if wall > 0 else float("inf")
        slowest = max(self.history, key=lambda p: p.wall_s)
        # Which participant the loop actually waits on is the only useful optimisation
        # target, and it is rarely the one people guess.
        by_topic: dict[str, float] = {}
        for p in self.history:
            for topic, w in p.waits.items():
                by_topic[topic] = by_topic.get(topic, 0.0) + w
        worst = sorted(by_topic.items(), key=lambda kv: -kv[1])[:3]
        return "\n".join(
            [
                f"clock: {len(self.history)} periods, {self.simulated_s:.3f} s simulated "
                f"in {wall:.2f} s wall ({rtf:.2f}x real time)",
                f"  slowest period: seq {slowest.seq} at {slowest.wall_s * 1000:.1f} ms",
                "  time spent waiting on: "
                + ", ".join(f"{t} {w:.2f}s" for t, w in worst),
            ]
        )
