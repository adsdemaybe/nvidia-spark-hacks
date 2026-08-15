"""Stream fixture TwinState over a WebSocket (STRUCT_2.md 38, 58).

    uv run python tools/mock_twin_server.py --hz 30

Serves `WS /twin/{scene_id}`, the same route the Isaac bridge will serve from
the Spark. Clients connect to this during development and to the bridge later
with no code change -- that swap is the whole point of the provider split in
STRUCT_2.md 25.

Deliberately thin: MockTwinSource is unit-tested and owns everything
interesting. This file only pumps it onto a socket.

A client rendering this stream MUST label it as fixture data, not live Isaac
state (STRUCT_2.md 82).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging

import websockets
from arxr.core.twin_mock import DEFAULT_HZ, MockTwinSource

log = logging.getLogger("struct-ar-twin")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8850


def scene_from_path(path: str) -> str | None:
    """`/twin/demo_room` -> `demo_room`. Anything else is not our route."""
    parts = [p for p in path.split("?")[0].split("/") if p]
    if len(parts) == 2 and parts[0] == "twin":
        return parts[1]
    return None


async def publish(connection, hz: float) -> None:
    scene_id = scene_from_path(connection.request.path)
    if scene_id is None:
        await connection.close(code=4004, reason="expected /twin/{scene_id}")
        return

    source = MockTwinSource(scene_id=scene_id, hz=hz)
    period = 1.0 / hz
    log.info("client attached to scene %s at %.1f Hz", scene_id, hz)

    tick = 0
    try:
        while True:
            await connection.send(source.at_tick(tick).model_dump_json())
            tick += 1
            await asyncio.sleep(period)
    except websockets.ConnectionClosed:
        log.info("client detached from scene %s after %d frames", scene_id, tick)


async def serve(host: str, port: int, hz: float) -> None:
    async with websockets.serve(lambda c: publish(c, hz), host, port):
        log.info("struct-ar-twin (mock) on ws://%s:%d/twin/{scene_id}", host, port)
        await asyncio.Future()  # run until cancelled


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hz", type=float, default=DEFAULT_HZ,
                        help=f"publish rate (default {DEFAULT_HZ}; spec suggests 20-60)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(args.host, args.port, args.hz))


if __name__ == "__main__":
    main()
