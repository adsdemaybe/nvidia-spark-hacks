"""The wire — ZeroMQ pub/sub, in RAM.

A publish/subscribe bus, not an RPC layer, because a wiring harness has no
request/response: the board broadcasts what it is doing and whoever cares listens.

Everything connects to a **proxy** rather than to each other. With N participants,
direct connections need every one to know about every other, and a participant that
starts late misses whoever it did not know to connect to. One XPUB/XSUB pair in the
middle means each side knows exactly one address.

Two behaviours worth being explicit about, because both are places a co-simulation can
quietly lie:

- **Malformed messages are dropped and counted.** `Subscriber.dropped` is a real number
  that the orchestrator prints at the end. A silent drop turns a broken participant into
  a mysteriously wrong trajectory.
- **Slow joiner protection.** ZeroMQ discards messages published before a subscriber has
  finished connecting, so `wait_ready()` exists and the orchestrator calls it. Without it
  the first period is missing for whoever booted last, which looks like a physics bug.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from typing import Iterator

import zmq

from cosim.schema import Envelope, model_for

DEFAULT_XSUB = "tcp://127.0.0.1:5559"  # publishers connect here
DEFAULT_XPUB = "tcp://127.0.0.1:5560"  # subscribers connect here


class Proxy:
    """The middle of the bus. Run one per simulation.

    `tcp` on loopback rather than `ipc` so a participant can live in a container or on
    another host later without changing anything but an address.
    """

    def __init__(self, xsub_addr: str = DEFAULT_XSUB, xpub_addr: str = DEFAULT_XPUB):
        self.xsub_addr = xsub_addr
        self.xpub_addr = xpub_addr
        self._ctx = zmq.Context.instance()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> "Proxy":
        def run() -> None:
            xsub = self._ctx.socket(zmq.XSUB)
            xsub.bind(self.xsub_addr)
            xpub = self._ctx.socket(zmq.XPUB)
            xpub.bind(self.xpub_addr)
            poller = zmq.Poller()
            poller.register(xsub, zmq.POLLIN)
            poller.register(xpub, zmq.POLLIN)
            try:
                while not self._stop.is_set():
                    events = dict(poller.poll(timeout=50))
                    if xsub in events:
                        xpub.send_multipart(xsub.recv_multipart())
                    # Subscriptions travel upstream on the XPUB socket; forwarding them
                    # is what makes topic filtering work at the publisher.
                    if xpub in events:
                        xsub.send_multipart(xpub.recv_multipart())
            finally:
                xsub.close(0)
                xpub.close(0)

        self._thread = threading.Thread(target=run, name="cosim-proxy", daemon=True)
        self._thread.start()
        # The bind has to complete before anyone connects, or early messages vanish.
        time.sleep(0.15)
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def __enter__(self) -> "Proxy":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


class Publisher:
    """Sends typed messages on named topics."""

    def __init__(self, addr: str = DEFAULT_XSUB, name: str = "anon"):
        self.name = name
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.connect(addr)
        self.sent: Counter[str] = Counter()
        # Connection is asynchronous; publishing immediately loses the first messages.
        time.sleep(0.15)

    def publish(self, topic: str, message: Envelope) -> None:
        expected = model_for(topic)
        if not isinstance(message, expected):
            raise TypeError(
                f"{topic} carries {expected.__name__}, got {type(message).__name__}"
            )
        payload = message.model_dump_json().encode()
        self._sock.send_multipart([topic.encode(), payload])
        self.sent[topic] += 1

    def close(self) -> None:
        self._sock.close(0)


class Subscriber:
    """Receives and validates messages on the topics it asked for."""

    def __init__(self, topics: list[str], addr: str = DEFAULT_XPUB, name: str = "anon"):
        self.name = name
        self.topics = topics
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.SUB)
        self._sock.connect(addr)
        for t in topics:
            self._sock.setsockopt(zmq.SUBSCRIBE, t.encode())
        self.received: Counter[str] = Counter()
        self.dropped: Counter[str] = Counter()
        self.drop_reasons: list[str] = []
        time.sleep(0.15)

    def wait_ready(self, seconds: float = 0.3) -> None:
        """Give the subscription time to reach the publishers through the proxy."""
        time.sleep(seconds)

    def poll(self, timeout_ms: int = 100) -> Envelope | None:
        """One message, or None on timeout.

        A message that fails validation is counted and skipped rather than raised: one
        bad frame from one participant should not abort a rollout, but it must appear in
        the totals.
        """
        if not self._sock.poll(timeout_ms):
            return None
        topic_b, payload = self._sock.recv_multipart()
        topic = topic_b.decode()
        try:
            model = model_for(topic)
            message = model.model_validate_json(payload)
        except Exception as exc:  # noqa: BLE001 — any decode failure is a drop
            self.dropped[topic] += 1
            if len(self.drop_reasons) < 20:
                self.drop_reasons.append(f"{topic}: {exc}")
            return None
        self.received[topic] += 1
        return message

    def drain(self, timeout_ms: int = 0) -> Iterator[Envelope]:
        """Everything currently queued."""
        while (m := self.poll(timeout_ms)) is not None:
            yield m

    def await_seq(self, topic: str, seq: int, timeout_s: float = 5.0) -> Envelope:
        """Block until `topic` publishes period `seq`.

        Messages for earlier periods are discarded — a late arrival for a period the
        loop has already left is stale by definition. A message from a *later* period is
        a barrier violation and raises, because silently accepting it means somebody ran
        ahead and the physics no longer lines up.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            m = self.poll(timeout_ms=50)
            if m is None:
                continue
            if m.seq == seq:
                return m
            if m.seq > seq:
                raise RuntimeError(
                    f"{self.name}: {topic} jumped to seq {m.seq} while waiting for {seq} — "
                    "a participant advanced past the barrier"
                )
        raise TimeoutError(
            f"{self.name}: timed out after {timeout_s}s waiting for {topic} seq {seq}. "
            "The publisher is stalled, absent, or subscribed to nothing."
        )

    def close(self) -> None:
        self._sock.close(0)


def summarise(*parties: Publisher | Subscriber) -> str:
    """Counts from every participant, so a rollout can be reconciled after the fact."""
    lines = []
    for p in parties:
        if isinstance(p, Publisher):
            total = sum(p.sent.values())
            lines.append(f"  {p.name:<14} published {total:>6}  {dict(p.sent)}")
        else:
            total = sum(p.received.values())
            drops = sum(p.dropped.values())
            lines.append(
                f"  {p.name:<14} received  {total:>6}  dropped {drops}"
                + (f"  {p.drop_reasons[:3]}" if drops else "")
            )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_XPUB",
    "DEFAULT_XSUB",
    "Proxy",
    "Publisher",
    "Subscriber",
    "summarise",
    "json",
]
